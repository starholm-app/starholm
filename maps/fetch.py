"""The refresh job's side of offline maps: find each wanted map's newest file on Kiwix's download server, download
it, check it against the SHA-256 in its .meta4, and only then move it into data.noindex/maps/. The app never runs
this: library/refresh.py calls run() (see maps/__init__.py for the files the two sides share).

Rules:
- Only maps in wanted.json are downloaded. A map taken out of wanted.json while it downloads is thrown away.
- A download lands in maps/.incoming/ and is moved into maps/ only after its size and SHA-256 match the .meta4. A
  failed or cut-off download never replaces the copy in use, which stays live.
- Each map keeps its live copy and at most one older one; older ones are deleted once a newer one is in place.
- Maps nobody wants any more are deleted (the app deletes them itself too; this catches a delete that failed).
"""
import json
import os
import re
import shutil
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maps  # noqa: E402
from library import safe_fetch  # noqa: E402
from library.safe_fetch import FetchError  # noqa: E402

LISTING_HOST = "lb.download.kiwix.org"  # serves the folder listing and the .meta4 files without a redirect
DOWNLOAD_HOST = "download.kiwix.org"    # where a download starts (it redirects to a mirror); as library/fetch_zims.py
LISTING_URL = f"https://{LISTING_HOST}/zim/maps/"
KEEP = 2                                # the live copy and one older one
FREE_SPACE_MARGIN = 10 << 30            # keep 10 GB free beyond a download, as library/fetch_zims.py does
_LINK = re.compile(r'href="(maps_[a-z]{2,3}_[a-z0-9-]{1,60}_\d{4}-\d{2}[a-z]?\.zim)"')


# ---------- the status file the app reads ----------

class Status:
    def __init__(self):
        old = maps._read_json(maps.STATUS_PATH)
        problems = old.get("problems") if isinstance(old, dict) and isinstance(old.get("problems"), dict) else {}
        latest = old.get("latest") if isinstance(old, dict) and isinstance(old.get("latest"), dict) else {}
        self.data = {"version": 1, "pid": os.getpid(), "running": True, "started": maps.utc_now(), "finished": None,
                     "map": None, "done": None, "total": None, "problems": problems, "latest": latest}
        self._lock = threading.Lock()
        self.write()

    def write(self):
        with self._lock:
            maps._write_json(maps.STATUS_PATH, self.data)

    def update(self, **kw):
        self.data.update(kw)
        self.write()

    def problem(self, map_id, text):
        if text:
            self.data["problems"][map_id] = str(text)[:300]
        else:
            self.data["problems"].pop(map_id, None)
        self.write()

    def latest(self, map_id, name, size):
        """The newest file Kiwix lists for a map and its size, for the Maps tab's "about N MB"."""
        self.data["latest"][map_id] = {"file": name, "size": size, "checked": maps.utc_now()}
        self.write()

    def finish(self):
        self.update(running=False, finished=maps.utc_now(), map=None, done=None, total=None)

    def watch(self, path, map_id, total):
        """While open, a thread refreshes the byte count from the size of the file being written."""
        status = self

        class Watch:
            def __enter__(self):
                self.stop = threading.Event()
                status.update(map=map_id, done=_size(path), total=total)

                def loop():
                    while not self.stop.wait(3):
                        status.update(done=_size(path))
                self.thread = threading.Thread(target=loop, daemon=True)
                self.thread.start()
                return self

            def __exit__(self, *exc):
                self.stop.set()
                self.thread.join()
                status.update(map=None, done=None, total=None)
        return Watch()


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# ---------- what Kiwix offers ----------

def parse_listing(text):
    """Every map file name in the download folder's listing."""
    return sorted(set(_LINK.findall(text or "")))


def newest(prefix, names):
    """The newest file of a map in a list of file names (by its date, then its re-release letter), or None."""
    mine = [n for n in names if maps.is_map_file(n, prefix)]
    return max(mine, key=maps.file_date) if mine else None


def fetch_listing():
    data = safe_fetch.fetch_small(LISTING_URL, frozenset({LISTING_HOST}), max_bytes=4 << 20, max_time=120)
    return parse_listing(data.decode("utf-8", "replace"))


def fetch_checksum(name):
    """(size, sha256) of a map file from its .meta4. FetchError if unavailable."""
    data = safe_fetch.fetch_small(f"{LISTING_URL}{name}.meta4", frozenset({LISTING_HOST}), max_bytes=2 << 20)
    return safe_fetch.parse_meta4(data, name)


# ---------- one map ----------

def download_url(name):
    return f"https://{DOWNLOAD_HOST}/zim/maps/{name}"


