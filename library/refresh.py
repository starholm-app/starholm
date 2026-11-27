"""The content refresh: fetch metadata, download what changed, build, check, swap. The one entry point for the
weekly job (ops/launchd/) and for a first full build.

Usage: .venv/bin/python library/refresh.py [--dry-run] [--rebuild KEY ...] [--only KEY ...]
       .venv/bin/python library/refresh.py --maps
       .venv/bin/python library/refresh.py --pending
       .venv/bin/python library/refresh.py --accept PATH --sha256 HASH | --accept all --sha256-file FILE
       .venv/bin/python library/refresh.py --recheck data.noindex/libs/.failed-lib-<date>

1. Read the Kiwix catalog and each ZIM's .meta4 checksum; download only ZIMs with no verified local copy, and
   verify each one (fetch_zims.py).
2. Fetch the manuals and risk tables and settle them against the hash lock (fetch_manuals.py, fetch_risk.py). A
   changed file is kept back for review and the previous copy stays in use.
3. Build only the sources whose origin changed into libs/lib-<date>/, check it, and swap "current" to it
   (build_library.py). A failed check leaves "current" alone.
4. Once a good build is live (swapped in, or nothing changed), delete the ZIMs and the wikiHow CSV it was
   converted from (build_library.retire_originals()). A later run downloads one again only when its catalog entry
   or its converter version changes, or --rebuild names its source.

5. Bring the offline maps the person chose up to date (maps/fetch.py): download the newest file of each, check
   its SHA-256, move it in, keep one older copy at most. Map problems go to maps/status.json for the Maps tab, not
   into this run's result.

Maps on their own: the app's "Download for offline use" leaves a request in data.noindex/maps/ and starts this
script with --maps (the installed LaunchAgent's own command line, plus --maps), which does only the maps step. A run
without --maps is always the full refresh, which ends with the maps step (and picks up a request made while it ran).

--dry-run fetches only the catalog and the .meta4 files, and lists what would be downloaded and rebuilt.
--only KEY ... limits a run (or a dry run) to those sources: only their ZIMs are looked up and downloaded, only they
can be rebuilt, and every other source is carried over as it is, even one with a newer file upstream. It skips the
steps that concern other sources: wikiHow, the manuals' and risk tables' downloads (a folder source in KEY is built
from the copy on disk), the warning about sources gone from the catalog, removing superseded ZIMs, removing sources
switched off, and the maps. The five zimgit PDF collections go together (one copy of a PDF they share is kept).
--recheck runs the build checks again on a build that failed them (kept as libs/.failed-<name>) and swaps it in
if they now pass, so a fixed or adjusted check does not cost another conversion.
--dry-run and --pending also say how much disk the library uses, and how much a converter change would download.
--pending lists the PDFs and risk tables waiting for review; --accept moves reviewed ones into use, each only with
the SHA-256 of the copy that was reviewed, since a newer download may replace a held copy (the next
refresh rebuilds their source).

It runs at low priority from start to end (nice 10 and throttled disk I/O, passed on to every child process).
Only one run at a time (a lock file in the data folder). Everything is logged to logs/refresh.log in the checkout.
Exit code 0 means it finished: nothing changed (a no-op) or the new build was swapped in. 1 means something failed.
A macOS notification is posted only on a failure, or when a PDF or risk table changed and needs review.

At the end of every full run (and --recheck), whatever its result, it calls emergency.save_detected_country() (the
module at the checkout's root, from the app's side: no arguments, no network, never raises). A missing module or
any error there only writes a WARNING line to the log.

Status file. A full run and --recheck write refresh-status.json beside the log (logs/refresh-status.json in the
checkout; STATUS_PATH), for the app's Status page. It is replaced atomically (written to a temporary file in the
same folder, then renamed), so a reader always sees a whole file. Its shape (version 1):

    {
      "version": 1,
      "pid": 12345,                       # the refresh process; with "running", check it is still alive
      "state": "running" | "finished",
      "started": "2026-11-15T03:00:12Z",  # UTC, ISO 8601
      "finished": null | "2026-11-15T05:41:03Z",
      "result": null | "updated" | "no-change" | "failed",
          # null while running. "updated": a new build was swapped in and nothing failed. "no-change": the live
          # library was already current and nothing failed. "failed": something failed (a download, a fetch, the
          # build or its checks); the live library is then whatever it was before, unless "changed" is not empty
          # (a new build was swapped in but another step, such as one manual's download, failed).
      "changed": ["medlineplus", ...],    # build keys of the sources the swapped-in build converted again; [] if none
      "problems": ["ZIM ...: download or check failed", ...],   # at most 10 lines, each at most 300 characters
      "progress": null | {                # null once finished
          "step": "catalog" | "download" | "fetch" | "build" | "check" | "tidy" | "maps",
          "text": "Downloading MedlinePlus (NIH), 2.1 of 3.4 GB",   # one line, ready to show
          "source": "medlineplus" | null, # the build key the step is about, when there is one
          "done": 2100000000 | null,      # bytes so far (download steps only)
          "total": 3400000000 | null      # bytes in all (download steps only)
      }
    }

During a download the byte counts are refreshed every few seconds from the size of the file being written. A
"running" file whose pid is not running any more is a run that was killed: read it as failed.
"""
import datetime
import itertools
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import build_library, fetch_manuals, fetch_risk, fetch_wikihow, fetch_zims, oscompat, safe_fetch, titles  # noqa: E402
cleanup = titles.cleanup()  # library/cleanup.py, or plain stand-ins where it is absent (library/titles.py)
from maps import fetch as maps_fetch  # noqa: E402

