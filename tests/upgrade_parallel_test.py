"""The upgrade's parallel pack download and its time estimate (upgrade/packs.py: Job._download_files, Speed), against
a stand-in key service on a local socket whose every connection is held to a fixed speed, as a real one is.

Checked:
- files download WORKERS (8) at a time, never more, each one once, every pack together, and each pack installs as
  soon as it is complete (the guides while the library is still coming);
- each worker keeps its connection open: far fewer connections than files;
- a file of SPLIT_MIN bytes or more comes in SEGMENT-sized range requests over several workers, is put together,
  checked against its SHA-256 and installed; a pause keeps its whole segments and Resume asks only for the others; a
  split file whose bytes are wrong is deleted, segments and all; a whole-file partial download left by 1.0.1 counts
  as the segments it covers; a quit after the last segment but before the file was checked and moved (or after the
  move, before it was recorded) is finished on Resume and on Download again; a job is never "done" while a pack
  isn't installed;
- with each connection held to the same speed, four workers finish in well under half the time one worker takes
  (the times are printed);
- a file whose SHA-256 doesn't match stops the download with an error, is deleted, and every file already in stays
  in the "have" list in upgrade-download.json; Resume then downloads only what is missing;
- a pause with four files in flight keeps their partial files, and Resume carries each one on with a range request;
- the estimate: nothing until enough of the download has been seen (a tiny first pack never gives a figure), then
  the bytes left across every pack at the recent, smoothed speed; a stall lowers the speed instead of freezing it;
- the progress figure never goes back: not at a split file's empty full-size .part before its record, not when a
  file is moved into place before it is recorded, nor when a segment lands between two reads (a whole job, sampled
  as often as possible).

A key made here signs the packs; no real key is involved. No test can reach a real host: fake_hosts.guard().

Usage: .venv/bin/python tests/upgrade_parallel_test.py"""
import base64
import hashlib
import http.client
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="upgrade-parallel-")
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"), SURVIVAL_LLM_DATA=os.path.join(TMP, "data"))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
sandbox.require()
import fake_hosts  # noqa: E402
sys.path.insert(0, ROOT)
fake_hosts.guard()
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from library import downloader  # noqa: E402
from upgrade import packs  # noqa: E402

FAILS = []
HOST = "keys.starholm.app"
KEY_TEXT = "SH1-NOT-A-REAL-KEY"  # the stand-in service accepts anything; the app's own check isn't involved here


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        FAILS.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


