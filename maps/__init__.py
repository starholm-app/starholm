"""Offline maps: Kiwix's OpenStreetMap map files (ZIM files made by maps2zim), kept whole in data.noindex/maps/.

This module is the app's side and never touches the network. A map is downloaded, checked against Kiwix's SHA-256 and
moved into place by maps/direct.py, in the app's process, when the person presses Download (the public and bundled app
have no other way), or by maps/fetch.py, run by the owner's weekly library/refresh.py, which keeps wanted maps current. The two sides talk through small files
in the maps folder:

    wanted.json          written here: the maps the person chose to keep ({"version": 1, "maps": {id: {"since"}}})
    request              written here: "download what is wanted now" (refresh.py reads it, then deletes it)
    status.json          written by maps/fetch.py: what the job is doing and the last problem per map
    <file>.zim           a map that passed its checksum, with <file>.zim.verified ({"sha256", "size", "mtime"})
    .incoming/<file>.zim a download in progress by the refresh job; never read from here
    .direct/<file>.zim.part  a download in progress from the Maps tab (maps/direct.py); never read from here

The live copy of a map is its newest verified file; the job keeps at most one older one beside it. Everything for
maps lives in this package (catalog.json, fetch.py) and in app/static/maps.js and maps.css, so maps can be switched
off or sold separately later by leaving them out.
"""
import datetime
import html
import json
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CATALOG_PATH = os.path.join(HERE, "catalog.json")
# The same data folder as the library (as library/search.py finds it): SURVIVAL_LLM_DATA moves it.
DATA_DIR = os.path.abspath(os.environ.get("SURVIVAL_LLM_DATA") or os.path.join(REPO, "data.noindex"))
MAPS_DIR = os.path.join(DATA_DIR, "maps")
WANTED_PATH = os.path.join(MAPS_DIR, "wanted.json")
REQUEST_PATH = os.path.join(MAPS_DIR, "request")
STATUS_PATH = os.path.join(MAPS_DIR, "status.json")
INCOMING_DIR = os.path.join(MAPS_DIR, ".incoming")

