"""The free library: the raw sources downloaded from their original hosts after the person's click, built on this
computer without the cleaning rules, checked, and swapped in.

The sources and their sizes, licences and hosts are library/free-sources.json (written by make_free_sources.py).
The project never redistributes a raw file: each comes from the host that publishes it, and only hosts the source
list names for that source are contacted (library/downloader.py, with safe_fetch.py's rules).

A Job runs in the app's server process, one thread at a time:
  1. start(keys) checks the keys and the free disk space (download + build + margin), then downloads each source
     into data.noindex/free/files/<key>/. Kiwix ZIMs are found through Kiwix's catalog and checked against the size
     and SHA-256 in Kiwix's .meta4; manuals and risk tables are checked to be PDFs or web pages. A dropped
     connection is resumed from its byte offset (up to AUTO_RESUMES times), pause() stops between two chunks, and
     every step is saved in data.noindex/free/state.json, so a quit or restart resumes where it stopped.
  2. When every source is downloaded, the build runs in a child process (`free_library.py --build`, low priority,
     holding the library lock refresh.py takes): build_library.build(raw=True) into libs/, then check_raw(). Only a
     build that passes is swapped in as "current"; the build that was live stays as the fallback
     (build_library.tidy_builds, KEEP_PREVIOUS_BUILD). A failed build is kept as libs/.failed-<name> and "current"
     is left alone.
  3. After the swap the downloaded files are deleted; the library holds their text.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import downloader, safe_fetch  # noqa: E402
from library.safe_fetch import FetchError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "free-sources.json")
BUILD_SHARE = 0.6          # the built library takes about this share of the downloaded size (15 GB from 33 GB today)
SPACE_MARGIN = 2 << 30     # free space kept beyond the download and the build
UNKNOWN_SIZE = 200 << 20   # what a source of unknown size (manuals, risk tables) counts for in the space check
AUTO_RESUMES = 3           # dropped connections resumed by themselves before a source shows Retry
SAVE_EVERY = 2.0           # seconds between saves of the progress while bytes arrive
MAX_FILE = 256 << 20       # the cap on one manual or risk table (as fetch_manuals.py)
MAX_CATALOG = 64 << 20
MAX_META4 = 2 << 20
ACTIVE = ("downloading", "building")
PAUSE_WAIT = 5.0           # seconds Pause waits for the download thread to stop (a stalled read can take longer)
STOPPING = "The download is still stopping. Try again in a moment."


class Refused(Exception):
    """A request the job turns down; str() is the message for the person. status is the HTTP status to answer."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def free_dir():
    return os.path.join(safe_fetch.DATA_DIR, "free")


def state_path():
    return os.path.join(free_dir(), "state.json")


def files_dir(key):
    return os.path.join(free_dir(), "files", key)


_sources = []


def sources():
    """The public source list, in the picker's order (largest first, then the manuals and risk tables)."""
    if not _sources:
        with open(SOURCES_FILE, encoding="utf-8") as f:
            _sources.append(json.load(f))
    return _sources[0]["sources"]


def source(key):
    for s in sources():
        if s["key"] == key:
            return s
    return None


def picker():
    """What the Library page's picker shows: each source with its size and licence, and the total."""
    items = [{"key": s["key"], "title": s["title"], "from": s["from"], "size": s["size"], "licence": s["licence"],
              "licence_text": s["licence_text"], "checksum": bool(s.get("checksum"))} for s in sources()]
    return {"sources": items, "count": len(items), "total_bytes": sum(s["size"] or 0 for s in items),
            "unknown_sizes": sum(1 for s in items if not s["size"])}


def disk_free(path=None):
    """Free bytes on the disk that holds the data folder (its nearest existing parent)."""
    path = os.path.abspath(path or safe_fetch.DATA_DIR)
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free


def space_needed(keys, done=None):
    """Bytes the download and build of keys need: what is left to download, the build, and a margin."""
    done = done or {}
    total = remaining = 0
    for key in keys:
        size = source(key)["size"] or UNKNOWN_SIZE
        total += size
        remaining += max(0, size - done.get(key, 0))
    return remaining + int(total * BUILD_SHARE) + SPACE_MARGIN


