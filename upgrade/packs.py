"""The upgrade's content packs: asked for from the key service, downloaded and installed, only when the person presses
"Download now" on the Upgrade page (Settings). Nothing here runs on its own.

The steps (docs/packs.md has the pack format and the install rules):

1. Ask the key service for download links: POST <KEY_SERVICE>/v1/download-links {"key", "installId"}
   (keyservice/README.md, "Endpoints"). The answer lists the packs ("guides", "library"), each with its files
   (path, size, SHA-256, a short-lived signed URL). A fourth computer gets 403 install_limit_reached, which the page
   shows as its polite message.
2. Download every pack's files together, WORKERS at a time, the guides' first, each worker on one connection it
   keeps open; a file of SPLIT_MIN bytes or more goes in SEGMENT-sized range requests spread over the workers, so the
   biggest file never finishes last on its own. Each file with library/downloader.py into
   <data>/packs/.incoming/<pack>/: pause and resume (a partial file carries on from its last byte, across restarts;
   a split file from its last whole segment),
   the size and SHA-256 checked, and a fresh set of links asked for when the links have expired (before a file
   starts, or when the service answers 410 link_expired or 403 bad_link). A connection that fails or is reset
   mid-file (downloader.ConnectionDropped), or a 500, 502, 503 or 504 answer (RETRY_STATUSES), is tried again after
   DROP_DELAYS, from the bytes already in, while the other files keep coming; the error is shown only when every
   retry has failed too.
   Before a pack's first file, the files already in that pack's install location are checked against the list
   (on_disk(): sizes first, then the SHA-256 of the files whose size matches). Matching files are not downloaded
   again: they are hard-linked into the staging folder, or, when every file matches, the pack is marked installed
   where it is (adopt()). Nothing on disk is deleted by the check.
   Each pack's pack.json and pack.sig come first: the signature (Ed25519, PACK_PUBLIC_KEY_BASE64) must be valid over
   pack.json's exact bytes, and the files the service lists must be exactly the ones pack.json lists, before any
   other file of the pack is downloaded or adopted. The service's list then only says where each file is.
3. When every file of a pack is in, check pack.json's signature again and every file's size and SHA-256 against it,
   and install it (install()), while the other pack's files keep coming: the guides work while the library is still
   downloading. The current library stays in use until the new one is complete and checked; then
   the "current" link is swapped to it.

check_updates() is the Upgrade page's "Check for updates": it asks for the links, downloads only each pack's pack.json
and pack.sig, checks the signature and says which packs have a newer version than the one installed. "Update" is a
job started with update=True: packs whose installed version is the latest are skipped, the others download only the
files that changed (on_disk()).

After the paid library is installed, remove_free_library() deletes the free (raw) library builds the person built
earlier, when they ask for it and only them (free_library_builds()).

The only host contacted is KEY_SERVICE's, and only from these steps.
"""
import base64
import binascii
import collections
import datetime
import hashlib
import http.client
import json
import os
import queue
import re
import secrets
import shutil
import threading
import time
import urllib.parse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from library import downloader, oscompat, safe_fetch
from upgrade import key as upgrade_key
from library.downloader import DownloadError, Stopped
from library.safe_fetch import FetchError, HONEST_UA

# ---- the one setting for the address the app may contact for the upgrade ----
KEY_SERVICE = "https://keys.starholm.app"
# ----

# ---- the pack-signing public key (docs/packs.md, "Signing"); the release step swaps it ----
# The production pack key from packbuilder/make_pack_keypair.py. The tests use the TEST pack key, whose private half
# tests/upgrade_fixture.py derives in the open; ops/make_public_tree.py --check fails if the test key comes back here.
PACK_PUBLIC_KEY_BASE64 = "b/IE0VjxLGVuvSCqtb2hCa45UIRFN+HtzM9Ij1n+FvI="  # PRODUCTION pack key, made 25 November 2026
# ----

# Maps are not a pack: the Maps tab downloads each map straight from Kiwix (maps/direct.py), behind the same key.
PACK_ORDER = ("guides", "library")
PACK_NAMES = {"guides": "Guides, booklets and the emergency pack", "library": "The ready-made library"}
# What the page says before anything is asked of the service ("About 16 GB in all"): the packs' expected sizes. Built on
# 25 November 2026: guides 345,258 bytes, library 16,064,009,000; the library is rounded down so the page says 16 GB.
# What "Download now" says before anything is asked: the bytes that download (the format-2 packs, compressed; built 26
# November 2026: guides 145 kB, library 5.79 GB) and the space they take once unpacked (365 kB and 16.06 GB).
ESTIMATE_DOWNLOAD = {"guides": 150_000, "library": 5_800_000_000}
ESTIMATE_BYTES = {"guides": 370_000, "library": 16_100_000_000}  # on disk
ESTIMATE_RATE = 1_500_000  # bytes a second behind "about N hours on a fast connection"
MANIFEST = "pack.json"
SIGNATURE = "pack.sig"  # the Ed25519 signature of pack.json's exact bytes, in base64 on one line
SPECIAL = (MANIFEST, SIGNATURE)  # the two files of a pack that pack.json doesn't list
MAX_SIGNATURE = 200  # bytes
MAX_MANIFEST = 2_000_000  # bytes
MAX_LINKS_ANSWER = 5_000_000  # bytes in the service's answer
LINK_MARGIN = 60  # seconds before the links' expiry that fresh ones are asked for
LINK_TRIES = 3  # times one file may be retried with fresh links
FORMATS = (1, 2)  # pack.json formats this app reads: 1, files as they are; 2, files stored zstd-compressed in frames
ASK_FORMAT = 2  # sent to the key service: a service that has format-2 packs lists them, else (or an older one) format 1
MIN_FRAME = 4096  # bytes: the smallest frame a format-2 pack.json may name
RETRY_STATUSES = (500, 502, 503, 504)  # server answers tried again as a dropped connection is; 4xx are not
DROP_DELAYS = (1, 2, 4, 8)  # seconds before each retry of a file (or a split file's segment) whose connection dropped;
#                             one retry per entry, then the error is shown. The bytes already in are kept each time.
DEFAULT_LINK_TTL = 3600
PATH = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,119}/){0,5}[A-Za-z0-9_][A-Za-z0-9._-]{0,119}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
SHA256 = re.compile(r"[0-9a-f]{64}")
INSTALL_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
STATE_FILE = "upgrade-download.json"  # the job, in the app's folder
INSTALLED_FILE = "upgrade-packs.json"  # what is installed: {pack: {"version", "installed"}}
INSTALL_ID_FILE = "upgrade-install-id"  # a random id for this computer, the only thing the service counts
CHECK_CHUNK = 1 << 20  # bytes read at a time while hashing files already on this computer
LIB_NAME = re.compile(r"lib-\d{4}-\d{2}-\d{2}(-\d+)?")  # as build_library.LIB_NAME_RE
WORKERS = 8  # downloads at the same time, each on its own kept-open connection: live R2 stopped gaining past 6 to 8
SPLIT_MIN = 128 << 20  # a file this big is split into SEGMENT-sized range requests (the library's index.sqlite, 6.6 GB)
SEGMENT = 64 << 20


class Refused(Exception):
    """The key service said no: code is its error code (install_limit_reached, invalid_key, revoked,
    bad_install_id); extra holds the rest of its answer (limit and used for install_limit_reached)."""

    def __init__(self, code, extra=None):
        super().__init__(code)
        self.code, self.extra = code, extra or {}


class PackError(Exception):
    """A downloaded pack does not match its pack.json, or can't be installed."""


def data_dir():
    return os.path.abspath(os.environ.get("SURVIVAL_LLM_DATA") or safe_fetch.DATA_DIR)


def incoming_dir(data=None):
    return os.path.join(data or data_dir(), "packs", ".incoming")


def _read_json(path, limit=1_000_000):
    try:
        with open(path, "rb") as f:
            raw = f.read(limit + 1)
        return json.loads(raw.decode("utf-8")) if len(raw) <= limit else None
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def unsafe_path(path):
    """Why a file's path can't be used in a pack, or None when it is a plain relative path inside it (PATH: no "..",
    no leading "/", no empty or dot-led parts)."""
    if not isinstance(path, str) or not PATH.fullmatch(path) or ".." in path.split("/") or os.path.isabs(path):
        return f"a path outside the pack, or not a plain file name: {str(path)[:200]!r}"
    return None


