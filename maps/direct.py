"""Download one map straight from Kiwix, in the app's own process, when the person presses Download on the Maps tab.
This is how maps arrive in the public and bundled app, which have no weekly refresh job (the owner's copy uses it too).
Nothing here runs on its own: a click starts it, and Pause stops it.

The steps, the same rules as maps/fetch.py (the refresh job's side):
1. Read Kiwix's map folder listing (LISTING_URL) and take the newest file of the map's prefix (maps/catalog.json).
2. Read that file's .meta4: its exact size and SHA-256.
3. Download it with library/downloader.py into <maps>/.direct/<file>: progress, pause and resume (a partial file
   carries on from its last byte with a range request, after a restart too), its size and SHA-256 checked when
   complete; a file that fails is deleted. With a SHA-256 to check, the download may follow Kiwix's redirect to a
   mirror.
4. Only then move it into the maps folder with its .verified record, and delete older copies (at most one older one
   is kept, as the refresh job does). The copy in use stays live until the new one is in place.

Progress goes to the maps folder's status.json in the refresh job's format (maps/fetch.py Status), so the Maps tab
shows it the same way. One map downloads at a time, and never while the refresh job's maps step runs. The hosts
contacted are Kiwix's only: lb.download.kiwix.org for the listing and .meta4, download.kiwix.org and its mirrors for
the file."""
import os
import shutil
import threading
import time

import maps
from maps import fetch
from library import downloader, safe_fetch
from library.downloader import DownloadError, Stopped
from library.safe_fetch import FetchError

MAX_LISTING = 4 << 20
MAX_META4 = 2 << 20
STATUS_EVERY = 1.0  # seconds between status.json writes while a map downloads


def direct_dir():
    return os.path.join(maps.MAPS_DIR, ".direct")


def partials(prefix):
    """[(file name, bytes)] of the paused downloads of this map in .direct/."""
    try:
        names = os.listdir(direct_dir())
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(".part") and maps.is_map_file(n[:-len(".part")], prefix):
            base = n[:-len(".part")]
            out.append((base, downloader.partial_size(os.path.join(direct_dir(), base))))
    return out


def partial(prefix):
    """(file name, bytes) of a paused download of this map, the newest build's, or None."""
    found = partials(prefix)
    return max(found, key=lambda x: maps.file_date(x[0])) if found else None


class Job:
    """The Maps tab's downloads. listing(), meta4(name) and the downloader's CONNECT stand in for the network in
    tests."""

    def __init__(self, listing=None, meta4=None):
        self.lock = threading.Lock()
        self.thread = None
        self.map_id = None
        self.stopping = threading.Event()
        self.listing = listing or self._listing
        self.meta4 = meta4 or self._meta4

    # -- the network --
    @staticmethod
    def _listing():
        data = downloader.fetch_bytes(fetch.LISTING_URL, frozenset({fetch.LISTING_HOST}), max_bytes=MAX_LISTING)
        return fetch.parse_listing(data.decode("utf-8", "replace"))

    @staticmethod
    def _meta4(name):
        data = downloader.fetch_bytes(f"{fetch.LISTING_URL}{name}.meta4", frozenset({fetch.LISTING_HOST}),
                                      max_bytes=MAX_META4)
        return safe_fetch.parse_meta4(data, name)

    # -- actions --
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def current(self):
        return self.map_id if self.running() else None

    def start(self, map_id):
        """Download (or Resume) map_id now. Raises maps.MapError: 404 for a map not on offer, 409 while another map
        downloads or the refresh job's maps step runs."""
        m = maps.entry(map_id)
        with self.lock:
            if self.running():
                if self.map_id == map_id:
                    return
                raise maps.MapError(409, "Another map is downloading. Wait for it to finish, or pause it first.")
            job = maps.job_status()
            if job["running"]:  # the refresh job's maps step, in another process
                raise maps.MapError(409, "The weekly refresh is downloading maps right now. Try again when it has "
                                         "finished.")
            maps.set_wanted(map_id, True)
            self.map_id = map_id
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, args=(m,), daemon=True, name=f"map-{map_id}")
            self.thread.start()

    def pause(self):
        self.stopping.set()

    def wait(self, seconds=None):
        thread = self.thread
        if thread:
            thread.join(seconds)

    def forget(self, map_id):
        """A map was deleted: stop its download and drop its partial file."""
        m = maps.entry(map_id)
        if self.current() == map_id:
            self.pause()
            self.wait(30)
        for base, _ in partials(m["prefix"]):
            downloader.discard(os.path.join(direct_dir(), base))

    # -- the thread --
    def _run(self, m):
        status = fetch.Status()
        map_id, prefix = m["id"], m["prefix"]
        try:
            name = fetch.newest(prefix, self.listing())
            if not name:
                raise FetchError("Kiwix no longer offers this map")
            local = maps.local_files(prefix)
            if local and local[0][0] == name:
                status.problem(map_id, None)
                return
            size, sha256 = self.meta4(name)
            status.latest(map_id, name, size)
            for base, _ in partials(prefix):
                if base != name:  # an older build's partial file: Kiwix has a newer one now
                    downloader.discard(os.path.join(direct_dir(), base))
            os.makedirs(direct_dir(), exist_ok=True)
            dest = os.path.join(direct_dir(), name)
            have = downloader.partial_size(dest)
            if shutil.disk_usage(direct_dir()).free < size - have + fetch.FREE_SPACE_MARGIN:
                raise FetchError(f"not enough disk space for this map ({size / 1e9:.2f} GB and 10 GB to spare)")
            status.update(map=map_id, done=have, total=size)
            last = [0.0]

            def progress(done, total):
                now = time.monotonic()
                if now - last[0] >= STATUS_EVERY:
                    last[0] = now
                    status.update(done=done)
            downloader.fetch(fetch.download_url(name), dest, frozenset({fetch.DOWNLOAD_HOST}), size=size,
                             sha256=sha256, progress=progress, stop=self.stopping.is_set, follow=True)
            if map_id not in maps.wanted():  # deleted while it downloaded
                os.remove(dest)
                return
            final = os.path.join(maps.MAPS_DIR, name)
            os.replace(dest, final)
            maps.write_verified(final, sha256)
            fetch.prune(prefix)
            status.problem(map_id, None)
        except Stopped:
            status.problem(map_id, None)
        except (DownloadError, FetchError, OSError) as exc:
            status.problem(map_id, f"The download stopped: {exc}. Resume carries on from where it stopped.")
        finally:
            status.finish()