LOG_PATH = os.environ.get("SURVIVAL_LLM_REFRESH_LOG") or os.path.join(safe_fetch.LOG_DIR, "refresh.log")
NOTIFY_FAILED = "The library refresh failed. See logs/refresh.log in the survival-llm folder."
NOTIFY_MISSING = "{n} library source(s) are no longer offered by Kiwix. The copies already built stay in use."
NOTIFY_CLOUD = "Ollama's cloud features are not switched off. See logs/refresh.log."
MISSING_STATE = safe_fetch.MISSING_SOURCES
OLLAMA_STATUS = "http://127.0.0.1:11434/api/status"
NOTIFY_REVIEW = "{n} library file(s) changed at the source and need review before use. Run refresh.py --pending."
STATUS_PATH = os.path.join(os.path.dirname(LOG_PATH), "refresh-status.json")
STATUS_EVERY = 5  # seconds between byte-count updates while a file downloads
MAX_PROBLEMS = 10


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Status:
    """refresh-status.json (see the module docstring for its shape). Writing it never fails the refresh: an error
    only costs the Status page its update."""

    def __init__(self, path=None):
        self.path = path or STATUS_PATH
        self.lock = threading.Lock()
        self.data = {"version": 1, "pid": os.getpid(), "state": "running", "started": _utc(), "finished": None,
                     "result": None, "changed": [], "problems": [], "progress": None}

    def write(self):
        with self.lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = f"{self.path}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=1)
                os.replace(tmp, self.path)
            except OSError as exc:
                print(f"WARNING: status file not written: {exc}", flush=True)

    def progress(self, step, text, source=None, done=None, total=None):
        self.data["progress"] = {"step": step, "text": text, "source": source, "done": done, "total": total}
        self.write()

    def watch(self, path, step, verb, label, source, total):
        """A context manager: while it is open, a thread refreshes the progress of step from path's size every
        STATUS_EVERY seconds ("<verb> <label>, 2.1 of 3.4 GB")."""
        status = self

        class Watch:
            def __enter__(self):
                self.stop = threading.Event()
                self.update()
                self.thread = threading.Thread(target=self.loop, daemon=True)
                self.thread.start()
                return self

            def update(self):
                try:
                    done = os.path.getsize(path)
                except (OSError, TypeError):
                    done = 0
                done = min(done, total) if total else done
                text = f"{verb} {label}" + (f", {_gb_of(done, total)}" if total else "")
                status.progress(step, text, source, done, total)

            def loop(self):
                while not self.stop.wait(STATUS_EVERY):
                    self.update()

            def __exit__(self, *exc):
                self.stop.set()
                self.thread.join(timeout=STATUS_EVERY + 1)
                return False
        return Watch()

    def finish(self, result, changed=(), problems=()):
        self.data.update(state="finished", finished=_utc(), result=result, changed=sorted(changed),
                         problems=[str(p)[:300] for p in list(problems)[:MAX_PROBLEMS]], progress=None)
        self.write()


def _gb_of(done, total):
    """ "2.1 of 3.4 GB" (or MB for small files)."""
    if total >= 1e9:
        return f"{done / 1e9:.1f} of {total / 1e9:.1f} GB"
    return f"{done / 1e6:.0f} of {total / 1e6:.0f} MB"


def save_detected_country():
    """Call emergency.save_detected_country() (the app's module at the checkout's root). Its contract: no
    arguments, no network, never raises. A missing module or any error only writes a warning to the log."""
    try:
        import emergency
    except Exception as exc:
        print(f"WARNING: emergency numbers not saved: the emergency module did not load ({exc!r})", flush=True)
        return False
    try:
        emergency.save_detected_country()
    except Exception as exc:
        print(f"WARNING: emergency numbers not saved: {exc!r}", flush=True)
        return False
    return True


