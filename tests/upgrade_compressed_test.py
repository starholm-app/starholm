"""Compressed packs, pack.json format 2 (docs/packs.md, "Format 2"; upgrade/packs.py, library/downloader.py
fetch_frames), against a stand-in key service on a local socket that lists the v2/ packs to an app that asks for
format 2.

Checked:
- the app asks for format 2; a format-2 guides pack and library pack download several files at once, each unpacked
  as it arrives, are checked against the signed pack.json and installed byte for byte in today's layout; the split
  index.sqlite comes as one range request per segment, each a run of whole frames;
- the progress bar counts the unpacked bytes and never goes back;
- a connection reset mid-file, in a whole file and in a segment, is retried from the frame it stopped in, and the
  download finishes by itself; a pause keeps the unpacked bytes and Resume asks only for the frames after them;
- a partial file left by a format-1 download carries on as format 2;
- wrong compressed bytes (a frame that doesn't unpack, or unpacks to another length) stop the download with an
  error and nothing is installed; a pack.json whose frames don't add up, or whose frame size doesn't divide a
  segment, is refused; a service that answers with format-1 packs still works.

A key made here signs the packs; no real key is involved. No test can reach a real host: fake_hosts.guard().

Usage: .venv/bin/python tests/upgrade_compressed_test.py"""
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
TMP = tempfile.mkdtemp(prefix="upgrade-compressed-")
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"), SURVIVAL_LLM_DATA=os.path.join(TMP, "data"))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
sandbox.require()
import fake_hosts  # noqa: E402
sys.path.insert(0, ROOT)
fake_hosts.guard()
import zstandard  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from library import downloader  # noqa: E402
from upgrade import packs  # noqa: E402

FAILS = []
HOST = "keys.starholm.app"
KEY_TEXT = "SH1-NOT-A-REAL-KEY"


def check(name, ok, detail=""):
    if not ok and isinstance(detail, dict) and detail.get("error"):  # the whole error first: it names the file
        detail = f"error: {detail['error']} | {detail}"
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {str(detail)[:1500]}"), flush=True)
    if not ok:
        FAILS.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