def built_raw(lib_dir):
    """True when the library in lib_dir is a free (raw) build: its build.json says so."""
    try:
        with open(os.path.join(lib_dir, "build.json"), encoding="utf-8") as f:
            return json.load(f).get("raw") is True
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def looks_right(name):
    def check(path):
        with open(path, "rb") as f:
            head = f.read(4096)
        if name.endswith(".pdf"):
            return head.startswith(b"%PDF-")
        return b"<html" in head.lower() or b"<!doctype html" in head.lower()
    return check


def run_build_child(job):
    """The build in a child process at low priority: this file with --build. Returns its result dict."""
    args = [sys.executable, "-I", os.path.abspath(__file__), "--build"]
    if os.path.isfile("/usr/bin/nice"):
        args = ["/usr/bin/nice", "-n", "10"] + args
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    proc = subprocess.Popen(args, cwd=os.path.dirname(HERE), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    job._child = proc
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip("\n"))
        del lines[:-200]
    proc.wait()
    job._child = None
    for line in reversed(lines):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"status": "failed", "problems": [f"the build stopped (exit {proc.returncode})"] + lines[-3:]}


class Job:
    """The free library's download and build. All state is in self.state (saved to state.json); methods are safe to
    call from any server thread. connect: downloader.CONNECT stand-in (tests); build: a function(job) -> result
    dict, run_build_child by default."""

    def __init__(self, connect=None, build=None):
        self.connect = connect
        self.build = build or run_build_child
        self.lock = threading.RLock()
        self.thread = None
        self.stop_flag = threading.Event()
        self.catalog = None
        self._child = None
        self._saved = 0.0
        self.state = self._load()

    # ---------- state ----------

    def _load(self):
        try:
            with open(state_path(), encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict) and isinstance(state.get("sources"), dict):
                for key, item in state["sources"].items():
                    if item.get("status") == "downloading":
                        item["status"] = "waiting"  # the app stopped mid-file: it resumes from the partial file
                return state
        except (OSError, ValueError):
            pass
        return None

    def _save(self, force=True):
        if not self.state:
            return
        now = time.monotonic()
        if not force and now - self._saved < SAVE_EVERY:
            return
        self._saved = now
        os.makedirs(free_dir(), exist_ok=True)
        tmp = state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, state_path())

    def snapshot(self):
        """The job as the Library page shows it, or None when there is none."""
        with self.lock:
            if not self.state:
                return None
            s = json.loads(json.dumps(self.state))
        items = s["sources"]
        s["running"] = bool(self.thread and self.thread.is_alive())
        s["count"] = len(items)
        s["done_count"] = sum(1 for i in items.values() if i["status"] == "done")
        s["total_bytes"] = sum(i.get("size") or 0 for i in items.values())
        s["done_bytes"] = sum(i.get("done") or 0 for i in items.values())
        s["now"] = next((k for k, i in items.items() if i["status"] == "downloading"), None)
        speed = s.get("speed") or 0
        left = s["total_bytes"] - s["done_bytes"]
        s["eta_seconds"] = int(left / speed) if speed > 0 and s["phase"] == "downloading" else None
        s["order"] = list(items)
        return s

    # ---------- what the page asks for ----------

    def start(self, keys):
        if not isinstance(keys, list) or not keys or len(keys) > len(sources()) \
                or not all(isinstance(k, str) and source(k) for k in keys) or len(set(keys)) != len(keys):
            raise Refused("Pick at least one source from the list.", 400)
        with self.lock:
            if self.state and self.state["phase"] not in ("done",):
                raise Refused("The free library is already being downloaded. Cancel it first to start again.")
            free = disk_free()
            need = space_needed(keys)
            if free < need:
                raise Refused(f"Not enough free space: this needs about {need / 1e9:.1f} GB and the disk has "
                              f"{free / 1e9:.1f} GB free. Free some space or pick fewer sources.", 507)
            order = [s["key"] for s in sources() if s["key"] in keys]
            self.state = {"phase": "downloading", "started": _now(), "finished": None, "free_bytes": free,
                          "needed_bytes": need, "speed": None, "problems": [], "banner_hidden": False,
                          "sources": {k: {"status": "waiting", "done": 0, "size": source(k)["size"], "error": None,
                                          "missed": []} for k in order}}
            self._save()
            self._run()

    def pause(self):
        with self.lock:
            if not self.state or self.state["phase"] != "downloading":
                raise Refused("Nothing is downloading.")
            self.stop_flag.set()
        self._join(PAUSE_WAIT)  # a read stalled on the network may outlast this; Resume waits until it has stopped
        with self.lock:
            if self.state and self.state["phase"] == "downloading":
                self.state["phase"] = "paused"
                for item in self.state["sources"].values():
                    if item["status"] == "downloading":
                        item["status"] = "waiting"
                self._save()

    def resume(self):
        with self.lock:
            if not self.state or self.state["phase"] not in ("paused", "failed", "build-failed"):
                raise Refused("There is nothing to resume.")
            self._not_stopping()
            for item in self.state["sources"].values():
                if item["status"] == "failed":
                    item.update(status="waiting", error=None)
            self.state["phase"] = "downloading"
            self.state["problems"] = []
            self._save()
            self._run()

    def retry(self, key):
        with self.lock:
            item = self.state and self.state["sources"].get(key)
            if not item or item["status"] != "failed":
                raise Refused("That source has not failed.")
            self._not_stopping()
            item.update(status="waiting", error=None)
            if self.state["phase"] in ("failed", "build-failed", "paused"):
                self.state["phase"] = "downloading"
                self.state["problems"] = []
            self._save()
            if not (self.thread and self.thread.is_alive()):
                self._run()

    def leave_out(self, key):
        """Drop a failed source from this download, so the others can be built."""
        with self.lock:
            item = self.state and self.state["sources"].get(key)
            if not item or item["status"] != "failed":
                raise Refused("Only a source that failed can be left out.")
            if len(self.state["sources"]) == 1:
                raise Refused("It is the only source picked. Cancel instead.")
            self._not_stopping()
            del self.state["sources"][key]
            shutil.rmtree(files_dir(key), ignore_errors=True)
            if self.state["phase"] == "failed":
                self.state["phase"] = "downloading"
            self._save()
            if not (self.thread and self.thread.is_alive()):
                self._run()

    def cancel(self):
        """Stop and forget the download, deleting what was downloaded. The live library is not touched."""
        with self.lock:
            if not self.state:
                raise Refused("There is nothing to cancel.")
            self.stop_flag.set()
            child = self._child
        if child:
            try:
                child.terminate()
            except OSError:
                pass
        self._join()
        with self.lock:
            self.state = None
            shutil.rmtree(free_dir(), ignore_errors=True)

    def hide_banner(self):
        with self.lock:
            if not self.state:
                self.state = {"phase": "done", "sources": {}, "banner_hidden": True}
            self.state["banner_hidden"] = True
            self._save()

    def resume_after_restart(self):
        """At the app's start: a download or build the app's quit interrupted carries on (the person started it)."""
        with self.lock:
            if self.state and self.state.get("phase") in ACTIVE and not (self.thread and self.thread.is_alive()):
                self._run()
                return True
        return False

    # ---------- the worker thread ----------

    def _join(self, timeout=60):
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)

    def _alive(self):
        return bool(self.thread and self.thread.is_alive())

    def running(self):
        """True while the job downloads or builds (its thread is alive)."""
        return self._alive()

    def _not_stopping(self):
        """Refused while a paused or cancelled download thread has not stopped yet: a second thread would write to
        the same partial files."""
        if self._alive() and self.stop_flag.is_set():
            raise Refused(STOPPING)

    def _run(self):
        if self._alive():  # never two download threads on the same files
            raise Refused(STOPPING)
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self._work, name="free-library", daemon=True)
        self.thread.start()

    def _work(self):
        stop = self.stop_flag
        try:
            with self.lock:
                phase = self.state["phase"] if self.state else None
            if phase == "downloading":
                if not self._download_all(stop):
                    return
            with self.lock:
                if stop.is_set() or not self.state:
                    return
                self.state["phase"] = "building"
                self._save()
            result = self.build(self)
            with self.lock:
                if stop.is_set() or not self.state:
                    return
                if result.get("status") == "swapped":
                    self.state.update(phase="done", finished=_now(), problems=[], build=result.get("build"))
                    shutil.rmtree(os.path.join(free_dir(), "files"), ignore_errors=True)  # the library holds them now
                else:
                    self.state.update(phase="build-failed", problems=result.get("problems") or ["the build failed"])
                self._save()
        except Exception as exc:  # never leave the page showing a job that is not running
            with self.lock:
                if self.state:
                    self.state.update(phase="failed", problems=[f"{exc.__class__.__name__}: {exc}"])
                    self._save()

    def _download_all(self, stop):
        """Download every source not done yet. True when all are done."""
        self.catalog = None
        started = time.monotonic()
        with self.lock:
            base = self.snapshot_bytes()
        while True:  # a source retried while the others download is picked up by the next pass
            with self.lock:
                pending = [k for k, i in self.state["sources"].items() if i["status"] == "waiting"]
            if not pending:
                break
            if not self._download_pass(pending, stop, started, base):
                return False
        with self.lock:
            failed = [k for k, i in self.state["sources"].items() if i["status"] != "done"]
            if failed:
                self.state["phase"] = "failed"
                self.state["problems"] = [f"{len(failed)} source(s) could not be downloaded"]
                self._save()
                return False
        return True

    def _download_pass(self, keys, stop, started, base):
        for key in keys:
            with self.lock:
                item = self.state["sources"].get(key)
                if not item or item["status"] != "waiting":
                    continue
                item.update(status="downloading", error=None)
                self._save()
            src = source(key)
            for attempt in range(AUTO_RESUMES + 1):
                try:
                    if src["kind"] == "zim":
                        self._fetch_zim(key, src, stop, started, base)
                    elif src["kind"] == "file":
                        self._fetch_file(key, src, stop, started, base)
                    else:
                        self._fetch_files(key, src, stop, started, base)
                    with self.lock:
                        item.update(status="done", error=None)
                        self._save()
                    break
                except downloader.Stopped:
                    return False
                except downloader.ChecksumError as exc:
                    self._failed(item, f"{exc} The file was deleted; Retry downloads it again.")
                    break
                except FetchError as exc:
                    self._failed(item, f"Refused: {exc}")
                    break
                except downloader.DownloadError as exc:
                    if stop.is_set():
                        return False
                    if attempt == AUTO_RESUMES:
                        self._failed(item, str(exc))
                    else:
                        time.sleep(min(30, 2 ** attempt) if self.connect is None else 0)
            if stop.is_set():
                return False
        return True

    def _failed(self, item, message):
        with self.lock:
            item.update(status="failed", error=message)
            self._save()

    def _progress(self, item, before, started, base):
        def progress(done, total):
            with self.lock:
                item["done"] = before + done
                elapsed = time.monotonic() - started
                now = self.snapshot_bytes()
                if elapsed > 5 and now > base:
                    self.state["speed"] = (now - base) / elapsed
                self._save(force=False)
        return progress

    def snapshot_bytes(self):
        return sum(i.get("done") or 0 for i in self.state["sources"].values())

    def _get_catalog(self, hosts):
        from library import fetch_zims
        if self.catalog is None:
            data = downloader.fetch_bytes(fetch_zims.CATALOG_URL, hosts, max_bytes=MAX_CATALOG, connect=self.connect)
            self.catalog = fetch_zims.parse_catalog(data.decode("utf-8", "replace"))
        return self.catalog

    def _fetch_zim(self, key, src, stop, started, base):
        from library import fetch_zims
        hosts = frozenset(src["hosts"])
        kiwix = src["kiwix"]
        path = self._get_catalog(hosts).get((kiwix["name"], kiwix["flavour"]))
        if not path:
            raise FetchError("Kiwix's catalog no longer offers this source")
        name = path.rsplit("/", 1)[1]
        meta4 = downloader.fetch_bytes(fetch_zims.meta4_url(path), hosts, max_bytes=MAX_META4, connect=self.connect)
        size, sha256 = safe_fetch.parse_meta4(meta4, name)
        folder = files_dir(key)
        dest = os.path.join(folder, name)
        if os.path.isdir(folder):  # an older file of the same source, from a run before the catalog moved on
            for other in os.listdir(folder):
                if not other.startswith(name):
                    os.remove(os.path.join(folder, other))
        with self.lock:
            item = self.state["sources"][key]
            item.update(size=size, file=name, done=downloader.partial_size(dest))
        downloader.fetch(fetch_zims.zim_url(path), dest, hosts, size=size, sha256=sha256, stop=stop.is_set,
                         progress=self._progress(item, 0, started, base), connect=self.connect)
        with self.lock:
            item.update(done=size, sha256=sha256)

    def _fetch_file(self, key, src, stop, started, base):
        """One file with a size and SHA-256 pinned in the source list (wikiHow's CSV at its reviewed commit)."""
        dest = os.path.join(files_dir(key), src["file"])
        with self.lock:
            item = self.state["sources"][key]
            item.update(size=src["size"], file=src["file"], done=downloader.partial_size(dest))
        downloader.fetch(src["url"], dest, frozenset(src["hosts"]), size=src["size"], sha256=src["sha256"],
                         stop=stop.is_set, progress=self._progress(item, 0, started, base), connect=self.connect)
        with self.lock:
            item.update(done=src["size"], sha256=src["sha256"])

    def _fetch_files(self, key, src, stop, started, base):
        hosts = frozenset(src["hosts"])
        folder = files_dir(key)
        os.makedirs(folder, exist_ok=True)
        with self.lock:
            item = self.state["sources"][key]
        got, missed, problems = [], [], []
        for entry in src["files"]:
            dest = os.path.join(folder, entry["file"])
            if not os.path.exists(dest):
                before = sum(os.path.getsize(os.path.join(folder, e["file"])) for e in src["files"]
                             if os.path.exists(os.path.join(folder, e["file"])))
                try:
                    downloader.fetch(entry["url"], dest, hosts, max_bytes=MAX_FILE, stop=stop.is_set,
                                     check=looks_right(entry["file"]), fallback_user_agent=safe_fetch.SAFARI_UA,
                                     progress=self._progress(item, before, started, base), connect=self.connect)
                except (downloader.DownloadError, FetchError) as exc:
                    if stop.is_set():
                        raise downloader.Stopped() from None
                    missed.append(entry["title"])
                    problems.append(exc)
                    continue
            got.append({"file": entry["file"], "title": entry["title"], "url": entry["url"],
                        "license": src["licence_text"]})
        with self.lock:
            item["missed"] = missed
            item["done"] = item["size"] = sum(os.path.getsize(os.path.join(folder, g["file"])) for g in got)
        if not got:
            raise problems[0] if problems else downloader.DownloadError("nothing could be downloaded")
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(got, f, indent=1)