class Tee:
    """Send everything printed to the terminal (or launchd's log) and to the log file."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.stream.write(text)
        self.log.write(text)
        return len(text)

    def flush(self):
        self.stream.flush()
        self.log.flush()


def start_log():
    # The log names the files a run fetched and the checkout's paths: only this user may read it (folder 700, file
    # 600), as the app's own log already is, so another account on the computer can't.
    # A folder or log this user may not change (another account's) is a warning in the log, never a stopped refresh.
    folder = os.path.dirname(LOG_PATH)
    warnings = []
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if os.path.realpath(folder) == os.path.realpath(safe_fetch.LOG_DIR):  # never a folder SURVIVAL_LLM_REFRESH_LOG names
        try:
            os.chmod(folder, 0o700)
        except OSError as exc:
            warnings.append(f"Warning: couldn't make {folder} private (mode 700): {exc.strerror or exc}")
    fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)  # a log written before this change was 644
    except OSError as exc:
        warnings.append(f"Warning: couldn't make {LOG_PATH} private (mode 600): {exc.strerror or exc}")
    log = os.fdopen(fd, "a", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log)
    sys.stderr = Tee(sys.__stderr__, log)
    stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    print(f"\n===== library refresh {stamp}: {' '.join(sys.argv[1:]) or '(full run)'}", flush=True)
    for warning in warnings:
        print(warning, flush=True)


NICE = 10
IOPOL_TYPE_DISK, IOPOL_SCOPE_PROCESS, IOPOL_THROTTLE = 0, 0, 3  # <sys/resource.h>


def stay_low_priority():
    """Run the whole refresh in the background: nice at least NICE, and disk I/O throttled. Both pass to every child
    process (curl, pdftotext, the build's workers), so the Mac stays responsive while someone works on it. The
    launchd job sets the same; this covers a run started by hand. Returns (nice, io policy set). On Windows (a build
    the app starts) the process runs below normal priority instead, and the disk policy is left alone."""
    if sys.platform == "win32":
        oscompat.lower_priority()
        return NICE, False
    current = os.nice(0)
    if current < NICE:
        current = os.nice(NICE - current)
    io_ok = False
    try:
        import ctypes
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        io_ok = libc.setiopolicy_np(IOPOL_TYPE_DISK, IOPOL_SCOPE_PROCESS, IOPOL_THROTTLE) == 0
    except (OSError, AttributeError):
        pass
    return current, io_ok


def notify(message):
    """A macOS notification. The text is one of the fixed messages above (with a number at most), never anything
    read from the network, so nothing can be injected into the AppleScript."""
    script = 'display notification "{}" with title "Survival library"'.format(message.replace('"', "'"))
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def zim_key(item):
    """The build key for a planned ZIM, from its file name (wikem_en_all_nopic_2026-10.zim -> "wikem"). The file
    name, not the catalog name, matches build_library.ZIM_SOURCES: Gardenology's catalog name is gardenology_en_all
    but its files are gardenology.org_en_all_*."""
    entry = build_library.ZIM_SOURCES.get(fetch_zims.file_prefix(item.get("file")))
    return entry[0] if entry else None


def held_for(since):
    """How long a file has been held, in words ("3 days, since 2026-11-12 12:26 +04")."""
    try:
        start = datetime.datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return "unknown"
    seconds = max(0, (datetime.datetime.now(datetime.timezone.utc) - start).total_seconds())
    if seconds < 3600:
        span = f"{int(seconds // 60)} minutes"
    elif seconds < 2 * 86400:
        span = f"{int(seconds // 3600)} hours"
    else:
        span = f"{int(seconds // 86400)} days"
    return f"{span}, since {start.astimezone().strftime('%Y-%m-%d %H:%M %z')}"


def print_pending(lock):
    pending = lock.get("pending", {})
    for rel, info in sorted(pending.items()):
        if info.get("reason"):
            why = info["reason"]
        elif info.get("previous"):
            why = "changed at the source since the copy in use was recorded"
        elif info.get("from_fallback"):
            why = "first copy, and it came only from the Internet Archive"
        elif info.get("redirected_to"):
            why = f"first copy, and the official address redirected to {info['redirected_to']}"
        else:
            why = "first copy"
        held = held_for(info.get("held_since") or info.get("seen"))
        replaced = info.get("replaced") or []
        if replaced:
            print(f"needs review: {rel}\n"
                  f"    REPLACED: a newer download replaced the held copy {len(replaced)} time(s), last at "
                  f"{replaced[-1].get('at')}; review the copy held now (its hash is below)", flush=True)
        else:
            print(f"needs review: {rel}", flush=True)
        print(f"    held:     {held}\n"
              f"    why:      {why}\n"
              f"    source:   {info.get('url')}\n"
              f"    held at:  {os.path.join(safe_fetch.PENDING_DIR, rel)}\n"
              f"    size:     {info.get('size'):,} bytes, sha256 {info.get('sha256')} (seen {info.get('seen')})\n"
              f"    in use:   {'sha256 ' + info['previous'] if info.get('previous') else 'nothing yet'}\n"
              f"    accept:   .venv/bin/python library/refresh.py --accept {rel} "
              f"--sha256 {info.get('sha256', '')[:16]}", flush=True)
    if not pending:
        print("nothing waiting for review", flush=True)
    return len(pending)


def _usage(path, seen):
    """Bytes on disk under path (allocated blocks), counting a file hard-linked from several builds once."""
    total = 0
    paths = [path] if os.path.isfile(path) else []
    for folder, _, names in os.walk(path):
        paths += [os.path.join(folder, n) for n in names]
    for p in paths:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if (st.st_dev, st.st_ino) not in seen:
            seen.add((st.st_dev, st.st_ino))
            total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size  # Windows: no blocks
    return total


def _gb(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def disk_report(items=None):
    """Print how much disk the data folder uses, part by part, and what a converter change would download again.
    items: the ZIM plan, when the catalog was read (its sizes stand in for originals with no record)."""
    seen = set()
    live = build_library.live_build()
    parts = []
    if live:
        parts.append((f"live build {os.path.basename(live)}", _usage(live, seen)))
    libs = safe_fetch.LIBS_DIR
    for name in sorted(os.listdir(libs)) if os.path.isdir(libs) else []:
        path = os.path.join(libs, name)
        if os.path.islink(path) or not os.path.isdir(path) or os.path.realpath(path) == live:
            continue
        what = "failed build" if name.startswith(build_library.FAILED_PREFIX) else \
            "unfinished build" if name.startswith(".staging-") else "previous build"
        parts.append((f"{what} {name}", _usage(path, seen)))
    zims = _usage(safe_fetch.ZIM_DIR, seen) if os.path.isdir(safe_fetch.ZIM_DIR) else 0
    zim_files = len([n for n in os.listdir(safe_fetch.ZIM_DIR) if n.endswith(".zim")]) if zims else 0
    parts.append((f"ZIMs ({zim_files} files)", zims))
    parts.append(("wikiHow CSV", _usage(fetch_wikihow.dest(), seen)))
    parts.append(("held for review", _usage(safe_fetch.PENDING_DIR, seen)))
    parts.append(("PDFs and risk tables", _usage(safe_fetch.RAW_DIR, seen)))
    rest = _usage(safe_fetch.DATA_DIR, seen) if os.path.isdir(safe_fetch.DATA_DIR) else 0
    total = sum(n for _, n in parts) + rest
    shown = [f"{w} {_gb(n)}" for w, n in parts if n] + ([f"other {_gb(rest)}"] if rest > 1e8 else [])
    print(f"disk: the data folder uses {_gb(total)}" + (": " + ", ".join(shown) if shown else ""), flush=True)
    # What a converter change would download again: the originals of the live build's sources, by the converter
    # entry that covers them (build_library.CONVERTER: a kind of ZIM, or one source), with what is not on disk now.
    manifest = build_library.read_manifest(live)
    ledger = build_library.load_originals()
    catalog = {i["file"]: i.get("size") for i in items or () if i.get("file")}
    groups = {}
    for key, entry in manifest.items():
        kind = build_library.source_kind(key)
        if kind is None and key != "wikihow":
            continue
        origin = entry.get("origin") or ""
        path = fetch_wikihow.dest() if key == "wikihow" else os.path.join(safe_fetch.ZIM_DIR, origin)
        on_disk = os.path.isfile(path)
        if on_disk:
            size = os.path.getsize(path)
        elif ledger.get(key, {}).get("file") == origin:
            size = ledger[key].get("size") or 0
        else:
            size = catalog.get(origin) or 0
        groups.setdefault("wikiHow" if key == "wikihow" else f"{kind} ZIMs", []).append((size, key, on_disk))
    if not groups:
        return
    lines = []
    for group, members in sorted(groups.items(), key=lambda g: -sum(m[0] for m in g[1])):
        size = sum(m[0] for m in members)
        biggest = max(members)
        lines.append(f"{group} ({len(members)}) {_gb(size)}"
                     + (f", of which {biggest[1]} {_gb(biggest[0])}" if len(members) > 1 and biggest[0] else ""))
    every = sum(m[0] for g in groups.values() for m in g)
    gone = sum(m[0] for g in groups.values() for m in g if not m[2])
    print("disk: a converter change downloads the originals it covers: " + "; ".join(lines)
          + f"; every source {_gb(every)}, of which {_gb(gone)} is not on disk now", flush=True)


def zim_names(keys):
    """The Kiwix catalog names (fetch_zims.SOURCES) of the ZIM sources with these build keys."""
    names = set()
    for name, flavour in fetch_zims.SOURCES:
        prefix = name + (f"_{flavour}" if flavour else "")
        entry = build_library.ZIM_SOURCES.get(build_library.CATALOG_FILE_PREFIX.get(prefix, prefix))
        if entry and entry[0] in keys:
            names.add(name)
    return names


def plan_zims(force, only=None):
    """fetch_zims.plan() for every ZIM source, or with only (a set of build keys) for those alone ([] when none of
    them is a ZIM source)."""
    if not only:
        return fetch_zims.plan(force=force)
    names = zim_names(only)
    return fetch_zims.plan(wanted=names, force=force) if names else []


def only_inputs(only=None):
    """build_library.source_inputs(), or with only just those sources' inputs: the build carries every other source
    over from the live build."""
    inputs = build_library.source_inputs()
    return {k: v for k, v in inputs.items() if k in only} if only else inputs


def dry_run(force, only=None):
    """List what a run would do. Fetches only the catalog and .meta4 files; downloads no content."""
    if only:
        print("only: " + ", ".join(sorted(only)) + " (every other source is carried over as it is)", flush=True)
    items = plan_zims(force, only)
    for item in items:
        print("zim       " + fetch_zims.describe(item), flush=True)
    wikihow = {"status": "skipped"}
    if only:
        print("wikihow   not looked at (--only)", flush=True)
        print("manuals and risk tables: not downloaded (--only); a folder source named is built from its copy on "
              "disk", flush=True)
    else:
        wikihow = fetch_wikihow.plan(force=force)
        print("wikihow   " + fetch_wikihow.describe(wikihow), flush=True)
        print(f"would re-check {len(fetch_manuals.MANUALS)} manuals and {len(fetch_risk.DOCUMENTS)} risk tables "
              "against the hash lock (a real run downloads them to compare)", flush=True)
    print_pending(safe_fetch.load_lock())
    inputs = only_inputs(only)
    changed = build_library.changed_sources(inputs, build_library.read_manifest(build_library.previous_build()),
                                            force)
    downloads = [i for i in items if i["status"] == "download"]
    changed |= {k for k in (zim_key(i) for i in downloads) if k}
    if downloads:
        print(f"would download: {len(downloads)} ZIMs, {sum(i.get('size') or 0 for i in downloads):,} bytes "
              f"({_gb(sum(i.get('size') or 0 for i in downloads))})", flush=True)
    if wikihow["status"] == "download":
        changed.add("wikihow")
    print("would rebuild: " + (", ".join(build_library.build_order(changed)) or "nothing"), flush=True)
    disk_report(items)
    ollama_cloud_check(dry=True)
    print("dry run: no content downloaded, nothing built", flush=True)
    return 0


def prune_zims(items):
    """Delete ZIMs neither the live build came from nor the catalog names now (a partial download of the current
    file is kept, so the next run resumes it)."""
    keep = {v.get("origin") for v in build_library.read_manifest(build_library.previous_build()).values()}
    keep |= {i["file"] for i in items if i.get("file")}
    folder = fetch_zims.zim_dir()
    if not os.path.isdir(folder):
        return
    for name in sorted(os.listdir(folder)):
        base = name.removesuffix(".verified")
        if not safe_fetch.ZIM_NAME_RE.match(base) or base in keep:
            continue
        print(f"removing superseded {name}", flush=True)
        os.remove(os.path.join(folder, name))


def newly_missing(items):
    """Sources the Kiwix catalog no longer lists that were not missing on the last run (state in the data folder).
    A missing source is a warning, not a failure: its copy on disk and in the live build stays in use."""
    now = sorted(f"{i['name']} {i.get('flavour') or ''}".strip() for i in items if i["status"] == "missing")
    try:
        with open(MISSING_STATE, encoding="utf-8") as f:
            before = set(json.load(f))
    except (OSError, ValueError, TypeError):
        before = set()
    tmp = MISSING_STATE + ".tmp"
    os.makedirs(os.path.dirname(MISSING_STATE), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(now, f)
    os.replace(tmp, MISSING_STATE)
    for name in now:
        print(f"WARNING: {name} is no longer in the Kiwix catalog; its existing copy stays in use", flush=True)
    return [n for n in now if n not in before]


def ollama_cloud_check(dry=False):
    """Ask the local Ollama whether its cloud features are off (loopback only, no proxy, a short timeout). Notify if
    it answers and cloud.disabled is not true. Never a failure of the refresh: no answer just gets a log line."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(OLLAMA_STATUS, timeout=3) as r:
            status = json.loads(r.read(65536))
    except Exception as exc:
        print(f"ollama: no status answer ({exc.__class__.__name__}); cloud check skipped", flush=True)
        return None
    cloud = status.get("cloud") if isinstance(status, dict) else None
    disabled = isinstance(cloud, dict) and cloud.get("disabled") is True
    print(f"ollama: cloud features {'off' if disabled else 'NOT off'} ({json.dumps(cloud)[:200]})", flush=True)
    if not disabled and not dry:
        notify(NOTIFY_CLOUD)
    return disabled


def source_label(key):
    """The name a source is shown under ("MedlinePlus (NIH)"), or the key itself."""
    for prefix, entry in build_library.ZIM_SOURCES.items():
        if entry[0] == key:
            return entry[1]
    if key in build_library.PDF_SOURCES:
        return build_library.PDF_SOURCES[key][0]
    return "wikiHow" if key == "wikihow" else str(key)


LOCK_RETRY = (2, 5)  # seconds between tries, tries: the app may be checking the lock for a moment (maps.library_busy)
MAPS_WAIT = (60, 180)  # seconds between tries, tries: a full run waits up to three hours for a maps-only run to end


def take_lock_patiently(maps_only=False, sleep=time.sleep):
    """The refresh lock, or None. Tried a few times over a few seconds. A full run (not --maps) whose lock is held by a
    maps-only run started from the app keeps waiting, up to three hours, rather than being skipped for a week; it ends
    with the maps step itself. A maps-only run never waits longer: the run holding the lock does the maps at its end."""
    step, tries = LOCK_RETRY
    for n in range(tries):
        lock = safe_fetch.take_lock()
        if lock is not None or n == tries - 1:
            break
        sleep(step)
    if lock is not None or maps_only:
        return lock
    step, tries = MAPS_WAIT
    for _ in range(tries):
        if not maps_fetch.maps.job_status()["running"]:
            break
        print("a map download holds the lock; waiting for it to finish", flush=True)
        sleep(step)
        lock = safe_fetch.take_lock()
        if lock is not None:
            return lock
    return safe_fetch.take_lock()


def run_maps():
    """The maps step, run again while the app asks for more maps during it (at most three times). Returns the number
    of maps that failed. Never raises: a map problem must not fail the library refresh."""
    failed = 0
    for _ in range(3):
        try:
            failed = maps_fetch.run()
        except Exception as exc:  # noqa: BLE001
            print(f"maps: FAILED: {exc!r}", flush=True)
            failed = 1
        if not maps_fetch.requested():
            break
    return failed


def run(force, status=None, only=None):
    """A full refresh, or with only (a set of build keys) one limited to those sources (see --only). Returns the exit
    code. status: a Status to report progress and the result to."""
    status = status or Status()
    status.write()
    result = {"status": "failed", "changed": []}
    failures, review = [], []
    try:
        return _run(force, status, failures, review, result, only)
    except Exception as exc:
        failures.append(f"refresh: {exc!r}")
        raise
    finally:
        failed = bool(failures) or result.get("status") == "failed"
        status.finish("failed" if failed else "updated" if result.get("status") == "swapped" else "no-change",
                      result.get("changed") or (), failures)
        save_detected_country()


def _run(force, status, failures, review, result_out, only=None):
    if only:
        print("only: " + ", ".join(sorted(only)) + " (every other source is carried over as it is)", flush=True)
    status.progress("catalog", "Reading the Kiwix catalog")
    items = plan_zims(force, only)
    for item in items:
        print("zim       " + fetch_zims.describe(item), flush=True)
        if item["status"] not in ("current", "built", "missing", "off"):
            key = zim_key(item)
            dest = os.path.join(fetch_zims.zim_dir(), item.get("file") or "")
            with status.watch(dest, "download", "Downloading", source_label(key), key, item.get("size")):
                ok = fetch_zims.download_zim(item)
            if not ok:
                failures.append(f"ZIM {item.get('file') or item['name']}: "
                                f"{item.get('error') or 'download or check failed'}")
    gone = newly_missing(items) if not only else []  # a partial plan says nothing about the other sources
    if only:
        print("wikiHow, manuals and risk tables: not downloaded (--only)", flush=True)
    else:
        status.progress("fetch", "Checking wikiHow, the manuals and the risk tables")
        _fetch_rest(force, failures, review)
    status.progress("build", "Building the library (converting what changed, then checking it)")
    try:
        result = build_library.build_and_swap(force, inputs=only_inputs(only), only=only) if only else \
            build_library.build_and_swap(force)
    except Exception as exc:
        result = {"status": "failed", "problems": [f"build: {exc!r}"], "changed": []}
    return _finish(items, result, result_out, status, failures, review, gone, only)


def _fetch_rest(force, failures, review):
    """wikiHow, the manuals and the risk tables: download what changed (a changed file is held for review)."""
    try:
        wikihow = fetch_wikihow.plan(force=force)
        print("wikihow   " + fetch_wikihow.describe(wikihow), flush=True)
        result = fetch_wikihow.fetch(wikihow, safe_fetch.load_lock())
        review += result["pending"]
        failures += [f"fetch_wikihow: {title}" for title in result["failed"]]
    except Exception as exc:
        failures.append(f"fetch_wikihow: {exc!r}")
    for module in (fetch_manuals, fetch_risk):
        try:
            result = module.main()
        except Exception as exc:  # a broken source must not stop the build of everything else
            failures.append(f"{module.__name__}: {exc!r}")
            continue
        review += result["pending"]
        failures += [f"{module.__name__}: {title}" for title in result["failed"]]


def _finish(items, result, result_out, status, failures, review, gone, only=None):
    result_out.update(result)
    status.progress("tidy", "Tidying up")
    if result["status"] == "failed":
        failures += result["problems"]
        print("build NOT swapped in; the live library is unchanged", flush=True)
    elif result["status"] == "swapped":
        print(f"swapped in {result['build']} (rebuilt: {', '.join(result['changed'])}; "
              f"removed: {', '.join(result['removed']) or 'none'})", flush=True)
    else:
        print("nothing changed: the live library is up to date", flush=True)
    if result["status"] != "failed":
        retire(result)
    if result["status"] != "failed" and all(i["status"] != "error" for i in items) and not only:
        prune_zims(items)  # only with a full, fresh view of the catalog
    disk_report(items)
    for problem in failures:
        print(f"FAILED: {problem}", flush=True)
    for rel in review:
        print(f"NEEDS REVIEW: {rel}", flush=True)
    if failures:
        notify(NOTIFY_FAILED)
    elif review:
        notify(NOTIFY_REVIEW.format(n=len(review)))
    if gone:
        notify(NOTIFY_MISSING.format(n=len(gone)))
    ollama_cloud_check()
    if not only:
        status.progress("maps", "Updating the offline maps")
        run_maps()
    return 1 if failures else 0


def retire(result):
    """After a good run (a build swapped in, or nothing to change): the builds to keep, and the originals the live
    build no longer needs. Never a failure of the refresh: an error here only leaves files on disk."""
    try:
        # build_and_swap tidies the builds itself, on a swap and on a no-op (see build_library.tidy_builds()).
        if result.get("removed") and result["status"] == "noop":
            print(f"removed builds: {', '.join(result['removed'])}", flush=True)
        deleted = build_library.retire_originals()
        if deleted:
            print(f"deleted {len(deleted)} original(s) the live build holds, "
                  f"{_gb(sum(n for _, n in deleted))} freed", flush=True)
    except Exception as exc:
        print(f"WARNING: originals not deleted: {exc!r}", flush=True)


def read_sha256_file(path):
    """{path under raw/: reviewed sha256} from lines "<sha256>  <path>" (the shape sha256sum prints)."""
    reviewed = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and not line.lstrip().startswith("#"):
                reviewed[parts[1].strip().lstrip("*")] = parts[0].lower()
    return reviewed


def accept(names, sha256=None, sha256_file=None):
    """Accept held files, each only with the hash of the copy that was reviewed: one path with --sha256, or any
    number (or "all") with --sha256-file. Without them, list what is held and accept nothing."""
    lock = safe_fetch.load_lock()
    if names == ["all"]:
        names = sorted(lock["pending"])
    not_held = [rel for rel in names if rel not in lock["pending"]]
    for rel in not_held:
        print(f"nothing pending for {rel}", flush=True)
    names = [rel for rel in names if rel in lock["pending"]]
    if not names:
        return 1 if not_held else 0
    reviewed = read_sha256_file(sha256_file) if sha256_file else {}
    if sha256:
        if len(names) != 1:
            print("--sha256 goes with one path; for several use --sha256-file", flush=True)
            return 1
        reviewed[names[0]] = sha256
    missing = [rel for rel in names if rel not in reviewed]
    if missing:
        print("nothing accepted: each file needs the hash of the copy you reviewed. Held now:", flush=True)
        for rel in names:
            info = lock["pending"].get(rel, {})
            print(f"{info.get('sha256', '?')}  {rel}    ({info.get('size', 0):,} bytes)", flush=True)
        print("Save the lines you reviewed to a file and run --accept all --sha256-file FILE, or accept one with "
              "--accept PATH --sha256 HASH.", flush=True)
        return 1
    bad = 0
    for rel in names:
        if rel in lock["pending"]:
            print_pending({"pending": {rel: lock["pending"][rel]}})  # what is being accepted, on the record
        try:
            ok = safe_fetch.accept_pending(lock, rel, sha256=reviewed[rel])
            if ok:
                safe_fetch.save_lock(lock)  # after each file: a later error never leaves a moved file unrecorded
            print(("accepted " if ok else "nothing pending for ") + rel, flush=True)
        except safe_fetch.FetchError as exc:
            print(f"not accepted: {exc}", flush=True)
            ok = False
        bad += not ok
    safe_fetch.save_lock(lock)
    return 1 if bad or not_held else 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    build_library.set_workers(args)  # "--workers N": conversion processes per ZIM (default 4)
    known = {"--dry-run", "--rebuild", "--only", "--pending", "--accept", "--recheck", "--sha256", "--sha256-file",
             "--maps"}
    flags = [a for a in args if a.startswith("--")]
    if any(f not in known for f in flags):
        sys.exit("usage: refresh.py [--workers N] [--dry-run] [--rebuild KEY ...] [--only KEY ...] | --maps | --pending "
                 "| --accept PATH ... | --accept all | --recheck PATH")
    recheck_path = None
    if "--recheck" in args:
        rest = [a for a in args[args.index("--recheck") + 1:] if not a.startswith("--")]
        if len(rest) != 1:
            sys.exit("--recheck takes one folder: a failed build kept as data.noindex/libs/.failed-lib-<date>")
        recheck_path = rest[0]
    force = build_library.parse_rebuild(args)
    only = build_library.parse_rebuild(args, "--only")
    if only and (force - only):
        sys.exit("--rebuild names sources --only leaves out: " + ", ".join(sorted(force - only)))
    group = set(cleanup.PDF_GROUP)
    if only and only & group and not group <= only:
        # the build keeps one copy of a PDF the zimgit collections share (build_library.dedupe_pdf_group()): it needs
        # all of them converted the same way, or a shared PDF can end up twice
        sys.exit("--only takes the zimgit collections together: " + ", ".join(sorted(group)))
    if only and any(a in args for a in ("--maps", "--pending", "--accept", "--recheck")):
        sys.exit("--only goes with a run or --dry-run only")
    nice, io_ok = stay_low_priority()
    start_log()
    print(f"low priority: nice {nice}, disk I/O {'throttled' if io_ok else 'NOT throttled'}", flush=True)
    lock = take_lock_patiently(maps_only="--maps" in args)
    if lock is None:
        print("another refresh or build is already running; leaving it alone", flush=True)
        return 0
    try:
        if recheck_path:
            status = Status()
            status.progress("check", "Re-checking a build that failed its checks")
            result = {"status": "failed", "problems": ["the re-check crashed"]}
            try:
                result = build_library.recheck(recheck_path)
                if result["status"] == "swapped":
                    print(f"swapped in {result['build']} (removed: {', '.join(result['removed']) or 'none'})",
                          flush=True)
                    retire(result)
                elif result["status"] == "refused":
                    print("not re-checked: " + "; ".join(result["problems"]), flush=True)
            finally:
                status.finish("updated" if result["status"] == "swapped" else "failed",
                              problems=result.get("problems") or ())
                save_detected_country()
            return 0 if result["status"] == "swapped" else 1
        if "--pending" in args:
            print_pending(safe_fetch.load_lock())
            disk_report()
            return 0
        if "--accept" in args:
            names = list(itertools.takewhile(lambda a: not a.startswith("--"), args[args.index("--accept") + 1:]))
            if not names:
                sys.exit("--accept takes paths as --pending shows them, or 'all'")

            def value(flag):
                if flag not in args:
                    return None
                after = args[args.index(flag) + 1:]
                if not after or after[0].startswith("--"):
                    sys.exit(f"{flag} needs a value")
                return after[0]
            return accept(names, sha256=value("--sha256"), sha256_file=value("--sha256-file"))
        if "--dry-run" in args:
            return dry_run(force, only)
        if "--maps" in args:
            print("maps only: downloading the maps asked for", flush=True)
            return 1 if run_maps() else 0
        return run(force, only=only)
    except Exception as exc:
        print(f"FAILED: {exc!r}", flush=True)
        if "--dry-run" not in args:
            notify(NOTIFY_FAILED)
        return 1
    finally:
        print("===== done", flush=True)


if __name__ == "__main__":
    sys.exit(main())