def install_id(app_dir):
    """This computer's random install id (made once, kept in the app's folder). It says nothing about the person."""
    path = os.path.join(app_dir, INSTALL_ID_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read(100).strip()
        if INSTALL_ID.fullmatch(value):
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(24)  # 32 characters of A-Z a-z 0-9 _ -
    os.makedirs(app_dir, mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    return value


def installed(app_dir):
    """{pack: {"version", "installed"}} for the packs installed on this computer."""
    data = _read_json(os.path.join(app_dir, INSTALLED_FILE))
    return {k: v for k, v in data.items() if k in PACK_ORDER and isinstance(v, dict)} if isinstance(data, dict) else {}


def estimate():
    """{"bytes", "disk", "seconds"} for "Download now": the bytes that download, the space the packs take once
    unpacked, and the time the download takes at ESTIMATE_RATE."""
    down = sum(ESTIMATE_DOWNLOAD.values())
    return {"bytes": down, "disk": sum(ESTIMATE_BYTES.values()), "seconds": round(down / ESTIMATE_RATE)}


# ---------- the key service ----------

def _service_host(service):
    return (urllib.parse.urlsplit(service).hostname or "").lower()


def ask_links(key, inst_id, service=None):
    """POST /v1/download-links: {"expires": unix time, "packs": [{"pack", "files": [{path, size, sha256, url}]}]},
    in PACK_ORDER. Raises Refused for the service's refusals, DownloadError when it can't be reached or answers
    something else, FetchError when the address isn't allowed."""
    service = service or KEY_SERVICE
    url = service.rstrip("/") + "/v1/download-links"
    host = _service_host(service)
    safe_fetch.check_url(url, frozenset({host}))
    parts = urllib.parse.urlsplit(url)
    body = json.dumps({"key": key, "installId": inst_id, "format": ASK_FORMAT}).encode()
    conn = downloader.CONNECT(parts.hostname, parts.port)
    try:
        conn.request("POST", parts.path, body=body, headers={
            "Host": parts.hostname, "User-Agent": HONEST_UA, "Content-Type": "application/json",
            "Accept": "application/json", "Content-Length": str(len(body))})
        resp = conn.getresponse()
        raw = resp.read(MAX_LINKS_ANSWER + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise DownloadError(f"{host}: {exc}") from None
    finally:
        conn.close()
    try:
        data = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_LINKS_ANSWER else None
    except (ValueError, UnicodeDecodeError):
        data = None
    if resp.status != 200:
        code = data.get("error") if isinstance(data, dict) else None
        if resp.status in (400, 401, 403) and isinstance(code, str) and code in (
                "install_limit_reached", "invalid_key", "revoked", "bad_install_id"):
            raise Refused(code, {k: data[k] for k in ("limit", "used", "reason") if k in data})
        raise DownloadError(f"{host}: HTTP {resp.status}", resp.status)
    return parse_links(data, service)


def parse_links(data, service=None):
    """The service's answer, checked: every pack known, every path safe, every link on the service's own host."""
    service = service or KEY_SERVICE
    host = _service_host(service)
    if not isinstance(data, dict) or not isinstance(data.get("packs"), list):
        raise DownloadError("the key service's answer isn't what was expected")
    packs = {}
    for p in data["packs"]:
        name = p.get("pack") if isinstance(p, dict) else None
        if name not in PACK_ORDER or name in packs or not isinstance(p.get("files"), list) or not p["files"]:
            raise DownloadError("the key service listed a pack that isn't known")
        files, seen = [], set()
        for f in p["files"]:
            if not isinstance(f, dict):
                raise DownloadError("the key service listed a file that isn't valid")
            path, size, sha, link = f.get("path"), f.get("size"), str(f.get("sha256") or "").lower(), f.get("url")
            if unsafe_path(path):
                raise DownloadError(f"the key service listed {unsafe_path(path)}")
            if (path in seen or not isinstance(size, int) or isinstance(size, bool) or size < 0
                    or not SHA256.fullmatch(sha) or not isinstance(link, str)):
                raise DownloadError("the key service listed a file that isn't valid")
            safe_fetch.check_url(link, frozenset({host}))
            seen.add(path)
            files.append({"path": path, "size": size, "sha256": sha, "url": link})
        for need in SPECIAL:
            if need not in seen:
                raise DownloadError(f"the {name} pack has no {need}")
        packs[name] = files
    expires = time.time() + DEFAULT_LINK_TTL
    if isinstance(data.get("expiresAt"), str):
        try:
            expires = datetime.datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return {"expires": expires, "packs": [{"pack": n, "files": packs[n]} for n in PACK_ORDER if n in packs],
            "installs": data.get("installs") if isinstance(data.get("installs"), dict) else None}


# ---------- installing a pack ----------

def signature_ok(manifest_raw, signature_raw, public_key_base64=None):
    """Whether signature_raw (pack.sig: base64 of 64 bytes, one line) is the pack key's Ed25519 signature of
    manifest_raw, pack.json's exact bytes."""
    try:
        sig = base64.b64decode(signature_raw.strip(), validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_base64 or PACK_PUBLIC_KEY_BASE64))
        if len(sig) != 64:
            return False
        key.verify(sig, manifest_raw)
        return True
    except (InvalidSignature, ValueError, TypeError, binascii.Error):
        return False


def check_manifest(manifest_raw, signature_raw, name, files):
    """pack.json's bytes, checked: signed with the pack key (pack.sig), then parsed from those same bytes, naming
    this pack with a valid version, and listing exactly the files the service listed (path, size, SHA-256 each;
    pack.json and pack.sig left out), with their sum as its size. Returns pack.json as a dict; raises PackError."""
    if len(manifest_raw) > MAX_MANIFEST or len(signature_raw) > MAX_SIGNATURE:
        raise PackError(f"the {name} pack's {MANIFEST} isn't valid")
    if not signature_ok(manifest_raw, signature_raw):
        raise PackError(f"the {name} pack's signature doesn't match its {MANIFEST}: it wasn't made by Starholm, or "
                        "it was changed after it was signed")
    try:
        m = json.loads(manifest_raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        m = None
    if not isinstance(m, dict) or m.get("format") not in FORMATS or isinstance(m.get("format"), bool) \
            or m.get("name") != name:
        raise PackError(f"the {name} pack's {MANIFEST} isn't valid")
    if not isinstance(m.get("version"), str) or not VERSION.fullmatch(m["version"]):
        raise PackError(f"the {name} pack's version isn't valid")
    listed = m.get("files")
    if not isinstance(listed, list):
        raise PackError(f"the {name} pack's {MANIFEST} lists no files")
    for f in listed:
        bad = unsafe_path(f.get("path") if isinstance(f, dict) else None)
        if bad or f.get("path") in SPECIAL:
            raise PackError(f"the {name} pack's {MANIFEST} names {bad or f.get('path')}")
    if m["format"] == 2:
        _check_stored(m, name)
    want = {(f.get("path"), f.get("size"), str(f.get("sha256") or "").lower()) for f in listed if isinstance(f, dict)}
    # The files to check against: the service's list (for format 2, each file as stored: its compressed size and
    # SHA-256), or the job's own list once pack.json was read (unpacked sizes; stored_files()).
    stored = {(f.get("path"), _stored(f)["size"], _stored(f)["sha256"]) for f in listed if isinstance(f, dict)}
    got = {(f["path"], f["size"], f["sha256"]) for f in files if f["path"] not in SPECIAL}
    if len(want) != len(listed) or got not in (want, stored):
        raise PackError(f"the {name} pack's files don't match its {MANIFEST}")
    if any(not isinstance(f.get("size"), int) or isinstance(f.get("size"), bool) or f["size"] < 0 for f in listed) \
            or m.get("size") != sum(f["size"] for f in listed):
        raise PackError(f"the {name} pack's size doesn't match its {MANIFEST}")
    return m


def _stored(f):
    """How a file of pack.json is stored on the service: {"size", "sha256"}, and "frames" when it is compressed."""
    st = f.get("stored") if isinstance(f, dict) else None
    if isinstance(st, dict):
        return {"size": st.get("size"), "sha256": str(st.get("sha256") or "").lower(), "frames": st.get("frames")}
    return {"size": f.get("size") if isinstance(f, dict) else None,
            "sha256": str((f.get("sha256") if isinstance(f, dict) else "") or "").lower()}


def _check_stored(m, name):
    """A format-2 pack.json's frame size and every compressed file's frames: frames that tile the file exactly
    (each unpacks to "frame" bytes, the last to the rest), whose compressed lengths add up to the stored size, and a
    frame size that divides SEGMENT, so a split file's segments start on a frame."""
    frame = m.get("frame")
    bad = PackError(f"the {name} pack's {MANIFEST} isn't valid")
    if not isinstance(frame, int) or isinstance(frame, bool) or frame < MIN_FRAME or SEGMENT % frame:
        raise bad
    for f in m["files"]:
        st = f.get("stored")
        if st is None:
            continue
        size, frames = f.get("size"), st.get("frames") if isinstance(st, dict) else None
        if (not isinstance(size, int) or size <= 0 or not isinstance(st.get("size"), int)
                or not SHA256.fullmatch(str(st.get("sha256") or "")) or not isinstance(frames, list)
                or len(frames) != -(-size // frame)
                or any(not isinstance(z, int) or isinstance(z, bool) or not 0 < z <= frame + frame // 64 + 4096
                       for z in frames)
                or sum(frames) != st["size"]):
            raise bad


def transient(exc):
    """Whether a download error is worth trying again after DROP_DELAYS: a dropped connection, or a server error
    (RETRY_STATUSES). A wrong file, a refused link or any 4xx is not."""
    return isinstance(exc, downloader.ConnectionDropped) or (
        type(exc) is DownloadError and exc.status in RETRY_STATUSES)


def stored_files(m, files):
    """The job's list of a pack's files once its pack.json m is checked: for format 2, each file as it is unpacked
    (pack.json's size and SHA-256, which every later check uses), with "stored" ({"size", "sha256", "frames"}) for
    a compressed one and "frame"; pack.json and pack.sig as the service listed them. Format 1: files as they are."""
    if m.get("format") != 2:
        return files
    special = [f for f in files if f["path"] in SPECIAL]
    out = []
    for f in m["files"]:
        entry = {"path": f["path"], "size": f["size"], "sha256": f["sha256"].lower()}
        if f.get("stored") is not None:
            entry["stored"] = _stored(f)
            entry["frame"] = m["frame"]
        out.append(entry)
    return special + sorted(out, key=lambda f: f["path"])


def listing(f):
    """A file of the job's list as the service lists it: (path, stored size, stored SHA-256)."""
    st = f.get("stored") or f
    return f["path"], st["size"], st["sha256"]


def _read_raw(path, limit):
    try:
        with open(path, "rb") as f:
            return f.read(limit + 1)
    except OSError:
        return b""


def read_manifest(staging, name, files, where=None, present=True, hashes=False, stop=None):
    """pack.json and pack.sig from a pack's folder, checked by check_manifest() against the files the service
    listed. where: the folder holding the files, when it isn't the folder holding pack.json (a pack found already on
    disk). present: every file must be there with its size. hashes: and its SHA-256 must match pack.json (read in
    full; stop() is asked between chunks)."""
    m = check_manifest(_read_raw(os.path.join(staging, MANIFEST), MAX_MANIFEST),
                       _read_raw(os.path.join(staging, SIGNATURE), MAX_SIGNATURE), name, files)
    if not present:
        return m
    for f in m["files"]:
        full = os.path.join(where or staging, f["path"])
        if not os.path.isfile(full) or os.path.islink(full) or os.path.getsize(full) != f["size"]:
            raise PackError(f"{f['path']} is missing from the {name} pack")
        if hashes and _hash_file(full, stop) != f["sha256"].lower():
            raise PackError(f"{f['path']} in the {name} pack doesn't match its {MANIFEST}: it was changed")
    return m


def _free_name(folder, base):
    name, n = base, 1
    while os.path.lexists(os.path.join(folder, name)):
        n += 1
        name = f"{base}-{n}"
    return name


def _swap_link(link, target):
    """Point the symlink `link` at target atomically (a temporary link renamed over it); the link is relative.
    On Windows a directory junction instead, which needs no admin rights (library/oscompat.py)."""
    oscompat.swap_link(target, link)


def guides_dir(data=None):
    """The installed guides pack's folder (the target of <data>/packs/guides), or None."""
    link = os.path.join(data or data_dir(), "packs", "guides")
    target = os.path.realpath(link) if oscompat.is_link(link) else None
    return target if target and os.path.isfile(os.path.join(target, "index.json")) else None


def install(name, staging, files, data=None, stop=None):
    """Install a downloaded pack (docs/packs.md, "Installing") once its signature and every file's SHA-256 check out.
    Returns {"version", "installed", "where"}."""
    data = data or data_dir()
    m = read_manifest(staging, name, files, hashes=True, stop=stop)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if name == "guides":
        if not os.path.isfile(os.path.join(staging, "index.json")):
            raise PackError("the guides pack has no index.json")
        root = os.path.join(data, "packs")
        target = os.path.join(root, _free_name(root, f"guides-{m['version']}"))
        os.rename(staging, target)
        previous = guides_dir(data)
        _swap_link(os.path.join(root, "guides"), target)
        for old in os.listdir(root):  # keep the new guides and the ones before them
            full = os.path.join(root, old)
            if (old.startswith("guides-") and os.path.realpath(full) not in (os.path.realpath(target), previous)
                    and os.path.isdir(full) and not os.path.islink(full)):
                shutil.rmtree(full, ignore_errors=True)
        where = target
    elif name == "library":
        from library import build_library as bl
        for need in ("index.sqlite", bl.BUILD_MANIFEST, bl.CHECKS_PASSED):
            if not os.path.isfile(os.path.join(staging, need)):
                raise PackError(f"the library pack has no {need}")
        libs = os.path.join(data, "libs")
        os.makedirs(libs, exist_ok=True)
        day = m["version"][:10] if DATE.fullmatch(m["version"][:10]) else stamp[:10]
        target = os.path.join(libs, _free_name(libs, f"lib-{day}"))
        os.rename(staging, target)  # the same disk: a rename, not a copy
        bl.swap_current(target, os.path.join(data, "current"))  # the old build stays until a refresh tidies it
        where = target
    else:
        raise PackError(f"unknown pack {name}")
    return {"version": m["version"], "installed": stamp, "where": where}


# ---------- files already on this computer ----------

def _inside(folder, path):
    """path, resolved, is a file inside folder (resolved): no link inside the pack's folder leads out of it."""
    real, top = os.path.realpath(path), os.path.realpath(folder)
    return real.startswith(top + os.sep) and os.path.isfile(real) and not os.path.islink(path)


def _hash_file(path, stop=None, progress=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if stop and stop():
                raise Stopped()
            chunk = f.read(CHECK_CHUNK)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)
            if progress:
                progress(len(chunk))


def pack_places(name, data=None):
    """The folders where a pack installs, which may already hold its files: for guides the installed guides folder and
    every <data>/packs/guides-* folder; for the library the live build (when it is in
    <data>/libs) and every <data>/libs/lib-* build. Nothing else on the computer is looked at."""
    data = data or data_dir()
    found = []

    def folders(root, test):
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []
        return [os.path.join(root, n) for n in names
                if test(n) and os.path.isdir(os.path.join(root, n)) and not os.path.islink(os.path.join(root, n))]
    if name == "guides":
        found = [guides_dir(data)] + folders(os.path.join(data, "packs"), lambda n: n.startswith("guides-"))
    elif name == "library":
        libs = os.path.join(data, "libs")
        live = os.path.realpath(os.path.join(data, "current"))
        found = ([live] if os.path.dirname(live) == os.path.realpath(libs) else []) + folders(libs, LIB_NAME.fullmatch)
    out = []
    for folder in found:
        real = os.path.realpath(folder) if folder else None
        if real and os.path.isdir(real) and real not in out:
            out.append(real)
    return out


def on_disk(name, files, data=None, stop=None, progress=None):
    """Which of a pack's files (the service's list: path, size, SHA-256) are already in one of its install folders.
    Sizes are compared first, in every folder; then only the folder with the most bytes of same-size files is read,
    and only its same-size files are hashed. progress(done, total) counts the bytes hashed. Reads only; never writes
    or deletes. Returns {"where": folder or None, "match": [paths whose size and SHA-256 match]}."""
    best, best_bytes = None, 0
    for folder in pack_places(name, data):
        same = [f for f in files if _inside(folder, os.path.join(folder, f["path"]))
                and os.path.getsize(os.path.join(folder, f["path"])) == f["size"]]
        size = sum(f["size"] for f in same) + len(same)  # a file of 0 bytes still counts
        if same and size > best_bytes:
            best, best_bytes = (folder, same), size
    if not best:
        return {"where": None, "match": []}
    folder, same = best
    total, done = sum(f["size"] for f in same), [0]
    if progress:
        progress(0, total)

    def step(n):
        done[0] += n
        if progress:
            progress(done[0], total)
    match = [f["path"] for f in same if _hash_file(os.path.join(folder, f["path"]), stop, step) == f["sha256"]]
    return {"where": folder, "match": match}


def adopt(name, where, manifest_dir, files, data=None):
    """Mark a pack installed where it already is, every file of it found in `where` with its size and SHA-256
    (on_disk(), which hashed them against the signed list). pack.json and pack.sig are read from manifest_dir and
    their signature checked. guides: the guides link points at `where`; library: "current"
    points at it (a running app offers Restart, as after an install). Nothing is moved or deleted. Returns {"version", "installed", "where"}, as install()."""
    data = os.path.realpath(data or data_dir())  # `where` is resolved too, so the relative links come out right
    m = read_manifest(manifest_dir, name, files, where=where)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if name == "guides":
        if not os.path.isfile(os.path.join(where, "index.json")):
            raise PackError("the guides pack has no index.json")
        if guides_dir(data) != os.path.realpath(where):
            os.makedirs(os.path.join(data, "packs"), exist_ok=True)
            _swap_link(os.path.join(data, "packs", "guides"), where)
    elif name == "library":
        from library import build_library as bl
        for need in ("index.sqlite", bl.BUILD_MANIFEST, bl.CHECKS_PASSED):
            if not os.path.isfile(os.path.join(where, need)):
                raise PackError(f"the library pack has no {need}")
        if os.path.realpath(os.path.join(data, "current")) != os.path.realpath(where):
            bl.swap_current(where, os.path.join(data, "current"))
    else:
        raise PackError(f"unknown pack {name}")
    if os.path.realpath(manifest_dir) != os.path.realpath(where):
        for special in SPECIAL:  # found next time
            shutil.copyfile(os.path.join(manifest_dir, special), os.path.join(where, special))
    return {"version": m["version"], "installed": stamp, "where": where}


def _link_in(src, dest):
    """Put the file src at dest in the staging folder: a hard link (no copy, no extra space), or a copy when the
    disk won't link. A partial download of dest is replaced."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for old in (dest, dest + ".part", dest + ".part.json", dest + ".segments.json"):
        if os.path.lexists(old):
            os.remove(old)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


# ---------- removing the free library once the paid one is in ----------

def _folder_bytes(folder):
    total = 0
    for top, _, names in os.walk(folder):
        for n in names:
            try:
                total += os.lstat(os.path.join(top, n)).st_size
            except OSError:
                pass
    return total


def free_library_builds(app_dir, data=None, in_use=()):
    """The free (raw) library builds "Remove the free library" would delete: [{"name", "path", "bytes"}], or None when
    the button isn't offered (the paid library isn't installed, or the live build is itself a free build). Only lib-*
    folders in <data>/libs whose build.json says "raw": true; never the live build, a build this app or another
    running app still reads (in_use, build_library.readers), the paid builds, the person's own documents (kept in
    another folder) or anything outside <data>/libs."""
    from library import build_library as bl
    from library.free_library import built_raw
    data = data or data_dir()
    if "library" not in installed(app_dir):
        return None
    libs = os.path.join(data, "libs")
    live = os.path.realpath(os.path.join(data, "current"))
    if not os.path.isdir(live) or built_raw(live):
        return None
    keep = {os.path.realpath(p) for p in in_use if p} | {live}
    out = []
    try:
        names = sorted(os.listdir(libs))
    except OSError:
        return []
    for n in names:
        path = os.path.join(libs, n)
        if (not LIB_NAME.fullmatch(n) or os.path.islink(path) or not os.path.isdir(path)
                or os.path.realpath(path) in keep or not built_raw(path) or bl.readers(path)):
            continue
        out.append({"name": n, "path": path, "bytes": _folder_bytes(path)})
    return out


def remove_free_library(app_dir, data=None, in_use=()):
    """Delete the builds free_library_builds() lists, checked again now, while holding the data folder's build lock
    (safe_fetch.take_lock, the one refresh.py, build_library.py and the free library's build take), so no build can
    finish and swap in a new folder between the listing and the deletion. Raises PackError when a build or refresh
    holds the lock, or when the button isn't offered (the free build is live, or the paid library isn't installed).
    Returns the names removed."""
    data = data or data_dir()
    lock = safe_fetch.take_lock(os.path.join(data, ".refresh.lock"))
    if lock is None:
        raise PackError("A library build or refresh is running. Try again when it has finished.")
    try:
        builds = free_library_builds(app_dir, data, in_use)
        if builds is None:
            raise PackError("The free library is still the one in use, so it can't be removed.")
        for b in builds:
            shutil.rmtree(b["path"])
        return [b["name"] for b in builds]
    finally:
        lock.close()


# ---------- the time left ----------

def _stored_size(f):
    """The bytes a file of the job's list downloads: its stored size when it is compressed (format 2)."""
    return (f.get("stored") or f)["size"]


def _file_got(staging, p, f, inflight):
    """The bytes of one file of a pack the progress bar counts as in. Never more than a moment earlier, so the bar
    doesn't jump back: a file moved into place counts whole before it joins the have list, and a split file's empty
    full-size .part (made before its segments record) counts nothing."""
    if f["path"] in (p.get("have") or []):
        return f["size"]
    dest = os.path.join(staging, f["path"])
    got = 0
    if f["size"] >= SPLIT_MIN:
        seg = _read_segments(dest, f)
        if seg is not None:  # its whole segments, and the bytes of those coming now
            got = sum(_segment_range(f["size"], i)[1] - _segment_range(f["size"], i)[0] + 1 for i in seg["done"])
            got += sum(n for (pk, path, _), n in inflight.items() if pk == p["pack"] and path == f["path"])
        else:  # no record: the empty full-size .part _prepare_segments() makes counts nothing; a 1.0.1 partial counts
            side = _read_json(dest + ".part.json") or {}
            if side.get("size") == f["size"] and side.get("sha256") == f["sha256"]:
                got = downloader.partial_size(dest)
    else:
        got = downloader.partial_size(dest)
    # Looked at last: a file moves from .part (and its record) into place, so whatever the reads above missed is
    # there now. A file in place counts whole before it joins the have list.
    try:
        if got < f["size"] and os.path.getsize(dest) == f["size"]:
            return f["size"]
    except OSError:
        pass
    return min(got, f["size"])


class Speed:
    """The download speed behind "about N left": bytes counted over the last WINDOW seconds, from the first byte of
    content that arrives (asking for links and checking files on disk don't count), and smoothed so the figure
    doesn't jump. rate() is None until MIN_SECONDS of downloading and MIN_BYTES have been seen, so a tiny first pack
    (the guides) or a slow start can't stand in for the whole download."""
    WINDOW = 75.0       # seconds of recent progress the speed is measured over (a slow patch of 20 s barely moves it)
    MIN_SECONDS = 15.0  # of downloading before any figure is given
    MIN_BYTES = 8_000_000
    SMOOTHING = 0.03    # weight of each new measurement, at most two a second (about a 15-second time constant)

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.samples = collections.deque()  # (time, total bytes so far)
        self.total = 0
        self.first = None
        self.smooth = None
        self.last_look = None

    def add(self, n):
        if n <= 0:
            return
        with self.lock:
            now = self.clock()
            if self.first is None:
                self.first = now
                self.samples.append((now, 0))
            self.total += n
            self.samples.append((now, self.total))
            while len(self.samples) > 2 and now - self.samples[1][0] > self.WINDOW:
                self.samples.popleft()

    def rate(self):
        """Bytes a second, or None while there isn't enough to go on."""
        with self.lock:
            if self.first is None or len(self.samples) < 2:
                return None
            now = self.clock()
            if now - self.first < self.MIN_SECONDS or self.total < self.MIN_BYTES:
                return None
            start_t, start_b = self.samples[0]
            span = now - start_t  # up to now: a stall lowers the speed instead of freezing it
            if span <= 0:
                return None
            measured = (self.total - start_b) / span
            if self.smooth is None:
                self.smooth = measured
            elif self.last_look is None or now - self.last_look >= 0.5:
                self.smooth += self.SMOOTHING * (measured - self.smooth)
            self.last_look = now
            return self.smooth if self.smooth > 0 else None


def seconds_left(bytes_left, rate):
    """The time left for bytes_left at rate, or None when either is unknown or nothing is left."""
    if not rate or rate <= 0 or bytes_left is None or bytes_left <= 0:
        return None
    return round(bytes_left / rate)


# ---------- the job ----------

class Job:
    """One download of the packs, run in a thread of the app's server. State is kept in STATE_FILE so a pause, a
    quit or a crash leaves a download that "Resume" continues; nothing starts again by itself."""

    def __init__(self, app_dir, service=None, on_installed=None):
        self.app_dir = app_dir
        self.service = service or KEY_SERVICE
        self.on_installed = on_installed  # called with (pack name, install record) after each pack is installed
        self.log = None  # called with one line of text when a download starts and when it ends (the app's log)
        self.lock = threading.Lock()
        self.thread = None
        self.stopping = threading.Event()
        self.links = None  # {"expires", "urls": {(pack, path): url}, "gen"}; in memory only
        self.links_gen = 0  # bumped by every fresh set of links
        self.ask_lock = threading.Lock()  # one request for fresh links at a time, whatever the workers
        self.workers = WORKERS
        self.speed = None  # Speed() while the job runs
        self.inflight = {}  # (pack, path, segment) -> bytes of that segment written in this run, for the progress bar
        self.inflight_lock = threading.Lock()
        self.installing = set()  # packs being checked and put in place now
        self.install_lock = threading.Lock()
        self.checking = None  # {"pack", "done", "bytes"} while files already on this computer are hashed
        self.state = self._load()

    # -- state --
    def _path(self):
        return os.path.join(self.app_dir, STATE_FILE)

    def _load(self):
        s = _read_json(self._path())
        if not isinstance(s, dict) or s.get("phase") not in ("asking", "checking", "downloading", "installing", "paused",
                                                             "error", "refused", "done"):
            return None
        if s["phase"] in ("asking", "checking", "downloading", "installing"):
            s["phase"] = "paused"  # the app stopped while it ran: it waits for Resume
        return s

    def _save(self):
        if self.state is not None:
            _write_json(self._path(), self.state)

    def _set(self, **changes):
        with self.lock:
            self.state.update(changes)
            self._save()

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def snapshot(self):
        """The job for the page: {"phase", "packs": [{"pack", "name", "bytes", "done", "state"}], "bytes", "done",
        "download_bytes", "download_done", "seconds_left", "error", "code", "limit", "used"}, or None when nothing
        was ever started. bytes and done count the files as installed; download_bytes and download_done what comes
        over the network (less, for compressed packs: each file's stored bytes, in the share of it unpacked)."""
        with self.lock:  # the state, the in-flight counts and the segment records as one moment (_segment_done)
            s = json.loads(json.dumps(self.state)) if self.state else None
            if not s:
                return None
            with self.inflight_lock:
                inflight = dict(self.inflight)
            gots, downs = {}, {}
            for p in s.get("packs") or []:
                staging = os.path.join(incoming_dir(), p["pack"])
                got = [f["size"] if p.get("installed") else _file_got(staging, p, f, inflight) for f in p["files"]]
                gots[p["pack"]] = sum(got)
                # what came over the network: a compressed file's stored bytes in the share of it unpacked so far
                downs[p["pack"]] = (sum(_stored_size(f) for f in p["files"]),
                                    sum(_stored_size(f) * g // f["size"] if f["size"] else 0
                                        for f, g in zip(p["files"], got)))
        packs, total, done = [], 0, 0
        down_total = sum(t for t, _ in downs.values())
        down_done = sum(d for _, d in downs.values())
        for p in s.get("packs") or []:
            size = sum(f["size"] for f in p["files"])
            got = gots[p["pack"]]
            if p.get("installed"):
                state = "done"
            elif p["pack"] in self.installing:
                state = "installing"
            else:
                state = {"downloading": "downloading", "asking": "downloading", "installing": "downloading",
                         "checking": "checking"}.get(s["phase"], "paused" if s["phase"] == "paused" else "stopped")
            entry = {"pack": p["pack"], "name": PACK_NAMES[p["pack"]], "bytes": size, "done": got,
                     "state": state, "waiting_on": None, "current": bool(p.get("current"))}
            check = self.checking
            if state == "checking" and check and check.get("pack") == p["pack"]:
                entry["checked"], entry["to_check"] = check["done"], check["bytes"]
            packs.append(entry)
            total += size
            done += got
        left = None
        speed = self.speed
        if s["phase"] == "downloading" and speed is not None:
            left = seconds_left(total - done, speed.rate())  # every pack's bytes still to come, at the recent speed
        return {"phase": s["phase"], "update": bool(s.get("update")), "packs": packs, "bytes": total, "done": done,
                "download_bytes": down_total, "download_done": down_done, "seconds_left": left,
                "error": s.get("error"), "code": s.get("code"), "limit": s.get("limit"), "used": s.get("used")}

    # -- actions --
    def start(self, key, again=False, update=False):
        """Download now (or Download again): a new job, unless one is paused or stopped, which Resume continues.
        update: the Upgrade page's "Update" after "Check for updates": a new job in which a pack whose installed
        version is the one the service has is skipped, and the others download only the files that changed."""
        with self.lock:
            if self.running():
                return
            if again or update or not self.state or self.state.get("phase") in ("done", "refused"):
                self.state = {"phase": "asking", "packs": None, "started": time.time(), "update": bool(update)}
                self.links = None
            else:
                self.state["phase"] = "asking"
            self.state.update(error=None, code=None, limit=None, used=None)
            self._save()
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, args=(key,), daemon=True, name="upgrade-packs")
            self.thread.start()

    def pause(self):
        self.stopping.set()

    def resume(self, key):
        self.start(key)

    def wait(self, seconds=None):
        thread = self.thread
        if thread:
            thread.join(seconds)

    def forget(self):
        """Drop the job's record and its partial files (the key was removed)."""
        self.pause()
        self.wait(30)
        with self.lock:
            self.state = None
            self.links = None
            try:
                os.remove(self._path())
            except FileNotFoundError:
                pass
        shutil.rmtree(incoming_dir(), ignore_errors=True)

    # -- the thread --
    def _ask(self, key):
        try:
            answer = ask_links(key, install_id(self.app_dir), self.service)
        except Refused as exc:
            self._note(key, exc.code)  # "revoked" locks the paid features here
            raise
        self._note(key, None)
        urls = {}
        with self.lock:
            old = {p["pack"]: p for p in self.state.get("packs") or []}
            packs = []
            for p in answer["packs"]:
                o = old.get(p["pack"])
                files = [{k: f[k] for k in ("path", "size", "sha256")} for f in p["files"]]
                if o and (o.get("installed") or sorted(map(listing, o["files"])) == sorted(map(listing, files))):
                    packs.append(o)  # installed in this job already, or the same files: kept as it is
                else:  # new, or the service has a newer pack: files already here that are unchanged count
                    have = [h for h in (o or {}).get("have") or [] if any(f["path"] == h and o_same(o, f) for f in files)]
                    packs.append({"pack": p["pack"], "files": files, "have": have, "installed": False})
                for f in p["files"]:
                    urls[(p["pack"], f["path"])] = f["url"]
            self.state["packs"] = packs
            self._save()
            self.links_gen += 1
            self.links = {"expires": answer["expires"], "urls": urls, "gen": self.links_gen}

    def _note(self, key, code):
        try:
            upgrade_key.note_service_answer(self.app_dir, key, code)
        except OSError:
            pass  # a full disk or a read-only folder must not hide the service's answer or fail a working download

    def _pack(self, name):
        with self.lock:
            return next((p for p in self.state.get("packs") or [] if p["pack"] == name), None)

    def _links_fresh(self):
        return bool(self.links and self.links["expires"] - LINK_MARGIN > time.time())

    def _say(self, text):
        if self.log:
            try:
                self.log(text)
            except Exception:  # noqa: BLE001 - a log that can't be written must not stop a download
                pass

    def _run(self, key):
        self.speed = Speed()
        self._say("The packs' download started.")
        try:
            self._ask(key)
            self._set(phase="downloading")
            names = []
            for name in PACK_ORDER:
                p = self._pack(name)
                if not p or p.get("installed"):
                    continue
                staging = os.path.join(incoming_dir(), name)
                m, p = self._signed(key, p, staging)
                mine = installed(self.app_dir).get(name, {}).get("version")
                if isinstance(mine, str) and m["version"] < mine:  # Update, Download again or Resume alike
                    shutil.rmtree(staging, ignore_errors=True)
                    raise PackError(f"the download server offers an older {name} pack ({m['version']}) than the one "
                                    f"installed here ({mine}), so it was not installed")
                if self.state.get("update") and mine == m["version"]:
                    with self.lock:  # Update: this pack is already the latest version here
                        p["installed"] = p["current"] = True
                        self._save()
                    shutil.rmtree(staging, ignore_errors=True)
                    continue
                if not p.get("checked") and self._check_disk(key, p, staging):
                    continue  # every file was already here: installed where it is
                names.append(name)
            self._set(phase="downloading")
            self._download_files(key, names)  # every pack at once; each is installed as soon as it is complete
            self._set(phase="done", finished=time.time())
        except Stopped:
            self._set(phase="paused")
        except Refused as exc:
            self._set(phase="refused" if exc.code == "install_limit_reached" else "error", code=exc.code,
                      limit=exc.extra.get("limit"), used=exc.extra.get("used"),
                      error=refusal_text(exc.code, exc.extra.get("limit")))
        except (DownloadError, FetchError, PackError, OSError) as exc:
            self._set(phase="error", error=str(exc))
        finally:
            self.speed = None
            self.checking = None
            with self.inflight_lock:
                self.inflight = {}
            self.installing = set()
            state = self.state or {}
            self._say(f"The packs' download ended: {state.get('phase')}"
                      + (f" ({state.get('error')})" if state.get("error") else "") + ".")

    def _installed(self, name, p, record):
        with self.lock:
            p["installed"] = True
            done = installed(self.app_dir)
            done[name] = {"version": record["version"], "installed": record["installed"]}
            _write_json(os.path.join(self.app_dir, INSTALLED_FILE), done)
            self.state["phase"] = "downloading"
            self._save()
        if self.on_installed:
            self.on_installed(name, record)

    def _signed(self, key, p, staging):
        """Before anything else of a pack: download its pack.json and pack.sig into the staging folder and check them
        (check_manifest(): the pack key's signature, and the service's list equal to pack.json's). Returns (pack.json,
        the job's pack): fresh links may have brought a newer file list, which is then the one checked."""
        name = p["pack"]
        while True:
            for special in SPECIAL:
                if special in p["have"]:
                    continue
                f = next(f for f in p["files"] if f["path"] == special)
                if not self._fetch(key, p, f, staging):
                    p = self._pack(name)
                    if p is None:
                        raise DownloadError(f"the key service no longer lists the {name} pack")
                    break
                with self.lock:
                    p["have"].append(special)
                    self._save()
            else:
                break
        try:
            m = read_manifest(staging, name, p["files"], present=False)
            if m.get("format") == 2:
                with self.lock:  # from here on the job works in unpacked sizes; "stored" says how each file comes
                    p["files"] = stored_files(m, p["files"])
                    self._save()
            return m, p
        except PackError:
            for special in SPECIAL:  # a bad pair is downloaded again next time, not kept
                downloader.discard(os.path.join(staging, special))
                try:
                    os.remove(os.path.join(staging, special))
                except OSError:
                    pass
            with self.lock:
                p["have"] = [h for h in p["have"] if h not in SPECIAL]
                self._save()
            raise

    def _check_disk(self, key, p, staging):
        """Before a pack's first content download, its signed pack.json already checked (_signed()): check the
        files already in its install folders (on_disk()). Every file there: the pack is installed where it is
        (adopt()) and True is returned. Otherwise the matching files are linked into the staging folder and count
        as in."""
        name = p["pack"]
        self.checking = {"pack": name, "done": 0, "bytes": 0}
        self._set(phase="checking")

        def progress(done, total):
            self.checking = {"pack": name, "done": done, "bytes": total}
        found = on_disk(name, [f for f in p["files"] if f["path"] not in SPECIAL], stop=self.stopping.is_set,
                        progress=progress)
        self.checking = None
        where, match = found["where"], set(found["match"])
        content = {f["path"] for f in p["files"] if f["path"] not in SPECIAL}
        if where and content <= match:
            self._set(phase="installing")
            self._installed(name, p, adopt(name, where, staging, p["files"]))
            shutil.rmtree(staging, ignore_errors=True)
            return True
        with self.lock:
            for path in sorted(match - set(p["have"]) - set(SPECIAL)):
                _link_in(os.path.join(where, path), os.path.join(staging, path))
                p["have"].append(path)
            p["checked"] = True
            self._save()
        return False

    def _install_if_complete(self, name):
        """Install the pack when every file of it is in (and it isn't installed yet). One install at a time; the
        other workers carry on downloading. The library's install hashes every file again, so the job's phase says
        "installing" only while nothing else is left to download."""
        with self.install_lock:
            p = self._pack(name)
            if p is None or p.get("installed") or any(f["path"] not in p["have"] for f in p["files"]):
                return
            self.installing.add(name)
            others = [x for x in self.state.get("packs") or [] if x["pack"] != name and not x.get("installed")]
            if not others:
                self._set(phase="installing")
            try:
                record = install(name, os.path.join(incoming_dir(), name), p["files"], stop=self.stopping.is_set)
                self._installed(name, p, record)
            finally:
                self.installing.discard(name)

    def _tasks(self, names):
        """What is left to download, in the order the workers take it: the guides' files first, then the rest by
        size, largest first, with a split file's missing segments counted as SEGMENT each. Each task is
        (pack, the job's pack, file, segment index or None)."""
        tasks = []
        for name in names:
            p = self._pack(name)
            if p is None or p.get("installed"):
                continue
            staging = os.path.join(incoming_dir(), name)
            for f in p["files"]:
                if f["path"] in p["have"]:
                    continue
                if f["size"] >= SPLIT_MIN:
                    dest = os.path.join(staging, f["path"])
                    if os.path.isfile(dest) and not os.path.exists(dest + ".part") and _finish_split(dest, f):
                        self._mark_have(name, p, f)  # moved into place by an earlier run that stopped right after
                        continue
                    seg = _prepare_segments(dest, f)
                    if len(seg["done"]) == _segment_count(f["size"]):
                        # every segment was in, but the app stopped before the file was checked and moved into
                        # place (or the move failed): do that now; a wrong file starts again from nothing
                        if _finish_split(dest, f):
                            self._mark_have(name, p, f)
                            continue
                        seg = _prepare_segments(dest, f)
                    tasks += [(name, p, f, i) for i in range(_segment_count(f["size"])) if i not in seg["done"]]
                else:
                    tasks.append((name, p, f, None))
        first = PACK_ORDER[0]
        return sorted(tasks, key=lambda t: (t[0] != first, -(SEGMENT if t[3] is not None else t[2]["size"])))

    def _download_files(self, key, names):
        """Download every file the packs in names still need, self.workers at a time, each worker on one connection
        it keeps open. Each file still goes through downloader.fetch() (or, split, fetch_segment() and one SHA-256
        of the whole file at the end): its size and SHA-256 are checked, a partial file resumes, and it joins its
        pack's "have" list (saved in STATE_FILE) once it is in. A pack whose files are all in is installed at once,
        while the rest keep coming. A failure in one worker stops the others after their current chunk, partial
        files kept, and is raised; a pause raises Stopped. When fresh links bring a changed file list, the workers
        stop and start again from the new list."""
        while True:
            tasks = self._tasks(names)  # may finish a split file left whole on disk
            for name in names:  # a pack with nothing left to download (files found on disk, or a resumed job)
                self._install_if_complete(name)
            if not tasks:
                left = [n for n in names if not (self._pack(n) or {}).get("installed")]
                if left:  # never "done" with a pack that isn't in place
                    raise PackError(f"the {left[0]} pack is incomplete; press Resume to finish it")
                return
            work = queue.Queue()
            for t in tasks:
                work.put(t)
            abort = threading.Event()  # another worker failed, or a file list changed
            errors = []

            def stop():
                return self.stopping.is_set() or abort.is_set()

            def worker():
                try:
                    while not stop():
                        try:
                            name, p, f, seg = work.get_nowait()
                        except queue.Empty:
                            return
                        if self._pack(name) is not p:  # another worker's fresh links changed this file list
                            abort.set()
                            return
                        staging = os.path.join(incoming_dir(), name)
                        try:
                            if seg is None:
                                ok = self._fetch(key, p, f, staging, stop=stop)
                                finished = ok
                            else:
                                ok = self._fetch_segment(key, p, f, staging, seg, stop=stop)
                                finished = ok and self._segment_done(p, f, staging, seg)
                            if not ok:
                                abort.set()
                                return
                            if finished:
                                self._mark_have(name, p, f)
                                self._install_if_complete(name)
                        except Stopped:
                            return
                        except BaseException as exc:  # noqa: BLE001 - handed to the job's own handlers below
                            errors.append(exc)
                            abort.set()
                            return
                finally:
                    downloader.close_kept()
            threads = [threading.Thread(target=worker, daemon=True, name=f"upgrade-packs-{i}")
                       for i in range(max(1, min(self.workers, len(tasks))))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if errors:
                raise errors[0]
            if self.stopping.is_set():
                raise Stopped()
            # a list changed (fresh links), or every task is done: look again

    def _segment_done(self, p, f, staging, seg):
        """Record segment seg of a split file as in. When it was the last one, check the whole file's size and SHA-256
        and move it into place: True. A wrong file is deleted, segments and all (ChecksumError)."""
        dest = os.path.join(staging, f["path"])
        with self.lock:
            state = _read_segments(dest, f) or {"done": []}
            if seg not in state["done"]:
                state["done"].append(seg)
            _write_segments(dest, f, state)
            with self.inflight_lock:
                self.inflight.pop((p["pack"], f["path"], seg), None)
            if len(state["done"]) < _segment_count(f["size"]):
                return False
        if not _finish_split(dest, f):
            raise downloader.ChecksumError(f"{os.path.basename(dest)}: the SHA-256 does not match the one published")
        return True

    def _mark_have(self, name, p, f):
        with self.lock:
            cur = next((x for x in self.state.get("packs") or [] if x["pack"] == name), None)
            if cur is not None and f["path"] not in cur["have"] and (cur is p or o_same(cur, f)):
                cur["have"].append(f["path"])  # the same file in a newer list counts too
            self._save()

    def _fresh_links(self, key, seen_gen):
        """Ask for fresh links unless another worker already has since this one last looked (seen_gen)."""
        with self.ask_lock:
            if self.links and self.links.get("gen") != seen_gen and self._links_fresh():
                return
            self._ask(key)

    def _link(self, key, p, f):
        """(links, url) for a file, asking for fresh links first when they are about to expire. url None: fresh links
        changed the pack's files (start over from the new list)."""
        links = self.links
        if not self._links_fresh():
            self._fresh_links(key, links.get("gen") if links else None)
            if self._pack(p["pack"]) is not p:
                return None, None
            links = self.links
        url = links["urls"].get((p["pack"], f["path"]))
        if not url:
            raise DownloadError(f"the key service no longer lists {p['pack']}/{f['path']}")
        return links, url

    def _expired(self, links):
        with self.ask_lock:  # an expired or refused link: fresh links, then the same request again
            if self.links is links:
                self.links = None

    def _fetch(self, key, p, f, staging, stop=None):
        """Download one whole file. True when it is in; False when fresh links changed the pack's files (start over
        from the new list). Safe to run in several threads at once."""
        tries = drops = 0
        stop = stop or self.stopping.is_set
        while True:
            links, url = self._link(key, p, f)
            if url is None:
                return False
            last = [downloader.partial_size(os.path.join(staging, f["path"]))]  # the bytes in count once
            speed = self.speed

            def progress(done, total):
                if speed is not None:
                    speed.add(done - last[0])
                last[0] = done
            try:
                if f.get("stored", {}).get("frames"):
                    self._fetch_compressed(url, p, f, os.path.join(staging, f["path"]), stop)
                else:
                    downloader.fetch(url, os.path.join(staging, f["path"]), frozenset({_service_host(self.service)}),
                                     size=f["size"], sha256=f["sha256"], progress=progress,
                                     stop=stop, same_file=f"{p['pack']}/{f['path']}", keep=True)
                return True
            except DownloadError as exc:
                if transient(exc):
                    drops += 1
                    if drops > len(DROP_DELAYS):
                        raise
                    self._wait_to_retry(drops, stop)  # the partial file stays: the next try asks for the rest
                    continue
                tries += 1
                if exc.status in (403, 410) and tries < LINK_TRIES:
                    self._expired(links)
                    continue
                raise

    def _fetch_compressed(self, url, p, f, dest, stop):
        """One try at a compressed (format 2) whole file: its frames from the first one not yet unpacked into
        <dest>.part, unpacked as they arrive (downloader.fetch_frames()), then its size and SHA-256 checked against
        pack.json and the file moved to dest. The partial file is the unpacked bytes, with the same <dest>.part.json
        as downloader.fetch() writes, so a partial file from either way of downloading carries on in the other. A retry
        writes its first frame again over the bytes already there, so the file (and the progress bar) never shrinks."""
        meta = {"url": f"{p['pack']}/{f['path']}", "size": f["size"], "sha256": f["sha256"]}
        side = _read_json(dest + ".part.json") or {}
        if any(side.get(k) != v for k, v in meta.items()):
            downloader.discard(dest)  # a partial file of something else: start over
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        _write_json(dest + ".part.json", dict(meta, validator=None))  # no validator: fetch() starts such a file over
        frame, frames = f["frame"], f["stored"]["frames"]
        have = min(downloader.partial_size(dest), f["size"])
        first = have // frame
        speed = self.speed
        pos, reached = [first * frame], [have]

        def progress(n):
            pos[0] += n
            if pos[0] > reached[0]:
                if speed is not None:
                    speed.add(pos[0] - reached[0])
                reached[0] = pos[0]
        if first < len(frames):
            downloader.fetch_frames(url, dest + ".part", frozenset({_service_host(self.service)}), frame_size=frame,
                                    frames=frames, first=first, last=len(frames) - 1, size=f["size"],
                                    stored_size=f["stored"]["size"], progress=progress, stop=stop, keep=True)
        part = dest + ".part"
        with open(part, "r+b") as out:
            out.truncate(f["size"])
        if safe_fetch.sha256_file(part) != f["sha256"]:
            downloader.discard(dest)
            raise downloader.ChecksumError(f"{os.path.basename(dest)}: the SHA-256 does not match the one published")
        os.replace(part, dest)
        downloader.discard(dest)

    def _fetch_segment(self, key, p, f, staging, seg, stop=None):
        """Download one segment of a split file into its .part file. True when it is in; False when fresh links
        changed the pack's files. A dropped connection is tried again (DROP_DELAYS) from the byte it stopped at, and
        the progress bar keeps the bytes already in. A file changed on the server (its validator no longer matches)
        is started again from nothing next time, and the error raised."""
        tries = drops = 0
        stop = stop or self.stopping.is_set
        dest = os.path.join(staging, f["path"])
        start, end = _segment_range(f["size"], seg)
        speed = self.speed
        slot = (p["pack"], f["path"], seg)
        with self.inflight_lock:
            self.inflight[slot] = 0  # this segment's bytes written in this run, at start.. on

        def progress(n):
            with self.inflight_lock:
                self.inflight[slot] = self.inflight.get(slot, 0) + n
            if speed is not None:
                speed.add(n)
        try:
            while True:
                links, url = self._link(key, p, f)
                if url is None:
                    with self.inflight_lock:
                        self.inflight.pop(slot, None)
                    return False
                with self.lock:
                    validator = (_read_segments(dest, f) or {}).get("validator")
                with self.inflight_lock:
                    have = self.inflight.get(slot, 0)
                    if have > end - start:
                        have = self.inflight[slot] = 0
                try:
                    if f.get("stored", {}).get("frames"):  # compressed: from the frame the bytes in stopped in
                        frame, frames = f["frame"], f["stored"]["frames"]
                        per = SEGMENT // frame
                        pos = [have - have % frame]  # this segment's bytes written in this try, the frame again

                        def zprogress(n):
                            pos[0] += n
                            with self.inflight_lock:
                                delta = pos[0] - self.inflight.get(slot, 0)
                                if delta > 0:
                                    self.inflight[slot] = pos[0]
                            if delta > 0 and speed is not None:
                                speed.add(delta)
                        got = downloader.fetch_frames(
                            url, dest + ".part", frozenset({_service_host(self.service)}), frame_size=frame,
                            frames=frames, first=seg * per + pos[0] // frame,
                            last=min((seg + 1) * per, len(frames)) - 1, size=f["size"],
                            stored_size=f["stored"]["size"], validator=validator, progress=zprogress, stop=stop,
                            keep=True)
                    else:
                        got = downloader.fetch_segment(url, dest + ".part", start + have, end,
                                                       frozenset({_service_host(self.service)}), total=f["size"],
                                                       validator=validator, progress=progress, stop=stop, keep=True)
                except DownloadError as exc:
                    if transient(exc):
                        drops += 1
                        if drops > len(DROP_DELAYS):
                            raise
                        self._wait_to_retry(drops, stop)
                        continue
                    tries += 1
                    if exc.status in (403, 410) and tries < LINK_TRIES:
                        self._expired(links)
                        continue
                    if exc.status in (304, 412):  # the file changed under its validator: all of it again
                        _discard_segments(dest)
                    raise
                with self.lock:
                    state = _read_segments(dest, f) or {"done": []}
                    if not state.get("validator") and got:
                        state["validator"] = got
                        _write_segments(dest, f, state)
                return True  # _segment_done() records it and clears its in-flight count
        except BaseException:
            with self.inflight_lock:
                self.inflight.pop(slot, None)  # a segment not recorded is downloaded again whole next time
            raise

    def _wait_to_retry(self, attempt, stop):
        """Wait DROP_DELAYS[attempt - 1] seconds before retry number attempt; a pause (or another worker's failure)
        during the wait raises Stopped. Nothing comes in meanwhile, so the speed behind the time left drops with it."""
        until = time.monotonic() + DROP_DELAYS[min(attempt, len(DROP_DELAYS)) - 1]
        while True:
            if stop():
                raise Stopped()
            left = until - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.1, left))


