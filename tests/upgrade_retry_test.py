"""The upgrade download's retries after a dropped connection (upgrade/packs.py: Job._fetch, Job._fetch_segment,
DROP_DELAYS; library/downloader.py: ConnectionDropped), against a stand-in key service on a local socket that resets
the connection of chosen files partway through, a set number of times.

Checked:
- a whole file whose connection is reset mid-file once, then twice, finishes without anyone pressing anything: each
  retry is a range request from the byte the last one stopped at, so no byte is downloaded twice;
- the same for one segment of a split file: the retry asks for the rest of that segment only;
- the retries wait longer each time (DROP_DELAYS), and while they wait the job stays "downloading" with no error and
  the progress figure never goes back;
- a file whose connection is reset every time stops the download with the connection error only after the last
  retry (one try plus one per DROP_DELAYS entry); every other file came in meanwhile and is kept, and so is its
  partial file; Resume then finishes it with a range request;
- a pause during a retry's wait pauses the download at once;
- a 500, 502, 503 or 504 answer is retried the same way, and the download finishes; one every time errors after
  the last retry; a 404 is not retried.

A key made here signs the packs; no real key is involved. No test can reach a real host: fake_hosts.guard().

Usage: .venv/bin/python tests/upgrade_retry_test.py"""
import base64
import hashlib
import http.client
import http.server
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="upgrade-retry-")
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
KEY_TEXT = "SH1-NOT-A-REAL-KEY"


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        FAILS.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