# packs signed with a key made here
SIGNER = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"upgrade_parallel_test pack key").digest())
packs.PACK_PUBLIC_KEY_BASE64 = base64.b64encode(SIGNER.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def make_pack(name, version, files):
    listed = [{"path": p, "size": len(b), "sha256": sha(b)} for p, b in sorted(files.items())]
    raw = json.dumps({"format": 1, "name": name, "version": version, "size": sum(f["size"] for f in listed),
                      "files": listed}, indent=1).encode()
    return {**files, packs.MANIFEST: raw, packs.SIGNATURE: base64.b64encode(SIGNER.sign(raw)) + b"\n"}


SHARD = 60_000  # smaller than a segment, as the library's files are smaller than its 64 MB segments
packs.SPLIT_MIN, packs.SEGMENT = 600_000, 100_000  # the test's index.sqlite (1.25 MB) is split into 13 segments
BIG = b"SQLite format 3\0" + bytes((j * 7 + j // 997) % 256 for j in range(1_250_000))
GUIDES = make_pack("guides", "2026-11-26", {
    "index.json": json.dumps([{"slug": "water-basics", "title": "Safe water", "group": "Water", "order": 1}]).encode(),
    "water-basics/guide.md": b"# Safe water\n\n## Boil it\n\nBoil water for one minute.\n" + bytes(30_000)})
LIBRARY = make_pack("library", "2026-11-26", {
    "index.sqlite": BIG, "build.json": b'{"built": "2026-11-26"}',
    "checks-passed.json": b'{"ok": true}',
    **{f"text/shard-{i:03d}.txt": bytes(((i * 31 + j) % 251) for j in range(SHARD)) for i in range(12)}})


class Service:
    """POST /v1/download-links and GET /v1/files/<pack>/<path>, each connection sending at most `rate` bytes a second."""

    def __init__(self):
        self.rate = 2_000_000
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.gets = []  # (path, Range)
        self.bad = {}   # path -> bytes served instead
        self.connections = 0
        self.content = {"library": LIBRARY}
        svc = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                exp = int(time.time()) + 3600
                out = [{"pack": name, "files": [{"path": p, "size": len(b), "sha256": sha(b),
                                                 "url": f"https://{HOST}/v1/files/{name}/{p}?exp={exp}&sig=g"}
                                                for p, b in sorted(content.items())]}
                       for name, content in svc.content.items()]
                self._json(200, {"expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)), "packs": out})

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                pack, path = url.path.split("/", 4)[3:5]
                rng = self.headers.get("Range")
                body = svc.bad.get(path, svc.content.get(pack, {}).get(path))
                if body is None:
                    return self._json(404, {"error": "not_found"})
                with svc.lock:
                    svc.gets.append((path, rng))
                    svc.active += 1
                    svc.peak = max(svc.peak, svc.active)
                try:
                    etag = f'"{sha(body)[:16]}"'
                    if self.headers.get("If-Match") and self.headers.get("If-Match") != etag:
                        self.send_response(304)  # as the key service answers R2's failed condition
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    start, end = 0, len(body) - 1
                    if rng and (self.headers.get("If-Range") in (None, etag)):
                        a, _, b = rng[6:].partition("-")
                        start, end = int(a), int(b) if b else len(body) - 1
                        self.send_response(206)
                        self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
                    else:
                        self.send_response(200)
                    part = body[start:end + 1]
                    self.send_header("Content-Length", str(len(part)))
                    self.send_header("ETag", etag)
                    self.end_headers()
                    step = 16_384
                    for i in range(0, len(part), step):
                        self.wfile.write(part[i:i + step])
                        self.wfile.flush()
                        time.sleep(step / svc.rate)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with svc.lock:
                        svc.active -= 1

        class Quiet(http.server.ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 64  # the default 5 resets the eighth worker's connection on macOS

            def handle_error(self, request, client_address):
                pass

        self.server = Quiet(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def connect(self, host, port):
        assert host == HOST, host
        self.connections += 1
        return http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)


svc = Service()
downloader.CONNECT = svc.connect
downloader.CHUNK = 16_384

# How many files the job downloads at once: counted around downloader.fetch(), which handles one file.
FLIGHT = {"now": 0, "peak": 0}
_flight_lock = threading.Lock()


def counted(real):
    def call(*a, **kw):
        with _flight_lock:
            FLIGHT["now"] += 1
            FLIGHT["peak"] = max(FLIGHT["peak"], FLIGHT["now"])
        try:
            return real(*a, **kw)
        finally:
            with _flight_lock:
                FLIGHT["now"] -= 1
    return call


downloader.fetch = counted(downloader.fetch)  # a whole file
downloader.fetch_segment = counted(downloader.fetch_segment)  # one segment of a split file


def fresh(name):
    """A new app folder and data folder for one run (packs.data_dir() reads SURVIVAL_LLM_DATA each time)."""
    app, data = os.path.join(TMP, name, "app"), os.path.join(TMP, name, "data")
    os.makedirs(app)
    os.environ["SURVIVAL_LLM_DATA"] = data
    return app


def run(name, workers):
    app = fresh(name)
    svc.gets = []
    FLIGHT["peak"] = 0
    job = packs.Job(app, service=f"https://{HOST}")
    job.workers = workers
    began = time.monotonic()
    job.start(KEY_TEXT)
    job.wait(120)
    return job, time.monotonic() - began, app


# ---------- 1. eight at a time, every pack together, each file once, connections kept ----------
svc.content = {"guides": GUIDES, "library": LIBRARY}
installed_at = {}
real_installed = packs.Job._installed


def noting(self, name, p, record):
    installed_at[name] = (time.monotonic(), len(svc.gets))
    return real_installed(self, name, p, record)


packs.Job._installed = noting
svc.connections = 0
job, t8, app = run("eight", packs.WORKERS)
packs.Job._installed = real_installed
snap = job.snapshot()
check("the default is eight workers", packs.WORKERS == 8)
check("eight workers: the download finishes and both packs install", snap["phase"] == "done"
      and sorted(packs.installed(app)) == ["guides", "library"], snap)
check("never more than eight downloads at once, and eight at the busiest", FLIGHT["peak"] == 8, FLIGHT["peak"])
paths = [p for p, r in svc.gets if not r]
check("each whole file is downloaded once", sorted(paths) == sorted(p for p in list(GUIDES) + list(LIBRARY)
                                                                     if p != "index.sqlite"), sorted(paths))
check("the guides install while the library is still downloading",
      installed_at["guides"][1] < len(svc.gets) and installed_at["guides"][0] < installed_at["library"][0], installed_at)
check("one progress figure covers both packs", snap["bytes"] == sum(sum(len(b) for b in c.values())
                                                                     for c in (GUIDES, LIBRARY)) and snap["done"] == snap["bytes"])
check("each worker keeps its connection: far fewer connections than requests",
      svc.connections <= packs.WORKERS + 4 and len(svc.gets) > 3 * svc.connections, (svc.connections, len(svc.gets)))
segs = sorted(r for p, r in svc.gets if p == "index.sqlite")
want = [f"bytes={i * packs.SEGMENT}-{min(len(BIG), (i + 1) * packs.SEGMENT) - 1}" for i in range(13)]
check("the big file came as 13 range requests, one per segment", segs == sorted(want), segs)
lib = os.path.join(os.environ["SURVIVAL_LLM_DATA"], "current", "index.sqlite")
check("the split file was put together byte for byte", open(lib, "rb").read() == BIG)
job = packs.Job(fresh("order"), service=f"https://{HOST}")


def listed(content):
    return [{"path": p, "size": len(b), "sha256": sha(b)} for p, b in sorted(content.items())]


job.state = {"phase": "paused", "packs": [{"pack": "library", "files": listed(LIBRARY), "have": [], "installed": False},
                                          {"pack": "guides", "files": listed(GUIDES), "have": [], "installed": False}]}
order = [(t[0], t[2]["path"], t[3]) for t in job._tasks(["guides", "library"])]
check("the queue: the guides' files first, then the big file's segments, then the rest largest first",
      [o[0] for o in order[:len(GUIDES)]] == ["guides"] * len(GUIDES)
      and [o[1] for o in order[len(GUIDES):len(GUIDES) + 13]] == ["index.sqlite"] * 13, order[:20])
svc.content = {"library": LIBRARY}

# ---------- 1b. four workers, for the timing below ----------
job, t4, app = run("four", 4)
check("four workers: the download finishes", job.snapshot()["phase"] == "done")
check("never more than four at once", FLIGHT["peak"] == 4, FLIGHT["peak"])

# ---------- 2. the time against one worker, each connection at the same speed ----------
job1, t1, _ = run("one", 1)
check("one worker: the same download finishes", job1.snapshot()["phase"] == "done")
check("one worker never downloads more than one file at a time", FLIGHT["peak"] == 1, FLIGHT["peak"])
print(f"     content bytes {sum(len(b) for b in LIBRARY.values()):,}, each connection held to "
      f"{svc.rate / 1e6:.1f} MB/s: one worker {t1:.2f} s, four workers {t4:.2f} s ({t1 / t4:.1f} times as fast)")
check("four workers take well under half the time of one", t4 < t1 / 2, (t1, t4))

# ---------- 3. a wrong file: an error, the file deleted, the rest kept; Resume fetches only what is missing ----------
app = fresh("bad")
svc.gets = []
bad_path = "text/shard-005.txt"
svc.bad[bad_path] = b"x" * len(LIBRARY[bad_path])
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
job.wait(120)
snap = job.snapshot()
staging = os.path.join(packs.incoming_dir(), "library")
saved = json.load(open(os.path.join(app, packs.STATE_FILE)))
have = saved["packs"][0]["have"]
check("a file whose SHA-256 doesn't match stops the download with an error",
      snap["phase"] == "error" and "SHA-256" in (snap["error"] or ""), snap)
check("the wrong file is deleted, partial file and all", not os.path.exists(os.path.join(staging, bad_path))
      and downloader.partial_size(os.path.join(staging, bad_path)) == 0)
check("files already in stay in the saved have list", bad_path not in have and len(have) >= 2, have)
check("the saved list is kept per pack in upgrade-download.json", saved["packs"][0]["pack"] == "library")
del svc.bad[bad_path]
before = list(have)
svc.gets = []
job.resume(KEY_TEXT)
job.wait(120)
again = [p for p, _ in svc.gets]
check("Resume finishes the download", job.snapshot()["phase"] == "done", job.snapshot())
check("Resume downloads only what was missing", not set(again) & set(before) and bad_path in again, (before, again))

# ---------- 3b. a split file whose bytes are wrong ----------
app = fresh("badsplit")
svc.bad["index.sqlite"] = bytes(len(BIG))
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
job.wait(120)
snap = job.snapshot()
big = os.path.join(packs.incoming_dir(), "library", "index.sqlite")
check("a split file whose bytes are wrong stops the download with an error",
      snap["phase"] == "error" and "SHA-256" in (snap["error"] or ""), snap)
check("...and is deleted, segments record and all", not any(os.path.exists(big + x) for x in ("", ".part", ".segments.json")))
del svc.bad["index.sqlite"]

# ---------- 3c. a whole-file partial download left by 1.0.1 counts as the segments it covers ----------
app = fresh("legacy")
staging = os.path.join(packs.incoming_dir(), "library")
os.makedirs(staging)
big = os.path.join(staging, "index.sqlite")
with open(big + ".part", "wb") as f:
    f.write(BIG[:350_000])  # segments 0, 1 and 2 whole, 3 in part
with open(big + ".part.json", "w") as f:
    json.dump({"url": "library/index.sqlite", "size": len(BIG), "sha256": sha(BIG), "validator": f'"{sha(BIG)[:16]}"'}, f)
svc.gets = []
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
job.wait(120)
asked = sorted(r for p, r in svc.gets if p == "index.sqlite")
check("the old partial file's whole segments aren't asked for again", job.snapshot()["phase"] == "done"
      and len(asked) == 10 and "bytes=0-99999" not in asked and "bytes=300000-399999" in asked, asked)
check("...and the file is right", open(os.path.join(os.environ["SURVIVAL_LLM_DATA"], "current", "index.sqlite"), "rb").read() == BIG)

# ---------- 3d. stopped after the last segment, before the file was checked and moved into place ----------
def stopped_after_segments(name, part_bytes=None, moved=False):
    """A job folder as a quit leaves it when every segment was recorded but the whole file wasn't checked and moved
    (moved: the move happened, the have list wasn't saved). Returns (app folder, the file's path)."""
    app = fresh(name)
    staging = os.path.join(packs.incoming_dir(), "library")
    os.makedirs(staging)
    big = os.path.join(staging, "index.sqlite")
    with open(big if moved else big + ".part", "wb") as f:
        f.write(BIG if part_bytes is None else part_bytes)
    if not moved:
        json.dump({"size": len(BIG), "sha256": sha(BIG), "done": list(range(13)), "validator": None},
                  open(big + ".segments.json", "w"))
    return app, big


def installed_right(app):
    lib = os.path.join(os.environ["SURVIVAL_LLM_DATA"], "current", "index.sqlite")
    return packs.installed(app).get("library") is not None and os.path.isfile(lib) and open(lib, "rb").read() == BIG


app, big = stopped_after_segments("all-segments-in")
svc.gets = []
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
job.wait(60)
check("every segment in but the file not moved: Resume checks and moves it, and the library installs",
      job.snapshot()["phase"] == "done" and installed_right(app), job.snapshot())
check("...without asking for any segment again", not [r for p, r in svc.gets if p == "index.sqlite"], svc.gets)

app, big = stopped_after_segments("all-segments-wrong", part_bytes=bytes(len(BIG)))
svc.gets = []
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
job.wait(60)
check("every segment in but the bytes wrong: the file starts again from nothing and installs right",
      job.snapshot()["phase"] == "done" and installed_right(app)
      and len([r for p, r in svc.gets if p == "index.sqlite"]) == 13, (job.snapshot()["phase"], len(svc.gets)))

app, big = stopped_after_segments("moved-not-recorded", moved=True)
svc.gets = []
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
job.wait(60)
check("moved into place but not recorded: Resume finds it whole and installs, asking for nothing of it",
      job.snapshot()["phase"] == "done" and installed_right(app)
      and not [r for p, r in svc.gets if p == "index.sqlite"], (job.snapshot()["phase"], svc.gets))

app, big = stopped_after_segments("download-again")
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT, again=True)
job.wait(60)
check("Download again over a stale all-segments record installs the library", job.snapshot()["phase"] == "done"
      and installed_right(app), job.snapshot())

app = fresh("never-done-incomplete")
job = packs.Job(app, service=f"https://{HOST}")
job._install_if_complete = lambda name: None  # stands in for an install that silently didn't happen
job.start(KEY_TEXT)
job.wait(60)
snap = job.snapshot()
check("the job is never 'done' while a pack isn't in place", snap["phase"] == "error"
      and "incomplete" in (snap["error"] or ""), snap)

# ---------- 4. a pause with four files in flight, then Resume with range requests ----------
app = fresh("pause")
svc.gets = []
svc.rate = 400_000
job = packs.Job(app, service=f"https://{HOST}")
job.workers = 4
job.start(KEY_TEXT)
end = time.time() + 30
seg_file = os.path.join(packs.incoming_dir(), "library", "index.sqlite.segments.json")


def segments_in():
    try:
        return len(json.load(open(seg_file))["done"])
    except (OSError, ValueError, KeyError):
        return 0


while time.time() < end and not (FLIGHT["now"] >= 4 and segments_in() >= 2):  # segments go first
    time.sleep(0.01)
job.pause()
job.wait(30)
staging = os.path.join(packs.incoming_dir(), "library")
partial = {p: downloader.partial_size(os.path.join(staging, p)) for p in LIBRARY if p != "index.sqlite"}
in_flight = {p: n for p, n in partial.items() if n}
split_done = json.load(open(os.path.join(staging, "index.sqlite.segments.json")))["done"]
check("pause: the job is paused", job.snapshot()["phase"] == "paused", job.snapshot())
check("pause: what was in flight is kept (whole segments, partial files)", len(split_done) >= 2, (split_done, partial))
svc.rate = 4_000_000
svc.gets = []
job.resume(KEY_TEXT)
job.wait(120)
ranged = {p: r for p, r in svc.gets if r and p != "index.sqlite"}
again = [r for p, r in svc.gets if p == "index.sqlite"]
check("Resume finishes", job.snapshot()["phase"] == "done", job.snapshot())
check("Resume carries each partial file on from its last byte",
      all(ranged.get(p) == f"bytes={n}-" for p, n in in_flight.items()), (in_flight, ranged))
check("pause kept the split file's whole segments, and Resume asked only for the others",
      split_done and len(again) == 13 - len(split_done)
      and not any(r.startswith(f"bytes={i * packs.SEGMENT}-") for i in split_done for r in again), (split_done, again))

# ---------- 5. the estimate ----------
now = [0.0]
s = packs.Speed(clock=lambda: now[0])
check("no figure before any byte", s.rate() is None)
for _ in range(10):  # the guides: 350 KB in a second
    now[0] += 0.1
    s.add(35_000)
check("no figure from a tiny first pack", s.rate() is None)
check("so no time left either", packs.seconds_left(16_000_000_000, s.rate()) is None)
for _ in range(100):  # then the library at 5 MB a second for 10 seconds
    now[0] += 0.1
    s.add(500_000)
r = s.rate()
check("after enough of the download, the speed is the recent one", r and 4_000_000 < r < 5_500_000, r)
left = packs.seconds_left(16_000_000_000, r)
check("the time left is every byte left at that speed (16 GB at about 5 MB/s: about an hour)",
      left and 2900 < left < 4000, left)
for _ in range(60):  # the next 30 s at 5 MB a second, looked at every half second
    for _ in range(5):
        now[0] += 0.1
        s.add(500_000)
    s.rate()
steady = s.rate()
for _ in range(20):  # a 10-second stall, looked at every half second
    now[0] += 0.5
    s.rate()
stalled = s.rate()
check("a stall lowers the speed instead of freezing it, and still gives a figure",
      stalled is not None and stalled < steady * 0.9, (steady, stalled))
check("the figure moves smoothly: a stall doesn't drop it to nothing at once", stalled > steady * 0.3, (steady, stalled))
check("nothing left: no time left", packs.seconds_left(0, 5e6) is None)

# a real job: right after the start the page gets no figure, never an absurd one
app = fresh("estimate")
svc.rate = 2_000_000
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
seen = []
while job.running():
    snap = job.snapshot()
    if snap and snap["phase"] == "downloading":
        seen.append(snap["seconds_left"])
    time.sleep(0.05)
check("a short download never shows a time left it can't back up", seen and all(x is None for x in seen), seen[:5])

# ---------- the progress figure never goes back ----------
# The moments between two steps of one file, which the page can land on.
app = fresh("bar")
job = packs.Job(app, service=f"https://{HOST}")
listed_lib = listed(LIBRARY)
job.state = {"phase": "downloading", "packs": [{"pack": "library", "files": listed_lib, "have": [], "installed": False}]}
staging = os.path.join(packs.incoming_dir(), "library")
big_f = next(f for f in listed_lib if f["path"] == "index.sqlite")
big_dest = os.path.join(staging, "index.sqlite")
os.makedirs(staging, exist_ok=True)
with open(big_dest + ".part", "wb") as out:
    out.truncate(len(BIG))  # _prepare_segments() has made the empty full-size file, not yet its record
check("a split file's empty .part before its segments record counts nothing", job.snapshot()["done"] == 0,
      job.snapshot()["done"])
packs._prepare_segments(big_dest, big_f)
check("then its record says no segment is in: still nothing", job.snapshot()["done"] == 0, job.snapshot()["done"])
os.replace(big_dest + ".part", big_dest)  # moved into place, its record not yet deleted (_finish_split())
real_isfile = os.path.isfile
os.path.isfile = lambda q: True if q == big_dest + ".part" else real_isfile(q)  # it was there a moment ago
try:
    moved = job.snapshot()["done"]
except OSError as exc:
    moved = repr(exc)
finally:
    os.path.isfile = real_isfile
check("a split file moved into place before its record is deleted counts whole, and the page's view doesn't fail",
      moved == len(BIG), moved)
os.remove(big_dest)
os.remove(big_dest + ".segments.json")
open(os.path.join(staging, "build.json"), "wb").write(LIBRARY["build.json"])  # moved into place, not yet recorded
check("a file moved into place counts whole before it joins the have list",
      job.snapshot()["done"] == len(LIBRARY["build.json"]), job.snapshot()["done"])
# a 1.0.1 partial download of the big file still counts as the bytes it has
open(big_dest + ".part", "wb").write(BIG[:300_000])
json.dump({"url": "library/index.sqlite", "size": len(BIG), "sha256": sha(BIG), "validator": '"x"'},
          open(big_dest + ".part.json", "w"))
check("a 1.0.1 partial download of a split file counts the bytes it has",
      job.snapshot()["done"] == len(LIBRARY["build.json"]) + 300_000, job.snapshot()["done"])

# a whole job, looked at as often as possible: the figure only ever grows
app = fresh("bar-run")
svc.rate = 8_000_000
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
dones = []
while job.running():
    snap = job.snapshot()
    if snap:
        dones.append(snap["done"])
job.wait(5)
dones.append(job.snapshot()["done"])
back = [(a, b) for a, b in zip(dones, dones[1:]) if b < a]
check(f"over {len(dones)} looks at a download, the progress figure never goes back", job.snapshot()["phase"] == "done"
      and not back, back[:5])

svc.server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print("all parallel download checks passed" if not FAILS else f"FAILED: {len(FAILS)} checks")
sys.exit(1 if FAILS else 0)