# ---------- split files: SEGMENT-sized parts of one big file ----------
# A split file is downloaded into <dest>.part, made at its full size first, each segment written at its offset by
# downloader.fetch_segment(). <dest>.segments.json says which segments are in and the server's validator, so a pause
# or a quit keeps every whole segment. The finished file's size and SHA-256 are checked before it is moved to dest.

def _segment_count(size):
    return max(1, -(-size // SEGMENT))


def _segment_range(size, i):
    start = i * SEGMENT
    return start, min(size, start + SEGMENT) - 1


def _read_segments(dest, f):
    """The split file's record, or None when there is none or it is for another file (size and SHA-256)."""
    data = _read_json(dest + ".segments.json")
    if not isinstance(data, dict) or data.get("size") != f["size"] or data.get("sha256") != f["sha256"]:
        return None
    done = data.get("done")
    if not isinstance(done, list) or not all(isinstance(i, int) for i in done):
        return None
    try:  # the .part can be moved into place between any two looks (_finish_split()): then there is no record
        if os.path.getsize(dest + ".part") != f["size"] or not os.path.isfile(dest + ".part"):
            return None
    except OSError:
        return None
    return {"done": sorted(set(done)), "validator": data.get("validator")}


def _write_segments(dest, f, state):
    _write_json(dest + ".segments.json", {"size": f["size"], "sha256": f["sha256"], "done": sorted(set(state["done"])),
                                          "validator": state.get("validator")})


def _discard_segments(dest):
    for path in (dest + ".part", dest + ".segments.json"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _finish_split(dest, f):
    """Check a split file whose segments are all in (size and SHA-256 of the whole) and move it into place: True. A
    wrong file is deleted, segments record and all: False. Also True when an earlier run already moved it into place
    but stopped before recording it."""
    part = dest + ".part"
    if not os.path.isfile(part) and os.path.isfile(dest) and os.path.getsize(dest) == f["size"] \
            and safe_fetch.sha256_file(dest) == f["sha256"]:
        _discard_segments(dest)
        return True
    if not os.path.isfile(part) or os.path.getsize(part) != f["size"] or safe_fetch.sha256_file(part) != f["sha256"]:
        _discard_segments(dest)
        return False
    os.replace(part, dest)
    _discard_segments(dest)
    return True


def _prepare_segments(dest, f):
    """The split file's record, starting one (an empty .part of the full size) when there is none that fits."""
    state = _read_segments(dest, f)
    if state is not None:
        return state
    if os.path.exists(dest + ".segments.json"):
        _discard_segments(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # A whole-file partial download of this same file (1.0.1 downloaded one file at a time): its bytes are the
    # file's start, so every segment they cover counts as in, and the rest comes in segments.
    have = 0
    sidecar = _read_json(dest + ".part.json") or {}
    if os.path.isfile(dest + ".part") and sidecar.get("size") == f["size"] and sidecar.get("sha256") == f["sha256"]:
        have = min(downloader.partial_size(dest), f["size"])
    else:
        downloader.discard(dest)
    try:
        os.remove(dest + ".part.json")
    except FileNotFoundError:
        pass
    with open(dest + ".part", "r+b" if have else "wb") as out:
        out.truncate(f["size"])  # sparse: no space is used until the bytes come
    state = {"done": [i for i in range(_segment_count(f["size"])) if _segment_range(f["size"], i)[1] < have],
             "validator": sidecar.get("validator") if have else None}
    _write_segments(dest, f, state)
    return state


def check_updates(app_dir, key, service=None):
    """The Upgrade page's "Check for updates", run only when the person clicks it: ask the key service for the links
    (POST /v1/download-links, as "Download now" does, so its answer goes to note_service_answer(): "revoked" locks
    the paid features here), download only each pack's pack.json and pack.sig into memory, check them
    (check_manifest()), and compare each version with the one installed. Returns {"packs": [{"pack", "name",
    "installed", "latest", "newer"}], "newer": any pack newer, "checked": unix time}. A version is newer when it
    sorts after the installed one (versions are dates, docs/packs.md), so an older signed pack is never offered.
    Raises Refused, DownloadError, FetchError or PackError as the download does."""
    service = service or KEY_SERVICE
    try:
        answer = ask_links(key, install_id(app_dir), service)
    except Refused as exc:
        _note_answer(app_dir, key, exc.code)  # "revoked" locks the paid features here; the others change nothing
        raise
    _note_answer(app_dir, key, None)
    host = frozenset({_service_host(service)})
    have, out = installed(app_dir), []
    for p in answer["packs"]:
        raw = {}
        for special, limit in ((MANIFEST, MAX_MANIFEST), (SIGNATURE, MAX_SIGNATURE)):
            f = next(f for f in p["files"] if f["path"] == special)
            raw[special] = downloader.fetch_bytes(f["url"], host, max_bytes=limit)
        m = check_manifest(raw[MANIFEST], raw[SIGNATURE], p["pack"], p["files"])
        mine = (have.get(p["pack"]) or {}).get("version")
        newer = isinstance(mine, str) and m["version"] > mine
        out.append({"pack": p["pack"], "name": PACK_NAMES[p["pack"]], "installed": mine, "latest": m["version"],
                    "newer": newer})
    return {"packs": out, "newer": any(p["newer"] for p in out), "checked": time.time()}


def _note_answer(app_dir, key, code):
    try:
        upgrade_key.note_service_answer(app_dir, key, code)
    except OSError:
        pass  # as Job._note: a disk error must not hide the service's answer


def o_same(old_pack, f):
    """Whether a file already downloaded in old_pack is the same file (size and SHA-256) the service lists now."""
    return any(listing(x) == listing(f) or (x["path"], x["size"], x["sha256"]) == (f["path"], f["size"], f["sha256"])
               for x in old_pack.get("files") or [])


def computers_text(n):
    """3 -> "three computers", 25 -> "25 computers": the licence's number, as the page writes it."""
    n = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 3
    return f"{({1: 'one', 2: 'two', 3: 'three'}).get(n, n)} computer{'s' if n != 1 else ''}"


def refusal_text(code, limit=None):
    """limit: the number of computers the key allows (3 for an individual key, 25 for an organisation key)."""
    return {"invalid_key": "The download server doesn't recognise this key.",
            "revoked": "This key has been withdrawn. If you think this is a mistake, write to us.",
            "bad_install_id": "This computer's install id isn't valid. Try again.",
            "install_limit_reached": f"This key is already in use on {computers_text(limit)}, the most it allows."
            }.get(code, code)
