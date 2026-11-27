"""Emergency numbers for the person's country, from its country card, worked out offline.

The numbers come only from the country cards in countries/ (one <iso2>.json per country, described in
countries/schema.json; every number on a card was confirmed on an official page). A number is shown only if it is on
a card: a country without a card, or no country at all, gets "your local emergency number", never another country's
numbers.

Which country: the person's choice in Settings (settings.json "emergency_choice", a country code that has a card).
Until they choose, the first guess is the country this Mac is in, from its time zone (/etc/localtime, matched against
the system's zone.tab) and, when that gives nothing, its region setting (AppleLocale in the global preferences). On
Windows the guess is the region setting alone (GetUserDefaultGeoName).
library/refresh.py calls save_detected_country() at the end of each run, and the app calls it at start: it saves the
guess in the app's settings folder. Nothing here uses the network.
"""
import datetime
import glob
import json
import os
import plistlib
import re
import sys

REPO = os.path.dirname(os.path.realpath(__file__))
CARDS = os.path.join(REPO, "countries")
LOCALTIME = "/etc/localtime"
ZONE_TAB = "/usr/share/zoneinfo/zone.tab"
GLOBAL_PREFS = os.path.expanduser("~/Library/Preferences/.GlobalPreferences.plist")
LOCAL = "your local emergency number"
MAX_FILE = 2_000_000  # bytes read from any one file here
CODE = re.compile(r"[A-Z]{2}")


def app_dir():
    """The app's settings folder: $SURVIVAL_LLM_APP_DIR, else app-data/ in the checkout (as core.APP_DIR)."""
    return os.environ.get("SURVIVAL_LLM_APP_DIR") or os.path.join(REPO, "app-data")


def saved_path():
    return os.path.join(app_dir(), "emergency-country.json")