SIGNER = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"upgrade_compressed_test pack key").digest())
packs.PACK_PUBLIC_KEY_BASE64 = base64.b64encode(SIGNER.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
packs.SPLIT_MIN, packs.SEGMENT = 600_000, 100_000  # index.sqlite (1 MB) comes in 10 segments
packs.DROP_DELAYS = (0.2, 0.4, 0.6, 0.8)
FRAME = 25_000  # four frames a segment


def compress(raw, frame=FRAME):
    """(stored bytes, frames) as packbuilder/build.py writes them: independent zstd frames of `frame` bytes."""
    parts = [zstandard.ZstdCompressor(level=3, write_content_size=True).compress(raw[i:i + frame])
             for i in range(0, len(raw), frame)]
    return b"".join(parts), [len(p) for p in parts]


def make_pack(name, version, files, fmt=2, frame=FRAME, tamper=None):
    """{"raw": {path: bytes}, "stored": {path: bytes as served}, pack.json and pack.sig included}. tamper(manifest)
    may change pack.json before it is signed."""
    listed, stored = [], {}
    for path, raw in sorted(files.items()):
        entry = {"path": path, "size": len(raw), "sha256": sha(raw)}
        z, frames = compress(raw, frame)
        if fmt == 2 and len(z) < len(raw):
            entry["stored"] = {"size": len(z), "sha256": sha(z), "frames": frames}
            stored[path] = z
        else:
            stored[path] = raw
        listed.append(entry)
    m = {"format": fmt, "name": name, "version": version, "size": sum(f["size"] for f in listed), "files": listed}
    if fmt == 2:
        m["frame"] = frame
    if tamper:
        tamper(m)
    raw_m = json.dumps(m, indent=1).encode()
    special = {packs.MANIFEST: raw_m, packs.SIGNATURE: base64.b64encode(SIGNER.sign(raw_m)) + b"\n"}
    return {"raw": dict(files), "stored": {**stored, **special}, "manifest": m}


import random  # noqa: E402

WORDS = ("water boil filter iodine bleach splint pressure wound shelter fire knot signal mirror ration salt sugar "
         "fever burn cold heat map radio battery tarp rope stove fuel latrine soap gauze tourniquet").split()


def prose(seed, n):
    """n bytes of text that compresses about as the library's does (a third or so), the same every run."""
    rnd = random.Random(seed)
    out = bytearray()
    while len(out) < n:
        out += (" ".join(rnd.choice(WORDS) for _ in range(rnd.randint(6, 14))).capitalize() + ". ").encode()
        out += str(rnd.randint(0, 99999)).encode() + b" "
    return bytes(out[:n])


TEXT = {f"text/shard-{i:03d}.txt": prose(i, 150_000 + i * 997) for i in range(6)}
BIG = b"SQLite format 3\0" + prose("index", 1_000_000 - 16)
LIB_FILES = {"index.sqlite": BIG, "build.json": b'{"built": "2026-11-26"}', "checks-passed.json": b'{"ok": true}',
             **TEXT}
GUIDE_FILES = {"index.json": json.dumps([{"slug": "water-basics", "title": "Safe water", "group": "Water",
                                          "order": 1}]).encode(),
               "water-basics/guide.md": b"# Safe water\n\n" + prose("guide", 20_000)}
GUIDES = make_pack("guides", "2026-11-26", GUIDE_FILES)
LIBRARY = make_pack("library", "2026-11-26", LIB_FILES)


class Service:
    """POST /v1/download-links (format 2 when asked and self.v2, else self.v1) and GET /v1/files/<top>/<path>.
    drops[path] or drops[(path, Range)] = n: the next n answers send half the body, then reset."""

    def __init__(self):
        self.lock = threading.Lock()
        self.gets, self.asked, self.drops, self.served = [], [], {}, {}
        self.v2 = {"guides": GUIDES, "library": LIBRARY}
        self.v1 = None
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
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                svc.asked.append(body.get("format"))
                exp = int(time.time()) + 3600
                two = body.get("format") == 2 and svc.v2
                served, top = (svc.v2, "v2/") if two else (svc.v1, "")
                out = [{"pack": name, "files": [{"path": p, "size": len(b), "sha256": sha(b),
                                                 "url": f"https://{HOST}/v1/files/{top}{name}/{p}?exp={exp}&sig=g"}
                                                for p, b in sorted(pk["stored"].items())]}
                       for name, pk in served.items()]
                self._json(200, {"expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)), "packs": out})

            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path).path.split("/")[3:]
                served = svc.v1
                if parts[0] == "v2":
                    parts, served = parts[1:], svc.v2
                pack, path = parts[0], "/".join(parts[1:])
                body = (served or {}).get(pack, {}).get("stored", {}).get(path)
                if body is None:
                    return self._json(404, {"error": "not_found"})
                rng = self.headers.get("Range")
                with svc.lock:
                    svc.gets.append((path, rng))
                    drop = False
                    for k in ((path, rng), path):
                        if svc.drops.get(k, 0) > 0:
                            svc.drops[k] -= 1
                            drop = True
                            break
                etag = f'"{sha(body)[:16]}"'
                if self.headers.get("If-Match") and self.headers["If-Match"] != etag:
                    self.send_response(412)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
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
                    for i in range(0, len(send), 8192):
                        self.wfile.write(send[i:i + 8192])
                        self.wfile.flush()
                        time.sleep(svc.pace)
                except (BrokenPipeError, ConnectionResetError):
                    return
                with svc.lock:
                    svc.served[path] = svc.served.get(path, 0) + len(send)
                if drop:
                    time.sleep(0.2)
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self.close_connection = True
                    self.connection.close()

        class Quiet(http.server.ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 64

            def handle_error(self, request, client_address):
                pass

        self.pace = 0.001
        self.server = Quiet(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def connect(self, host, port):
        assert host == HOST, host
        return http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)

    def of(self, path):
        return [r for p, r in self.gets if p == path]


svc = Service()
downloader.CONNECT = svc.connect
downloader.CHUNK = 8192


def fresh(name):
    app, data = os.path.join(TMP, name, "app"), os.path.join(TMP, name, "data")
    os.makedirs(app)
    os.environ["SURVIVAL_LLM_DATA"] = data
    svc.gets, svc.served, svc.asked = [], {}, []
    return app


def run(app, seconds=60, resume=False, job=None):
    """Start (or Resume) the job and sample the page's figures to the end: (job, [(phase, error, done, bytes)])."""
    downs.clear()
    job = job or packs.Job(app, service=f"https://{HOST}")
    job.resume(KEY_TEXT) if resume else job.start(KEY_TEXT)
    seen, until = [], time.monotonic() + seconds
    while time.monotonic() < until:
        s = job.snapshot()
        seen.append((s["phase"], s["error"], s["done"], s["bytes"]))
        downs.append(s.get("download_done") or 0)
        if not job.running():
            break
    job.wait(5)
    s = job.snapshot()
    seen.append((s["phase"], s["error"], s["done"], s["bytes"]))
    return job, seen


downs = []  # download_done at each look of the last run()


def never_back(seen):
    done = [x[2] for x in seen]
    return [(a, b) for a, b in zip(done, done[1:]) if b < a]


def current(path):
    return open(os.path.join(os.environ["SURVIVAL_LLM_DATA"], "current", path), "rb").read()


def frame_starts(path, pk=LIBRARY):
    """The compressed offset of every frame of a stored file."""
    frames = next(f for f in pk["manifest"]["files"] if f["path"] == path)["stored"]["frames"]
    out, at = [], 0
    for z in frames:
        out.append(at)
        at += z
    return out


check("the test's packs are compressed: every text file and the index stored smaller than it unpacks",
      all("stored" in f for f in LIBRARY["manifest"]["files"] if f["path"].startswith("text/") or
          f["path"] == "index.sqlite"), [f["path"] for f in LIBRARY["manifest"]["files"] if "stored" not in f])
raw_total = sum(len(b) for b in LIB_FILES.values()) + sum(len(b) for b in GUIDE_FILES.values())
stored_total = sum(len(b) for pk in (GUIDES, LIBRARY) for p, b in pk["stored"].items() if p not in packs.SPECIAL)
print(f"     {raw_total:,} bytes unpacked, {stored_total:,} stored ({stored_total / raw_total:.0%})")

# ---------- 1. a format-2 download: both packs, unpacked as they come, installed byte for byte ----------
app = fresh("whole")
job, seen = run(app)
snap = job.snapshot()
check("the app asks the key service for format 2", svc.asked and all(a == 2 for a in svc.asked), svc.asked)
check("a format-2 download finishes and both packs install", snap["phase"] == "done"
      and sorted(packs.installed(app)) == ["guides", "library"], snap)
check("every library file is installed exactly as it unpacks", all(current(p) == b for p, b in LIB_FILES.items()))
guides_dir = packs.guides_dir()
check("the guides are installed unpacked too",
      all(open(os.path.join(guides_dir, p), "rb").read() == b for p, b in GUIDE_FILES.items()))
check("no compressed file is left in the install", not any(n.endswith((".zst", ".part", ".part.json"))
                                                         for r, _, ns in os.walk(os.environ["SURVIVAL_LLM_DATA"])
                                                         for n in ns), "")
bar_total = raw_total + sum(len(pk["stored"][s]) for pk in (GUIDES, LIBRARY) for s in packs.SPECIAL)
check("the progress bar counts the unpacked bytes (and pack.json and pack.sig), ends full and never goes back",
      snap["bytes"] == bar_total and snap["done"] == bar_total and not never_back(seen), (snap, never_back(seen)))
check("no error at any point", all(e is None for _, e, _, _ in seen), [x for x in seen if x[1]][:3])
stored_all = sum(len(b) for pk in (GUIDES, LIBRARY) for b in pk["stored"].values())
check("the page's download figures count the stored (compressed) bytes, end full and never go back",
      snap["download_bytes"] == stored_all and snap["download_done"] == stored_all
      and not [1 for a, b in zip(downs, downs[1:]) if b < a], (snap["download_bytes"], stored_all))
starts = frame_starts("index.sqlite")
segs = sorted(svc.of("index.sqlite"))
want = sorted(f"bytes={starts[i * 4]}-{(starts[i * 4 + 4] if i < 9 else len(LIBRARY['stored']['index.sqlite'])) - 1}"
              for i in range(10))
check("the split index came as one range request per segment, each four whole frames", segs == want, (segs, want))
check("each stored byte was downloaded once", sum(svc.served.values()) == sum(
    len(b) for pk in (GUIDES, LIBRARY) for b in pk["stored"].values()), (sum(svc.served.values()), stored_total))

# ---------- 2. resets: a whole file and a segment carry on from the frame they stopped in ----------
app = fresh("resets")
shard = "text/shard-002.txt"
seg5 = f"bytes={starts[20]}-{starts[24] - 1}"
svc.drops = {shard: 2, ("index.sqlite", seg5): 1}
job, seen = run(app)
snap = job.snapshot()
check("resets in a compressed file and a compressed segment: the download finishes by itself",
      snap["phase"] == "done" and current(shard) == LIB_FILES[shard] and current("index.sqlite") == BIG, snap)
check("no error and the bar never goes back during the retries", all(e is None for _, e, _, _ in seen)
      and not never_back(seen), never_back(seen)[:3])
asked = svc.of(shard)
shard_starts = frame_starts(shard)
froms = [int(r[6:].split("-")[0]) if r else 0 for r in asked]
check("each retry of the file asks from the start of a frame past the first, later each time",
      len(froms) == 3 and froms[0] == 0 and all(f in shard_starts for f in froms)
      and 0 < froms[1] < froms[2], (froms, shard_starts))
seg_retry = [r for r in svc.of("index.sqlite") if r.endswith(f"-{starts[24] - 1}") and r != seg5]
check("the reset segment is asked for again from a frame inside it, to its end",
      len(seg_retry) == 1 and int(seg_retry[0][6:].split("-")[0]) in starts[21:24], (seg_retry, starts[20:25]))

# ---------- 3. pause and Resume: the unpacked bytes are kept ----------
app = fresh("pause")
svc.pace = 0.01
job = packs.Job(app, service=f"https://{HOST}")
job.start(KEY_TEXT)
until = time.monotonic() + 30
while time.monotonic() < until and job.snapshot()["done"] < raw_total // 3:
    time.sleep(0.02)
job.pause()
job.wait(10)
paused = job.snapshot()
staging = os.path.join(packs.incoming_dir(), "library")
partials = {p: downloader.partial_size(os.path.join(staging, p)) for p in TEXT}
kept = {p: n for p, n in partials.items() if n}
check("a pause mid-download keeps the unpacked bytes", paused["phase"] == "paused" and paused["done"] > 0
      and (kept or os.path.exists(os.path.join(staging, "index.sqlite.segments.json"))), (paused, partials))
svc.pace = 0.001
svc.gets = []
job, seen = run(app, resume=True, job=job)
after = {p: svc.of(p) for p in kept}
check("Resume finishes it", job.snapshot()["phase"] == "done" and current("index.sqlite") == BIG, job.snapshot())
check("Resume asks for a partial file's frames from a frame start, not from zero",
      all(rs and rs[0] and int(rs[0][6:].split("-")[0]) in frame_starts(p) and int(rs[0][6:].split("-")[0]) > 0
          for p, rs in after.items() if kept[p] >= FRAME), after)
check("the bar picks up where the pause left it and never goes back", seen[0][2] >= paused["done"] * 0.9
      and not never_back(seen), (paused["done"], seen[:2], never_back(seen)[:3]))

# ---------- 4. a format-1 partial file carries on as format 2 ----------
app = fresh("migrate")
staging = os.path.join(packs.incoming_dir(), "library")
os.makedirs(os.path.join(staging, "text"), exist_ok=True)
half = TEXT[shard][:len(TEXT[shard]) // 2]
open(os.path.join(staging, shard + ".part"), "wb").write(half)
json.dump({"url": f"library/{shard}", "size": len(TEXT[shard]), "sha256": sha(TEXT[shard]), "validator": '"v1"'},
          open(os.path.join(staging, shard + ".part.json"), "w"))
job, seen = run(app)
froms = [int(r[6:].split("-")[0]) if r else 0 for r in svc.of(shard)]
check("a format-1 partial file is continued from the frame its bytes reach, unpacked",
      job.snapshot()["phase"] == "done" and current(shard) == TEXT[shard]
      and froms == [frame_starts(shard)[len(half) // FRAME]], (froms, frame_starts(shard)[len(half) // FRAME],
                                                               job.snapshot().get("error")))

# ---------- 5. wrong bytes and wrong pack.json ----------
def broken(label, mutate, tamper=None, want_text=None):
    pk = make_pack("library", "2026-11-26", LIB_FILES, tamper=tamper)
    mutate(pk)
    svc.v2 = {"library": pk}
    app = fresh(label)
    job, _ = run(app)
    s = job.snapshot()
    ok = s["phase"] == "error" and "library" not in packs.installed(app) and (want_text in (s["error"] or "")
                                                                             if want_text else True)
    svc.v2 = {"guides": GUIDES, "library": LIBRARY}
    return ok, s


def flip_frame(pk):
    z = bytearray(pk["stored"][shard])
    frames = next(f for f in pk["manifest"]["files"] if f["path"] == shard)["stored"]["frames"]
    z[frames[0] + 10] ^= 0xFF  # inside the second frame
    pk["stored"][shard] = bytes(z)


ok, s = broken("badframe", flip_frame)
check("a frame that doesn't unpack stops the download with an error and nothing installs", ok, s)


ALT = bytes(reversed(TEXT[shard]))  # the same length, other bytes
ALT_Z, ALT_FRAMES = compress(ALT)


def other_stored(m):  # pack.json says the shard is stored as ALT's frames, but keeps the shard's own SHA-256
    f = next(f for f in m["files"] if f["path"] == shard)
    f["stored"] = {"size": len(ALT_Z), "sha256": sha(ALT_Z), "frames": ALT_FRAMES}


def serve_alt(pk):
    pk["stored"][shard] = ALT_Z


ok, s = broken("wrongbytes", serve_alt, tamper=other_stored, want_text="SHA-256")
check("compressed bytes that unpack to the wrong content stop the download with an error", ok, s)


def bad_sum(m):
    f = next(f for f in m["files"] if f["path"] == shard)
    f["stored"]["frames"][0] += 1


ok, s = broken("badsum", lambda pk: None, tamper=bad_sum, want_text="isn't valid")
check("a signed pack.json whose frames don't add up to the stored size is refused", ok, s)


def bad_frame_size(m):
    m["frame"] = 30_000  # doesn't divide SEGMENT (100 000)


ok, s = broken("badframesize", lambda pk: None, tamper=bad_frame_size, want_text="isn't valid")
check("a frame size that doesn't divide a segment is refused", ok, s)

# ---------- 6. a service with only format-1 packs ----------
svc.v2 = None
svc.v1 = {"guides": make_pack("guides", "2026-11-26", GUIDE_FILES, fmt=1),
          "library": make_pack("library", "2026-11-26", LIB_FILES, fmt=1)}
app = fresh("format1")
job, seen = run(app)
check("a service that answers with format-1 packs (or an older one) still works",
      job.snapshot()["phase"] == "done" and current("index.sqlite") == BIG and svc.asked[0] == 2, job.snapshot())

# ---------- 7. Windows: a delete that meets a reader waits for it (library/oscompat.py) ----------
from library import oscompat  # noqa: E402

calls = []


def busy_remove(path):  # as Windows answers while another thread has the file open, twice
    calls.append(path)
    if len(calls) <= 2:
        err = PermissionError(13, "The process cannot access the file because it is being used by another process")
        err.winerror = 32
        raise err


def denied_remove(path):
    err = PermissionError(13, "Access is denied")
    err.winerror = 5
    raise err


real_remove, real_unlink = os.remove, os.unlink
try:
    os.remove, os.unlink = busy_remove, denied_remove
    oscompat._windows_remove()
    os.remove("x")
    waited = len(calls) == 3
    try:
        os.unlink("y")
        denied = False
    except PermissionError:
        denied = True
finally:
    os.remove, os.unlink = real_remove, real_unlink
check("on Windows a delete refused because a reader has the file open is tried again until it works", waited, calls)
check("an access-denied delete is not retried", denied)

svc.server.shutdown()
print(f"\n{len(FAILS)} failed" if FAILS else "\nall compressed pack checks passed")
sys.exit(1 if FAILS else 0)