MAP_ID = re.compile(r"^[a-z0-9-]{1,40}$")
PREFIX = re.compile(r"^maps_[a-z]{2,3}_[a-z0-9-]{1,60}$")
# A dated Kiwix file name: <prefix>_YYYY-MM.zim, sometimes with a letter for a re-release.
DATE_SUFFIX = re.compile(r"_(\d{4}-\d{2})([a-z]?)\.zim$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Only what the map viewer needs leaves a map file: its style, TileJSON and config (JSON), vector tiles, glyphs
# (application/octet-stream .pbf), sprites and the shaded-relief tiles. Never its own HTML or scripts.
SERVED_TYPES = {"application/json": "application/json", "application/x-protobuf": "application/x-protobuf",
                "application/octet-stream": "application/octet-stream", "image/png": "image/png",
                "image/webp": "image/webp"}
ENTRY_PATH = re.compile(r"^(?:assets/kiwix-(?:light|dark)|planet|content/config\.json|tiles/\d{1,2}/\d{1,7}/\d{1,7}\.pbf"
                        r"|fonts/[A-Za-z0-9 ]{1,60}/\d{1,5}-\d{1,5}\.pbf|sprites/[a-z0-9_]{1,40}/[a-z0-9_]{1,40}(?:@2x)?\.(?:json|png)"
                        r"|natural_earth/[a-z0-9]{1,10}/\d{1,2}/\d{1,5}/\d{1,5}\.(?:webp|png))$")
MAX_ENTRY = 16 << 20
MAX_RESULTS = 12
MAX_QUERY = 100
PLACE_LINK = re.compile(r'href="(?:\.\./)*index\.html#lat=(-?\d{1,3}(?:\.\d+)?)&(?:amp;)?lon=(-?\d{1,3}(?:\.\d+)?)'
                        r'&(?:amp;)?zoom=(\d{1,2}(?:\.\d+)?)"[^>]*>([^<]{0,200})</a>')


class MapError(Exception):
    """A request about a map that can't be met: .status is the HTTP status, the message is shown to the person."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------- small files ----------

def _read_json(path, limit=1_000_000):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read(limit).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _write_json(path, data):
    """Replace path atomically (a temporary file in the same folder, then a rename), so a reader sees a whole file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- the catalog: the maps on offer ----------

def catalog():
    """The maps on offer, from catalog.json: [{"id", "name", "region", "prefix", "listed": {"file", "size",
    "sha256", "read"}}]. Entries with a bad id or prefix are left out."""
    raw = _read_json(CATALOG_PATH) or {}
    out = []
    for m in raw.get("maps") or []:
        if not isinstance(m, dict) or not MAP_ID.fullmatch(str(m.get("id", ""))) or not PREFIX.fullmatch(str(m.get("prefix", ""))):
            continue
        listed = m.get("listed") if isinstance(m.get("listed"), dict) else {}
        out.append({"id": m["id"], "name": str(m.get("name") or m["id"])[:80], "region": str(m.get("region") or "")[:80],
                    "prefix": m["prefix"], "listed": listed})
    return out


def entry(map_id):
    """The catalog entry for map_id, or MapError 404."""
    if isinstance(map_id, str) and MAP_ID.fullmatch(map_id):
        for m in catalog():
            if m["id"] == map_id:
                return m
    raise MapError(404, "That map isn't on offer.")


def file_date(name):
    """'2026-06' (plus a re-release letter) from a dated map file name, or None."""
    found = DATE_SUFFIX.search(name or "")
    return found.group(1) + found.group(2) if found else None


def is_map_file(name, prefix):
    return isinstance(name, str) and name.startswith(prefix + "_") and DATE_SUFFIX.fullmatch(name[len(prefix):]) is not None


# ---------- verified files on disk ----------

def verified_record(path):
    """The record a map file verified against ({"sha256", "size", "mtime"}), if it is there and the file has not
    changed since; else None. Reading it costs no hashing: the job hashed the file before writing the record."""
    rec = _read_json(path + ".verified", 10_000)
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not isinstance(rec, dict) or rec.get("size") != st.st_size or rec.get("mtime") != int(st.st_mtime):
        return None
    return rec if SHA256.fullmatch(str(rec.get("sha256", ""))) else None


def write_verified(path, sha256):
    st = os.stat(path)
    _write_json(path + ".verified", {"sha256": sha256, "size": st.st_size, "mtime": int(st.st_mtime)})


def local_files(prefix, folder=None):
    """The verified copies of a map in the maps folder, newest first: [(file name, record)]."""
    folder = folder or MAPS_DIR
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for name in names:
        if is_map_file(name, prefix):
            rec = verified_record(os.path.join(folder, name))
            if rec:
                found.append((name, rec))
    return sorted(found, key=lambda x: file_date(x[0]), reverse=True)


def live_file(prefix):
    """The path of a map's live copy (its newest verified file), or None."""
    files = local_files(prefix)
    return os.path.join(MAPS_DIR, files[0][0]) if files else None


# ---------- what the person chose ----------

def wanted():
    """{map id: {"since"}} from wanted.json: the maps the person asked to keep."""
    raw = _read_json(WANTED_PATH)
    maps = raw.get("maps") if isinstance(raw, dict) else None
    return {k: v for k, v in (maps or {}).items() if isinstance(k, str) and MAP_ID.fullmatch(k) and isinstance(v, dict)}


_wanted_lock = threading.Lock()


def set_wanted(map_id, keep):
    """Add map_id to wanted.json (keep True) or take it out."""
    with _wanted_lock:
        maps = wanted()
        if keep:
            maps.setdefault(map_id, {"since": utc_now()})
        else:
            maps.pop(map_id, None)
        _write_json(WANTED_PATH, {"version": 1, "maps": maps})


def request_download(map_id):
    """Mark map_id as wanted and leave the request file for the refresh job. The caller then starts the job."""
    entry(map_id)
    set_wanted(map_id, True)
    os.makedirs(MAPS_DIR, exist_ok=True)
    with open(REQUEST_PATH, "w", encoding="utf-8") as f:
        f.write(utc_now() + "\n")


def remove(map_id):
    """Take map_id out of wanted.json and delete every verified copy of it (with their records). A download of it in
    progress is not touched: the job sees it is no longer wanted and throws it away instead of moving it in.
    Returns the bytes freed."""
    m = entry(map_id)
    set_wanted(map_id, False)
    freed = 0
    _close(m["prefix"])
    try:
        names = os.listdir(MAPS_DIR)
    except OSError:
        return 0
    for name in names:
        base = name[:-len(".verified")] if name.endswith(".verified") else name
        if is_map_file(base, m["prefix"]):
            path = os.path.join(MAPS_DIR, name)
            try:
                freed += os.path.getsize(path)
                os.remove(path)
            except OSError:
                pass
    return freed


# ---------- the library lock ----------

LOCK_PATH = os.path.join(DATA_DIR, ".refresh.lock")  # the lock refresh.py and the library build take


def library_busy():
    """Whether a refresh, build or re-clean holds the library lock now. It asks for the lock without waiting and gives
    it back at once, so a run starting in that instant may find it taken: refresh.py tries again for a few seconds."""
    from library import oscompat  # flock on macOS, msvcrt.locking on Windows
    try:
        handle = open(LOCK_PATH, "a")
    except OSError:
        return False
    try:
        if not oscompat.try_lock(handle):
            return True
        oscompat.unlock(handle)
    except OSError:
        return False
    finally:
        handle.close()  # closing also gives the lock back
    return False


# ---------- the job's status ----------

def job_status():
    """status.json as written by maps/fetch.py, checked: {"running", "map", "done", "total", "finished",
    "problems": {id: text}}. A running status whose process has gone counts as not running."""
    raw = _read_json(STATUS_PATH) or {}
    out = {"running": False, "map": None, "done": None, "total": None, "finished": None, "problems": {}, "latest": {}}
    if not isinstance(raw, dict):
        return out
    pid = raw.get("pid")
    alive = False
    if raw.get("running") is True and isinstance(pid, int) and pid > 0 and sys.platform == "win32":
        from library import oscompat
        alive = not oscompat.pid_gone(pid)  # os.kill(pid, 0) would end the process on Windows
    elif raw.get("running") is True and isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except PermissionError:
            alive = True
        except OSError:
            alive = False
    out["running"] = alive
    if alive and isinstance(raw.get("map"), str) and MAP_ID.fullmatch(raw["map"]):
        out["map"] = raw["map"]
        for k in ("done", "total"):
            out[k] = raw[k] if isinstance(raw.get(k), int) and raw[k] >= 0 else None
    if isinstance(raw.get("finished"), str):
        out["finished"] = raw["finished"][:40]
    latest = raw.get("latest")
    if isinstance(latest, dict):
        for k, v in latest.items():
            if (isinstance(k, str) and MAP_ID.fullmatch(k) and isinstance(v, dict) and isinstance(v.get("size"), int)
                    and v["size"] > 0 and isinstance(v.get("file"), str) and DATE_SUFFIX.search(v["file"])):
                out["latest"][k] = {"file": v["file"], "size": v["size"], "checked": str(v.get("checked") or "")[:40]}
    problems = raw.get("problems")
    if isinstance(problems, dict):
        out["problems"] = {k: str(v)[:300] for k, v in problems.items() if isinstance(k, str) and MAP_ID.fullmatch(k)}
    return out


def list_maps(job_running=False):
    """The Maps tab's list: per map its name, region, size, build date and state:
    "available" (not downloaded), "waiting" (asked for, the job hasn't started it), "downloading" (with done and
    total bytes), "paused" (a download from the Maps tab stopped part-way, with done and total bytes; Download carries
    on from there), "saved" (a verified copy is here). job_running: whether the library refresh is running, which
    picks up waiting maps when it finishes."""
    want, job = wanted(), job_status()
    out = []
    for m in catalog():
        files = local_files(m["prefix"])
        # The size offered is the newest build the job last saw on Kiwix, else the catalog's; either way "about":
        # the job always downloads Kiwix's newest build, whose exact size it reads from that build's .meta4.
        listed = job["latest"].get(m["id"]) or m["listed"]
        item = {"id": m["id"], "name": m["name"], "region": m["region"],
                "size": listed.get("size") if isinstance(listed.get("size"), int) else None, "size_about": True,
                "date": file_date(str(listed.get("file") or "")), "state": "available", "saved": None,
                "problem": job["problems"].get(m["id"])}
        if files and m["id"] in want:
            name, rec = files[0]
            item.update(state="saved", size=rec["size"], size_about=False, date=file_date(name), saved=name,
                        used=sum(r["size"] for _, r in files))
        if m["id"] in want and job["map"] == m["id"]:
            item.update(state="downloading" if not files else "updating", done=job["done"], total=job["total"])
        elif m["id"] in want and not files:
            from maps import direct  # a paused download of the Maps tab's own (maps/direct.py)
            paused = direct.partial(m["prefix"])
            if paused:
                item.update(state="paused", done=paused[1], total=item["size"])
            else:
                item["state"] = "waiting"
                item["busy"] = bool(job_running)
        out.append(item)
    return {"maps": out, "running": job["running"]}


# ---------- reading a map file ----------

_open = {}  # path -> libzim Archive
_open_lock = threading.Lock()


def _archive(map_id):
    """The open libzim Archive of a map's live copy (opened once, reopened when a newer copy is swapped in)."""
    m = entry(map_id)
    if map_id not in wanted():
        raise MapError(404, "That map isn't downloaded.")
    path = live_file(m["prefix"])
    if not path:
        raise MapError(404, "That map isn't downloaded.")
    with _open_lock:
        arc = _open.get(path)
        if arc is None:
            from libzim.reader import Archive
            for old in [p for p in _open if os.path.basename(p).startswith(m["prefix"] + "_")]:
                _open.pop(old, None)
            try:
                arc = Archive(path)
            except RuntimeError:
                raise MapError(500, "That map file can't be read.") from None
            _open[path] = arc
        return arc


def _close(prefix):
    with _open_lock:
        for p in [p for p in _open if os.path.basename(p).startswith(prefix + "_")]:
            _open.pop(p, None)


def read_entry(map_id, path):
    """(bytes, content type) of one file inside a downloaded map, for the map viewer. Only the kinds of file the
    viewer needs (ENTRY_PATH, SERVED_TYPES); the TileJSON's tile address and the style's addresses are made
    absolute under /maps/<id>/. MapError 404 for anything else."""
    if not isinstance(path, str) or not ENTRY_PATH.fullmatch(path):
        raise MapError(404, "not found")
    arc = _archive(map_id)
    try:
        item = arc.get_entry_by_path(path).get_item()
    except KeyError:
        raise MapError(404, "not found") from None
    ctype = SERVED_TYPES.get(item.mimetype.split(";")[0].strip())
    if ctype is None or item.size > MAX_ENTRY:
        raise MapError(404, "not found")
    data = bytes(item.content)
    if path == "planet" or path.startswith("assets/"):
        data = _absolute(data, map_id)
    return data, ctype


def _absolute(data, map_id):
    """A style or TileJSON with its "./x" addresses (maps2zim writes them relative to the ZIM's root) made
    "/maps/<id>/x", so the viewer finds them wherever it runs. Any other address (another site, another map, a
    data: or blob: address) is dropped, not left for the content-security policy to stop: a glyph or sprite address,
    a tile address, a source with no address left (with the layers that draw from it)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise MapError(404, "not found") from None
    if not isinstance(obj, dict):
        raise MapError(404, "not found")
    base = f"/maps/{map_id}/"
    # The credit is HTML with links to other sites; the viewer shows its own plain-text credit instead.
    obj.pop("attribution", None)

    def fix(v):
        """The address under /maps/<id>/, or None for anything else."""
        if not isinstance(v, str):
            return None
        if v.startswith("./"):
            v = base + v[2:]
        # no "%" at all: an escaped ".." (%2e%2e) or "/" would get past the checks and resolve outside the map
        return v if v.startswith(base) and ".." not in v and "%" not in v and "\\" not in v and "//" not in v[1:] else None

    for key in ("glyphs", "sprite"):
        if key not in obj:
            continue
        if isinstance(obj[key], list):  # sprite as [{"id", "url"}]
            kept = [dict(e, url=fix(e.get("url"))) for e in obj[key] if isinstance(e, dict) and fix(e.get("url"))]
            obj[key] = kept
            if not kept:
                obj.pop(key)
        elif fix(obj[key]):
            obj[key] = fix(obj[key])
        else:
            obj.pop(key)
    if "tiles" in obj:
        obj["tiles"] = [fix(t) for t in obj["tiles"] if fix(t)] if isinstance(obj["tiles"], list) else []
    sources = obj.get("sources")
    if isinstance(sources, dict):
        for name in list(sources):
            src = sources[name]
            if not isinstance(src, dict):
                sources.pop(name)
                continue
            for key in ("url", "data"):
                if key in src and not isinstance(src[key], (dict, list)):
                    if fix(src[key]):
                        src[key] = fix(src[key])
                    else:
                        src.pop(key)
            if "tiles" in src:
                src["tiles"] = [fix(t) for t in src["tiles"] if fix(t)] if isinstance(src["tiles"], list) else []
                if not src["tiles"]:
                    src.pop("tiles")
            if not any(k in src for k in ("url", "tiles", "data")):
                sources.pop(name)
        if isinstance(obj.get("layers"), list):
            obj["layers"] = [l for l in obj["layers"] if not (isinstance(l, dict) and "source" in l and l["source"] not in sources)]
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def info(map_id):
    """A downloaded map's facts for the viewer: {"id", "name", "date", "bounds", "center", "zoom", "max_zoom"}."""
    m = entry(map_id)
    arc = _archive(map_id)
    config = {}
    try:
        config = json.loads(bytes(arc.get_entry_by_path("content/config.json").get_item().content).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        pass
    bounds = config.get("boundingBox")
    ok_bounds = (isinstance(bounds, list) and len(bounds) == 2
                 and all(isinstance(p, list) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p) for p in bounds))
    center = config.get("center")
    return {"id": map_id, "name": m["name"], "date": file_date(os.path.basename(arc.filename)),
            "bounds": bounds if ok_bounds else None,
            "center": center if isinstance(center, list) and len(center) == 2 else None,
            "zoom": config.get("zoom") if isinstance(config.get("zoom"), (int, float)) else None,
            "max_zoom": config.get("maxZoom") if isinstance(config.get("maxZoom"), (int, float)) else 14}


def search(map_id, q, limit=MAX_RESULTS):
    """Places in a downloaded map whose name matches q (the map's own title index of GeoNames places: towns,
    districts, airports, landmarks; no streets or house numbers): [{"label", "lat", "lon", "zoom"}]."""
    q = (q or "").strip()[:MAX_QUERY]
    if not q:
        return []
    arc = _archive(map_id)
    from libzim.suggestion import SuggestionSearcher
    paths = list(SuggestionSearcher(arc).suggest(q).get_results(0, limit * 3))
    out, seen = [], set()
    for p in paths:
        if not p.startswith("search/"):
            continue
        try:
            item = arc.get_entry_by_path(p).get_item()
        except KeyError:
            continue
        if item.size > 200_000:
            continue
        text = bytes(item.content).decode("utf-8", "replace")
        title = p[len("search/"):]
        links = PLACE_LINK.findall(text)
        for lat, lon, zoom, label in links:
            label = html.unescape(label).strip()
            if not label or label.lower().startswith(("click here", "open")):
                label = title
            key = (label, lat, lon)
            lat_f, lon_f = float(lat), float(lon)
            if key in seen or not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
                continue
            seen.add(key)
            out.append({"label": label[:200], "lat": lat_f, "lon": lon_f, "zoom": min(float(zoom), 18)})
        if len(out) >= limit:
            break
    return out[:limit]