def _read_json(path):
    try:
        with open(path, "rb") as f:
            value = json.loads(f.read(MAX_FILE).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


# ---------- the country cards ----------

def card_codes():
    """The codes of the countries that have a card, sorted: the files countries/<iso2>.json."""
    names = (os.path.basename(p)[:-5] for p in glob.glob(os.path.join(CARDS, "*.json")))
    return sorted(n.upper() for n in names if re.fullmatch(r"[a-z]{2}", n))


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _calls_ambulance(entry):
    """Whether a card's number is the one to call for an ambulance: its label names an ambulance, and nothing says
    it is not for emergencies."""
    words = f"{entry['label']} {entry['when']}".lower()
    return "ambulance" in entry["label"].lower() and "not for emergencies" not in words and "not an emergency" not in words


def card(code):
    """A country's card as the app uses it, or None when the country has no card (or its card can't be read):
    {"code", "name", "checked", "numbers": [{"label", "short", "number", "when", "wallet"}], "ambulance": the number the
    prompt names, "ambulance_named": whether that number's label names an ambulance, "police", "sources":
    [{"title", "publisher", "url", "checked"}], "notices": [...]}. Only what the cards show: no "extra", no notes."""
    if not isinstance(code, str) or not CODE.fullmatch(code):
        return None
    raw = _read_json(os.path.join(CARDS, f"{code.lower()}.json"))
    if not raw or raw.get("code") != code or not isinstance(raw.get("call"), list):
        return None
    numbers = []
    for n in raw["call"]:
        if not isinstance(n, dict) or not re.fullmatch(r"\+?[0-9][0-9 ]{0,17}[0-9]", _text(n.get("number"))):
            continue
        label = _text(n.get("label"))
        numbers.append({"label": label, "short": _text(n.get("short")) or label, "number": _text(n.get("number")),
                        "when": _text(n.get("when")), "wallet": n.get("wallet") is True})
    if not numbers or not _text(raw.get("name")):
        return None
    urgent = [n for n in numbers if "not for emergencies" not in n["when"].lower() and "not an emergency" not in
              f"{n['label']} {n['when']}".lower()] or numbers
    ambulance = next((n for n in urgent if _calls_ambulance(n)), None)
    police = next((n["number"] for n in urgent if "police" in n["label"].lower()), None)
    sources = [{"title": _text(s.get("title")), "publisher": _text(s.get("publisher")), "url": _text(s.get("url")),
                "checked": _text(s.get("checked"))} for s in raw.get("sources") or [] if isinstance(s, dict)]
    return {"code": code, "name": _text(raw["name"]), "checked": _text(raw.get("checked")), "numbers": numbers,
            "ambulance": (ambulance or urgent[0])["number"], "ambulance_named": ambulance is not None, "police": police,
            "sources": sources, "notices": [_text(x) for x in raw.get("notices") or [] if _text(x)]}


def countries():
    """The picker's list: every country with a card, as {"code", "name"}, in name order."""
    out = []
    for code in card_codes():
        raw = _read_json(os.path.join(CARDS, f"{code.lower()}.json")) or {}
        if raw.get("code") == code and _text(raw.get("name")):
            out.append({"code": code, "name": _text(raw["name"])})
    return sorted(out, key=lambda c: c["name"])


def country_name(code):
    """A country's name: its card's, else its code (only the cards hold country names)."""
    found = card(code)
    return found["name"] if found else code


# ---------- where the Mac is, offline ----------

def _zone_countries():
    """Time zone name -> country code, from the system's zone.tab."""
    out = {}
    try:
        with open(ZONE_TAB, encoding="utf-8", errors="replace") as f:
            for line in f.read(MAX_FILE).splitlines():
                parts = line.split("\t")
                if len(parts) >= 3 and not line.startswith("#") and CODE.fullmatch(parts[0]):
                    out[parts[2].strip()] = parts[0]
    except OSError:
        pass
    return out


def zone_name():
    """The Mac's time zone ("Europe/Paris"), from where /etc/localtime points; None when it can't be read."""
    try:
        target = os.readlink(LOCALTIME)
    except OSError:
        return None
    found = re.search(r"zoneinfo/(.+)$", target)
    return found.group(1) if found else None


def _windows_region():
    """The country in Windows' region setting (Settings > Time & language > Region), from GetUserDefaultGeoName
    (Windows 10 version 1709 or newer); None when it can't be read or isn't a country (such as "001", the world)."""
    import ctypes
    buf = ctypes.create_unicode_buffer(16)
    try:
        n = ctypes.WinDLL("kernel32").GetUserDefaultGeoName(buf, len(buf))
    except (OSError, AttributeError):
        return None
    return buf.value if n and CODE.fullmatch(buf.value) else None


def region_code():
    """The country in the Mac's region setting ("en_FR" gives "FR"), or on Windows in its region setting; None when it
    can't be read."""
    if sys.platform == "win32":
        return _windows_region()
    try:
        with open(GLOBAL_PREFS, "rb") as f:
            prefs = plistlib.loads(f.read(MAX_FILE))
    except Exception:  # noqa: BLE001 - missing, unreadable or not a plist: no region
        return None
    if not isinstance(prefs, dict):
        return None
    locale = prefs.get("AppleLocale")
    found = re.search(r"[_-]([A-Z]{2})(?:[@_.-]|$)", locale) if isinstance(locale, str) else None
    if found:
        return found.group(1)
    value = prefs.get("Country")
    return value if isinstance(value, str) and CODE.fullmatch(value) else None


def detect():
    """(country code, how it was found) from the time zone, else the region setting; (None, None) when neither
    gives a country. The time zone comes first: it follows where the Mac is when it sets its zone automatically."""
    zone = zone_name()
    code = _zone_countries().get(zone) if zone else None
    if code:
        return code, f"time zone {zone}"
    code = region_code()
    if code:
        return code, "region setting"
    return None, None


def _write_private(path, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    part = f"{path}.{os.getpid()}.part"
    flags = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)  # Windows has no O_NOFOLLOW, and needs O_BINARY
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(part, path)
    finally:
        if os.path.exists(part):
            os.remove(part)


def save_detected_country():
    """Work out the country the Mac is in and save the guess in the settings folder. No arguments, no network, and
    never raises. Returns what it saved ({"code", "has_card", "detected_by", "saved"}), or None (nothing detected, or
    it couldn't be saved; the last saved guess then stays)."""
    try:
        code, how = detect()
        if not code:
            return None
        saved = {"code": code, "has_card": code in card_codes(), "detected_by": how,
                 "saved": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        _write_private(saved_path(), json.dumps(saved, ensure_ascii=False, indent=1))
        return saved
    except Exception:  # noqa: BLE001 - a failed save must never stop the refresh or the app
        return None


def saved_country():
    """What save_detected_country() saved last, or None."""
    saved = _read_json(saved_path())
    if not saved or not isinstance(saved.get("code"), str) or not CODE.fullmatch(saved["code"]):
        return None
    return saved


# ---------- what the app shows ----------

def valid_choice(value):
    """Whether a value may be saved as the choice: the code of a country with a card."""
    return isinstance(value, str) and value in card_codes()


def choice():
    """The person's pick in settings.json: a country code with a card, or None (not chosen yet)."""
    settings = _read_json(os.path.join(app_dir(), "settings.json")) or {}
    value = settings.get("emergency_choice")
    return value if valid_choice(value) else None


def view():
    """Everything the interface shows: {"choice": the person's pick or None, "detected": the time-zone guess
    {"code", "name", "detected_by", "saved", "has_card"} or None, "countries": the picker's list, "country": the
    country in use {"code", "name"} or None (the choice, else the guess), "current": that country's card or None,
    "fallback": true when there is no card to show (then the page says to use your local emergency number)}."""
    pick, saved = choice(), saved_country()
    detected = None
    if saved:
        detected = {k: saved.get(k) for k in ("code", "detected_by", "saved")}
        detected["name"] = country_name(saved["code"])
        detected["has_card"] = saved["code"] in card_codes()
    code = pick or (saved["code"] if saved else None)
    current = card(code) if code else None
    used = {"code": code, "name": current["name"] if current else country_name(code)} if code else None
    return {"choice": pick, "detected": detected, "countries": countries(), "country": used, "current": current,
            "fallback": current is None}


def _who_to_call():
    """(number, words saying whose it is) for the prompt: the card's ambulance number, else its first emergency
    number; (None, None) without a card, for "your local emergency number". Never another country's number."""
    current = view()["current"]
    if not current:
        return None, None
    kind = "the ambulance number" if current["ambulance_named"] else "the emergency number"
    return current["ambulance"], f"{kind} in {current['name']}"


def call_line():
    """Who to call, for the prompt's "say plainly when to call ...": "<number> (the ambulance number in <country>)",
    or "your local emergency number"."""
    number, whose = _who_to_call()
    return f"{number} ({whose})" if number else LOCAL


def call_number():
    """The bare number for "Call ... now." in the prompt: the card's number, or "your local emergency number"."""
    return _who_to_call()[0] or LOCAL