SIGNER = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"upgrade_retry_test pack key").digest())
packs.PACK_PUBLIC_KEY_BASE64 = base64.b64encode(SIGNER.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def make_pack(name, version, files):
    listed = [{"path": p, "size": len(b), "sha256": sha(b)} for p, b in sorted(files.items())]
    raw = json.dumps({"format": 1, "name": name, "version": version, "size": sum(f["size"] for f in listed),
                      "files": listed}, indent=1).encode()
    return {**files, packs.MANIFEST: raw, packs.SIGNATURE: base64.b64encode(SIGNER.sign(raw)) + b"\n"}


SHARD = 200_000
packs.SPLIT_MIN, packs.SEGMENT = 600_000, 100_000  # the test's index.sqlite (800 kB) comes in 8 segments
packs.DROP_DELAYS = (0.3, 0.6, 0.9, 1.2)  # the real ones are seconds; the order and the growth are what is checked
BIG = b"SQLite format 3\0" + bytes((j * 7 + j // 997) % 256 for j in range(800_000 - 16))
LIBRARY = make_pack("library", "2026-11-26", {
    "index.sqlite": BIG, "build.json": b'{"built": "2026-11-26"}', "checks-passed.json": b'{"ok": true}',
    **{f"text/shard-{i:03d}.txt": bytes(((i * 31 + j) % 251) for j in range(SHARD)) for i in range(6)}})


class Service:
    """POST /v1/download-links and GET /v1/files/<pack>/<path>. drops[(path, Range or None)] or drops[path] = n: the
    next n answers for it send headers and part of the body, then reset the connection (an RST, as a dropped
    network gives)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.gets = []  # (path, Range, time)
        self.drops = {}
        self.served = {}  # path -> body bytes actually sent
        self.answers = {}  # path -> HTTP statuses to answer first, one per request, before the file is served
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
                out = [{"pack": "library", "files": [{"path": p, "size": len(b), "sha256": sha(b),
                                                      "url": f"https://{HOST}/v1/files/library/{p}?exp={exp}&sig=g"}
                                                     for p, b in sorted(LIBRARY.items())]}]
                self._json(200, {"expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)), "packs": out})

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path.split("/", 4)[4]
                rng = self.headers.get("Range")
                body = LIBRARY.get(path)
                if body is None:
                    return self._json(404, {"error": "not_found"})
                with svc.lock:
                    svc.gets.append((path, rng, time.monotonic()))
                    status = svc.answers[path].pop(0) if svc.answers.get(path) else None
                if status:
                    return self._json(status, {"error": "server"})
                with svc.lock:
                    drop = False
                    for k in ((path, rng), path):
                        if svc.drops.get(k, 0) > 0:
                            svc.drops[k] -= 1
                            drop = True
                            break
                etag = f'"{sha(body)[:16]}"'
                start, end = 0, len(body) - 1
                if rng and self.headers.get("If-Range") in (None, etag):
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
                send = part[:len(part) // 2] if drop else part
                try:
                    for i in range(0, len(send), 16_384):
                        self.wfile.write(send[i:i + 16_384])
                        self.wfile.flush()
                        time.sleep(0.002)
                except (BrokenPipeError, ConnectionResetError):
                    return
                with svc.lock:
                    svc.served[path] = svc.served.get(path, 0) + len(send)
                if drop:  # reset, not a clean close: SO_LINGER 0 sends an RST, once the app has read what came
                    time.sleep(0.2)
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self.close_connection = True
                    self.connection.close()

        class Quiet(http.server.ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 64

            def handle_error(self, request, client_address):
                pass

        self.server = Quiet(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def connect(self, host, port):
        assert host == HOST, host
        return http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)

    def of(self, path):
        return [(r, t) for p, r, t in self.gets if p == path]


svc = Service()
downloader.CONNECT = svc.connect
downloader.CHUNK = 16_384


def fresh(name):
    app, data = os.path.join(TMP, name, "app"), os.path.join(TMP, name, "data")
    os.makedirs(app)
    os.environ["SURVIVAL_LLM_DATA"] = data
    svc.gets, svc.served = [], {}
    return app


def watch(job, seconds=60):
    """Run the job to the end, sampling the page's view of it: [(phase, error, done)]."""
    seen = []
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        s = job.snapshot()
        seen.append((s["phase"], s["error"], s["done"]))
        if not job.running():
            break
        time.sleep(0.02)
    job.wait(5)
    s = job.snapshot()
    seen.append((s["phase"], s["error"], s["done"]))
    return seen


def never_back(seen):
    done = [d for _, _, d in seen]
    return all(b >= a for a, b in zip(done, done[1:]))


SHARD1, SHARD2 = "text/shard-001.txt", "text/shard-002.txt"

# ---------- 1. whole files reset once and twice: the download finishes by itself ----------
app = fresh("whole")
svc.drops = {SHARD1: 1, SHARD2: 2}
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
seen = watch(job)
snap = job.snapshot()
check("files whose connection is reset once and twice: the download finishes and the pack installs",
      snap["phase"] == "done" and "library" in packs.installed(app), snap)
check("no error is shown at any point while the retries run", all(e is None for _, e, _ in seen)
      and {p for p, _, _ in seen} <= {"asking", "checking", "downloading", "installing", "done"},
      [x for x in seen if x[1]])
check("the progress figure never goes back during the retries", never_back(seen), [d for *_, d in seen])
one, two = svc.of(SHARD1), svc.of(SHARD2)


def froms(asked):
    return [int(r[6:].split("-")[0]) if r else 0 for r, _ in asked]


# The service sends half of what is asked, then resets. The app keeps every whole chunk it wrote (downloader.CHUNK);
# the chunk it was reading when the reset came is lost, so each retry starts at most one chunk short of that half.
c = downloader.CHUNK
f1, f2 = froms(one), froms(two)
check("a file reset once is asked for twice: the whole file, then a range from where it stopped",
      len(f1) == 2 and f1[0] == 0 and SHARD // 2 - c < f1[1] <= SHARD // 2, one)
check("a file reset twice is asked for three times, each range from where the last one stopped",
      len(f2) == 3 and f2[0] == 0 and SHARD // 2 - c < f2[1] <= SHARD // 2
      and f2[1] + (SHARD - f2[1]) // 2 - c < f2[2] <= f2[1] + (SHARD - f2[1]) // 2, two)
check("the bytes already in are not downloaded again (at most the chunk cut by each reset)",
      SHARD <= svc.served.get(SHARD1, 0) < SHARD + c and SHARD <= svc.served.get(SHARD2, 0) < SHARD + 2 * c,
      {k: svc.served.get(k) for k in (SHARD1, SHARD2)})
gap1, gap2 = two[1][1] - two[0][1], two[2][1] - two[1][1]
check("the retries wait, and longer each time", gap1 >= packs.DROP_DELAYS[0] - 0.05
      and gap2 >= packs.DROP_DELAYS[1] - 0.05 and gap2 > gap1, (gap1, gap2))
check("the library installed byte for byte",
      open(os.path.join(os.environ["SURVIVAL_LLM_DATA"], "current", SHARD2), "rb").read() == LIBRARY[SHARD2])

# ---------- 2. one segment of a split file reset mid-segment: the retry asks for the rest of that segment ----------
app = fresh("segment")
seg3 = f"bytes={3 * packs.SEGMENT}-{4 * packs.SEGMENT - 1}"
svc.drops = {("index.sqlite", seg3): 1}
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
seen = watch(job)
snap = job.snapshot()
check("a split file with a segment reset mid-way: the download finishes", snap["phase"] == "done", snap)
ranges = [r for r, _ in svc.of("index.sqlite")]
seg_start, seg_end = 3 * packs.SEGMENT, 4 * packs.SEGMENT - 1
retry = [r for r in ranges if r.endswith(f"-{seg_end}") and r != seg3]
retry_from = int(retry[0][6:].split("-")[0]) if len(retry) == 1 else -1
check("the reset segment is asked for again from where it stopped, to the segment's end only",
      ranges.count(seg3) == 1 and len(retry) == 1 and len(ranges) == 9
      and seg_start + packs.SEGMENT // 2 - c < retry_from <= seg_start + packs.SEGMENT // 2, ranges)
check("the segment's bytes already in are not downloaded again (at most the chunk cut by the reset)",
      len(BIG) <= svc.served.get("index.sqlite", 0) < len(BIG) + c, svc.served.get("index.sqlite"))
check("no error shown and the progress never goes back", all(e is None for _, e, _ in seen) and never_back(seen),
      [x for x in seen if x[1]])
check("the split file was put together byte for byte",
      open(os.path.join(os.environ["SURVIVAL_LLM_DATA"], "current", "index.sqlite"), "rb").read() == BIG)

# ---------- 3. a file reset every time: the error only after the last retry, everything kept ----------
app = fresh("always")
svc.drops = {SHARD1: 1000}
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
seen = watch(job)
snap = job.snapshot()
tries = svc.of(SHARD1)
check("a file reset every time stops the download with the connection error",
      snap["phase"] == "error" and "connection dropped" in (snap["error"] or ""), snap)
check(f"it was tried {1 + len(packs.DROP_DELAYS)} times (once, then once per retry) before the error",
      len(tries) == 1 + len(packs.DROP_DELAYS), len(tries))
first_error = next((i for i, (ph, e, _) in enumerate(seen) if ph == "error" or e), None)
check("the error appears only after the last try, never between retries",
      first_error is not None and all(ph != "error" and e is None for ph, e, _ in seen[:first_error]), seen[-5:])
waits = [b - a for (_, a), (_, b) in zip(tries, tries[1:])]
check("each retry waits longer than the one before", all(b > a for a, b in zip(waits, waits[1:]))
      and waits[-1] >= packs.DROP_DELAYS[-1] - 0.05, waits)
staging = os.path.join(packs.incoming_dir(), "library")
have = json.load(open(os.path.join(app, packs.STATE_FILE)))["packs"][0]["have"]
check("the partial file is kept for Resume", downloader.partial_size(os.path.join(staging, SHARD1)) > 0)
check("every other file came in while that one retried, and stays in the have list",
      sorted(have) == sorted(p for p in LIBRARY if p != SHARD1), have)
svc.drops = {}
svc.gets = []
job.resume(KEY_TEXT)
job.wait(60)
check("Resume then finishes it, asking only for the rest of that file",
      job.snapshot()["phase"] == "done" and [p for p, _, _ in svc.gets] == [SHARD1]
      and svc.of(SHARD1)[0][0] not in (None, "bytes=0-"), svc.gets)

# ---------- 4. a pause during a retry's wait pauses at once ----------
app = fresh("pause")
packs.DROP_DELAYS = (5, 5, 5, 5)
svc.drops = {SHARD1: 1000}
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
until = time.monotonic() + 30
while time.monotonic() < until and len(svc.of(SHARD1)) < 1:
    time.sleep(0.02)
time.sleep(0.5)  # the first try has dropped; the retry is waiting its 5 s
began = time.monotonic()
job.pause()
job.wait(10)
check("a pause during a retry's wait pauses the download within a second",
      job.snapshot()["phase"] == "paused" and time.monotonic() - began < 1.5,
      (job.snapshot()["phase"], time.monotonic() - began))
packs.DROP_DELAYS = (0.3, 0.6, 0.9, 1.2)

# ---------- 4b. server errors (500, 502, 503, 504) are retried as dropped connections are; a 404 is not ----------
app = fresh("server-errors")
svc.drops = {}
svc.answers = {SHARD1: [503, 502], SHARD2: [500, 504]}
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
seen = watch(job)
check("a file answered 503 then 502, and another 500 then 504: the download finishes by itself",
      job.snapshot()["phase"] == "done" and len(svc.of(SHARD1)) == 3 and len(svc.of(SHARD2)) == 3,
      (job.snapshot(), len(svc.of(SHARD1)), len(svc.of(SHARD2))))
check("no error shown while the server errors were retried", all(e is None for _, e, _ in seen), seen[-3:])
app = fresh("server-down")
svc.answers = {SHARD1: [503] * 1000}
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
watch(job)
check(f"a file answered 503 every time errors after {1 + len(packs.DROP_DELAYS)} tries, with the status",
      job.snapshot()["phase"] == "error" and "503" in (job.snapshot()["error"] or "")
      and len(svc.of(SHARD1)) == 1 + len(packs.DROP_DELAYS), (job.snapshot()["error"], len(svc.of(SHARD1))))
app = fresh("not-found")
svc.answers = {SHARD1: [404] * 1000}
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
watch(job)
check("a 404 is not retried: one try, then the error", job.snapshot()["phase"] == "error"
      and len(svc.of(SHARD1)) == 1, (job.snapshot()["error"], len(svc.of(SHARD1))))
svc.answers = {}
check("which errors are retried", packs.transient(downloader.ConnectionDropped("x"))
      and all(packs.transient(downloader.DownloadError("x", c)) for c in (500, 502, 503, 504))
      and not any(packs.transient(downloader.DownloadError("x", c)) for c in (400, 403, 404, 410, 412, 416, 501))
      and not packs.transient(downloader.ChecksumError("x")) and not packs.transient(downloader.DownloadError("x")))

# ---------- 5. an HTTP error is not a dropped connection: no retry ----------
err = None
try:
    downloader.fetch(f"https://{HOST}/v1/files/library/missing.txt?sig=g", os.path.join(TMP, "missing.txt"),
                     frozenset({HOST}))
except downloader.DownloadError as exc:
    err = exc
check("a 404 is a DownloadError with its status, not a ConnectionDropped (so the job doesn't retry it)",
      err is not None and err.status == 404 and not isinstance(err, downloader.ConnectionDropped), repr(err))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