# ---------- the build (in the child process) ----------

def raw_inputs(state):
    """build_library inputs for the downloaded sources in state: {key: {label, topic, origin, fingerprint, docs,
    min_docs}}, with docs converting raw (no cleaning rules)."""
    from library import build_library as bl
    inputs = {}
    for key, item in state["sources"].items():
        src = source(key)
        if not src or item.get("status") != "done":
            continue
        if src["kind"] == "zim":
            prefix = bl.ZIM_KEYS[key]
            _, label, topic, kind, url_prefix = bl.ZIM_SOURCES[prefix]
            path = os.path.join(files_dir(key), item["file"])

            def docs(skip_wikipedia, path=path, key=key, kind=kind, url_prefix=url_prefix):
                return ((t, bl.FIXED_URLS.get(key) or (url_prefix + p if url_prefix else p), x)
                        for t, p, x in bl.zim_docs(path, kind, raw=True))
            inputs[key] = dict(label=label, topic=topic, origin=path, fingerprint=item["sha256"], kind=kind,
                               docs=docs, min_docs=1)
        elif src["kind"] == "file":  # wikiHow's CSV
            path = os.path.join(files_dir(key), item["file"])
            inputs[key] = dict(label="wikiHow", topic="howto", origin=path, fingerprint=item["sha256"], min_docs=1,
                               docs=lambda skip_wikipedia, p=path: bl.wikihow_docs(p, raw=True))
        else:
            label, topic, _ = bl.PDF_SOURCES[key]
            folder = files_dir(key)
            inputs[key] = dict(label=label, topic=topic, origin=folder, fingerprint=bl.folder_fingerprint(folder),
                               docs=lambda skip_wikipedia, f=folder: bl.manual_docs(f, skip_wikipedia, raw=True),
                               min_docs=1)
    return inputs