def install(map_id, name, size, sha256, status=None, download=None):
    """Download name into .incoming/, check it, and move it into the maps folder. True when a verified copy of name
    is in place afterwards. download(url, dest, **kw) stands in for safe_fetch.download in tests."""
    download = download or safe_fetch.download
    final = os.path.join(maps.MAPS_DIR, name)
    if maps.verified_record(final):
        return True
    os.makedirs(maps.INCOMING_DIR, exist_ok=True)
    part = os.path.join(maps.INCOMING_DIR, name)
    have = _size(part)
    if have > size:
        os.remove(part)
        have = 0
    if have < size:
        if shutil.disk_usage(maps.MAPS_DIR).free < size - have + FREE_SPACE_MARGIN:
            raise FetchError(f"not enough disk space for {name} ({size / 1e9:.2f} GB and 10 GB to spare)")
        print(f"maps: downloading {name} ({size / 1e9:.2f} GB)", flush=True)
        watch = status.watch(part, map_id, size) if status else None
        try:
            if watch:
                watch.__enter__()
            # One connection, resumed on the next run if cut off; --max-filesize is the .meta4's size.
            ok = download(download_url(name), part, frozenset({DOWNLOAD_HOST}), follow=True, resume=True,
                          max_bytes=size, max_time=3600 + (size - have) // 250_000)
        finally:
            if watch:
                watch.__exit__(None, None, None)
        if not ok:
            if _size(part) > size:
                os.remove(part)
            raise FetchError(f"the download of {name} stopped (the next run carries on from where it got to)")
    if _size(part) != size:
        os.remove(part)
        raise FetchError(f"{name} has the wrong size; it was deleted")
    if safe_fetch.sha256_file(part) != sha256:
        os.remove(part)
        raise FetchError(f"{name} failed its checksum; it was deleted and the map in use is unchanged")
    if map_id not in maps.wanted():
        os.remove(part)
        print(f"maps: {map_id} was removed while it downloaded; {name} thrown away", flush=True)
        return False
    os.replace(part, final)
    maps.write_verified(final, sha256)
    print(f"maps: {name} checked and moved into place", flush=True)
    return True


def prune(prefix, keep=KEEP):
    """Delete all but the newest keep verified copies of a map, and any unverified file of it left in the maps
    folder. Returns the names deleted."""
    gone = []
    keep_names = {n for n, _ in maps.local_files(prefix)[:keep]}
    try:
        names = os.listdir(maps.MAPS_DIR)
    except OSError:
        return gone
    for name in names:
        base = name[:-len(".verified")] if name.endswith(".verified") else name
        if maps.is_map_file(base, prefix) and base not in keep_names:
            os.remove(os.path.join(maps.MAPS_DIR, name))
            if name == base:
                gone.append(name)
    return gone


def drop_unwanted(want):
    """Delete the copies (and partial downloads) of maps nobody wants. Returns the names deleted."""
    gone = []
    for m in maps.catalog():
        if m["id"] in want:
            continue
        gone += prune(m["prefix"], keep=0)
        try:
            for name in os.listdir(maps.INCOMING_DIR):
                if maps.is_map_file(name, m["prefix"]):
                    os.remove(os.path.join(maps.INCOMING_DIR, name))
                    gone.append(name)
        except OSError:
            pass
    return gone


def clear_stale_incoming(keep):
    """Delete every partial download in .incoming/ except the names in keep (each wanted map's newest build): an
    older build left when a newer one appeared, a map no longer wanted, anything else. Returns the names deleted."""
    gone = []
    try:
        names = os.listdir(maps.INCOMING_DIR)
    except OSError:
        return gone
    for name in names:
        if name not in keep:
            path = os.path.join(maps.INCOMING_DIR, name)
            if os.path.isfile(path) and not os.path.islink(path):
                os.remove(path)
                gone.append(name)
    return gone


def clear_request():
    try:
        os.remove(maps.REQUEST_PATH)
    except FileNotFoundError:
        pass


def requested():
    return os.path.exists(maps.REQUEST_PATH)


def run(listing=None, checksum=None, download=None):
    """Bring every wanted map up to date. The caller holds the refresh lock. Returns the number of maps that
    failed. listing() and checksum(name) stand in for the network in tests."""
    clear_request()
    status = Status()
    failed = 0
    try:
        want = maps.wanted()
        for name in drop_unwanted(want):
            print(f"maps: deleted {name} (no longer wanted)", flush=True)
        if not want:
            print("maps: none wanted", flush=True)
            return 0
        try:
            names = (listing or fetch_listing)()
        except FetchError as exc:
            print(f"maps: FAILED to read Kiwix's map folder: {exc}", flush=True)
            for map_id in want:
                status.problem(map_id, "Couldn't reach Kiwix to check for this map. It is tried again next time.")
            return len(want)
        newest_names = {m["id"]: newest(m["prefix"], names) for m in maps.catalog()}
        for name in clear_stale_incoming({n for i, n in newest_names.items() if n and i in want}):
            print(f"maps: deleted the partial download {name} (an older build, or not wanted)", flush=True)
        for m in maps.catalog():
            name = newest_names[m["id"]]
            if m["id"] not in want:
                if name:  # the size the Maps tab offers it at
                    try:
                        status.latest(m["id"], name, (checksum or fetch_checksum)(name)[0])
                    except FetchError:
                        pass
                continue
            try:
                if not name:
                    raise FetchError("Kiwix no longer offers this map")
                local = maps.local_files(m["prefix"])
                if local and local[0][0] == name:
                    print(f"maps: {name} is current", flush=True)
                else:
                    size, sha256 = (checksum or fetch_checksum)(name)
                    status.latest(m["id"], name, size)
                    install(m["id"], name, size, sha256, status, download)
                for gone in prune(m["prefix"]):
                    print(f"maps: deleted {gone} (an older copy)", flush=True)
                status.problem(m["id"], None)
            except (FetchError, OSError) as exc:
                failed += 1
                print(f"maps: FAILED {m['id']}: {exc}", flush=True)
                status.problem(m["id"], str(exc))
        return failed
    finally:
        status.finish()


if __name__ == "__main__":
    print(json.dumps(maps.list_maps(), indent=1))