def probe_word(db_path):
    """A word from the build's first title, to check that a search finds something."""
    import re
    import sqlite3
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT title FROM docs ORDER BY id LIMIT 1").fetchone()
    finally:
        db.close()
    words = re.findall(r"[A-Za-z]{4,}", row[0] if row else "")
    return words[0].lower() if words else None


def check_raw(new_dir, inputs):
    """The problems that keep a raw build from going live: build_library.check_build's integrity, schema, shards and
    per-source checks with every downloaded source holding at least one document, the source-specific searches for
    the sources it has, and a search for a word of its first title. The fixed searches of a full library and the
    comparison with the build before are left out: a free build may hold any few sources."""
    from library import build_library as bl
    word = probe_word(os.path.join(new_dir, "index.sqlite"))
    queries = (word,) if word else ()
    problems = bl.check_build(new_dir, None, queries, {k: 1 for k in inputs}, bl.CHECK_SOURCE_QUERIES)
    if not word:
        problems.append("the build has no titles")
    return problems


def build_raw(state, current=None, libs_dir=None):
    """Build the downloaded sources raw, check the build and swap it in. Returns {"status": "swapped" | "failed",
    "build", "problems", "removed"}. "current" is changed only by a build that passed check_raw()."""
    from library import build_library as bl
    current = current or safe_fetch.CURRENT
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    inputs = raw_inputs(state)
    if not inputs:
        return {"status": "failed", "problems": ["nothing was downloaded"]}
    handle = safe_fetch.take_lock()
    if handle is None:
        return {"status": "failed", "problems": ["the library is being updated by another run; try again later"]}
    try:
        prev = bl.live_build(current, libs_dir)
        try:
            new, _ = bl.build(inputs=inputs, libs_dir=libs_dir, current=current, raw=True)
        except bl.SourceError as exc:
            return {"status": "failed", "problems": [str(exc)]}
        try:
            problems = check_raw(new, inputs)
        except Exception as exc:
            problems = [f"the checks crashed: {exc!r}"]
        if problems:
            kept = os.path.join(libs_dir, bl.FAILED_PREFIX + os.path.basename(new))
            n = 2
            while os.path.lexists(kept):
                kept = os.path.join(libs_dir, f"{bl.FAILED_PREFIX}{os.path.basename(new)}.{n}")
                n += 1
            os.rename(new, kept)
            with open(os.path.join(kept, bl.CHECKS_FAILED), "w", encoding="utf-8") as f:
                json.dump({"failed": bl._now(), "problems": problems, "raw": True}, f, indent=1)
            return {"status": "failed", "problems": problems, "kept": kept}
        with open(os.path.join(new, bl.CHECKS_PASSED), "w", encoding="utf-8") as f:
            json.dump({"passed": bl._now(), "expected": {k: 1 for k in inputs}, "raw": True}, f, indent=1)
        bl.swap_current(new, current)
        removed = bl.tidy_builds(current, libs_dir, previous=prev)
        return {"status": "swapped", "build": new, "problems": [], "removed": removed, "previous": prev}
    finally:
        handle.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["--build"]:
        print("usage: free_library.py --build  (the app runs it; it builds what data.noindex/free/state.json lists)")
        return 2
    with open(state_path(), encoding="utf-8") as f:
        state = json.load(f)
    try:
        result = build_raw(state)
    except Exception as exc:
        result = {"status": "failed", "problems": [f"{exc.__class__.__name__}: {exc}"]}
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "swapped" else 1


if __name__ == "__main__":
    sys.exit(main())
