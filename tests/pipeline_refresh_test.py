"""refresh.py as a whole: --dry-run fetches only the catalog and .meta4 files, a run with nothing to do exits 0
without a notification, a second run while one holds the lock does nothing, failures and files needing review are
notified, and superseded ZIMs are pruned. No network: curl is replaced by a fake (safe_fetch.run_curl).

Usage: .venv/bin/python tests/pipeline_refresh_test.py"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TMP = tempfile.mkdtemp(prefix="pipeline-refresh-")
os.environ["SURVIVAL_LLM_DATA"] = os.path.join(TMP, "data")
os.environ["SURVIVAL_LLM_REFRESH_LOG"] = os.path.join(TMP, "logs", "survival-library.log")
os.environ["SURVIVAL_LLM_APP_DIR"] = os.path.join(TMP, "app")
os.environ["SURVIVAL_LLM_LIBRARY"] = os.path.join(TMP, "no-library")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":  # the weekly refresh runs through launchd, on the Mac only
    print("SKIP on Windows: the weekly refresh runs through launchd, on the Mac only")
    sys.exit(0)
from library import build_library, fetch_manuals, fetch_risk, fetch_wikihow, fetch_zims, refresh, safe_fetch  # noqa: E402

FAILS = []
COUNT = [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    sys.__stdout__.write(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}") + "\n")
    sys.__stdout__.flush()
    if not ok:
        FAILS.append(name)


NOTES = []
refresh.notify = NOTES.append


def main(args):
    """refresh.main(), with the terminal restored afterwards (it sends output to the log as well)."""
    try:
        return refresh.main(args)
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__


DATA = os.environ["SURVIVAL_LLM_DATA"]
NAME = "zimgit-knots_en_2024-08.zim"
PATH = "/zim/other/" + NAME
CONTENT = b"ZIM" * 100
META = f"""<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink"><file name="{NAME}"><size>{len(CONTENT)}</size>
<hash type="sha-256">{hashlib.sha256(CONTENT).hexdigest()}</hash></file></metalink>""".encode()
CATALOG = f"""<feed><entry><name>zimgit-knots_en</name><flavour></flavour>
<link rel="http://opds-spec.org/acquisition/open-access" href="https://lb.download.kiwix.org{PATH}.meta4"/>
</entry></feed>""".encode()
COMMIT = "5e44fed0e204c27ad985e6292e55e311504d4fe5"
WIKIHOW = b"headline,title,text\n" + b'"Step one.","How to Test","Some text."\n' * 50
WH_SHA = hashlib.sha256(WIKIHOW).hexdigest()
WH_TREE = json.dumps([{"type": "file", "path": "wikihowAll.csv", "size": len(WIKIHOW),
                       "lfs": {"oid": WH_SHA, "size": len(WIKIHOW)}}]).encode()
SERVED = {fetch_zims.CATALOG_URL: CATALOG, fetch_zims.meta4_url(PATH): META, fetch_zims.zim_url(PATH): CONTENT,
          fetch_wikihow.revision_url(): json.dumps({"sha": COMMIT}).encode(),
          fetch_wikihow.tree_url(COMMIT): WH_TREE, fetch_wikihow.download_url(COMMIT): WIKIHOW}

REAL_PIN = dict(fetch_wikihow.PIN)
fetch_wikihow.PIN = {"commit": COMMIT, "size": len(WIKIHOW), "sha256": WH_SHA}  # pin the small stand-in file

# A stand-in for the local Ollama's /api/status, on a free loopback port.
OLLAMA = {"answer": {"cloud": {"disabled": True, "source": "config"}}}


class FakeOllama(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(OLLAMA["answer"]).encode()
        self.send_response(200 if self.path == "/api/status" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), FakeOllama)
threading.Thread(target=server.serve_forever, daemon=True).start()
refresh.OLLAMA_STATUS = f"http://127.0.0.1:{server.server_address[1]}/api/status"
CALLS = []


ARGS = {}


def fake_curl(args):
    url = args[-1]
    CALLS.append(url)
    ARGS[url] = args
    body = SERVED.get(url)
    if body is None:
        return 22, "404"
    with open(args[args.index("--output") + 1], "wb") as f:
        f.write(body)
    return 0, "200"


safe_fetch.run_curl = fake_curl
fetch_zims.SOURCES = [("zimgit-knots_en", "")]

# ---- --dry-run downloads nothing ----
rc = main(["--dry-run"])
check("dry run: exits 0", rc == 0, rc)
check("dry run: fetched only the catalog, the .meta4 and Hugging Face's metadata",
      sorted(set(CALLS)) == sorted([fetch_zims.CATALOG_URL, fetch_zims.meta4_url(PATH), fetch_wikihow.revision_url(),
                                    fetch_wikihow.tree_url(COMMIT)]), CALLS)
check("dry run: the wikiHow file itself was not requested", fetch_wikihow.download_url(COMMIT) not in CALLS, CALLS)
check("dry run: no ZIM, PDF or risk table requested", fetch_zims.zim_url(PATH) not in CALLS
      and not any(url in CALLS for _, _, url in fetch_manuals.MANUALS)
      and not any(url in CALLS for _, _, url in fetch_risk.DOCUMENTS), CALLS)
zim_dir = os.path.join(DATA, "zim")
check("dry run: nothing downloaded or built", not (os.path.isdir(zim_dir) and os.listdir(zim_dir))
      and not os.path.exists(os.path.join(DATA, "libs")) and not os.path.lexists(os.path.join(DATA, "current")))
with open(os.environ["SURVIVAL_LLM_REFRESH_LOG"]) as f:
    log = f.read()
check("dry run: the log lists what would happen", f"download  {NAME}" in log and "download  wikihowAll.csv" in log
      and "would rebuild: wikihow, knots" in log and "ollama: cloud features off" in log, log)
check("dry run: no notification", NOTES == [], NOTES)

check("zim_key: the build key comes from the file name", refresh.zim_key(
    {"name": "gardenology_en_all", "file": "gardenology.org_en_all_2026-09.zim"}) == "gardenology"
    and refresh.zim_key({"name": "nhs.uk_en_medicines", "file": "nhs.uk_en_medicines_2026-09b.zim"}) == "nhs-medicines"
    and refresh.zim_key({"name": "wikem_en_all", "flavour": "nopic"}) is None)

# ---- wikiHow: pinned in the repo; Hugging Face's API is only compared with ----
check("wikiHow: the real pin is the reviewed commit, size and SHA-256",
      REAL_PIN == {"commit": "5e44fed0e204c27ad985e6292e55e311504d4fe5", "size": 619172672,
                   "sha256": "8ebf5bd485275af895aa83ea3e55c416fe76aa4fefc58e0b9e6d344852bde5aa"}, REAL_PIN)
check("wikiHow: only huggingface.co is on its allow-list", fetch_wikihow.HF_HOSTS == {"huggingface.co"})
lock = safe_fetch.load_lock()
item = fetch_wikihow.plan(lock)
check("wikiHow plan: the pinned file is to be downloaded, the dataset matches the pin",
      item["status"] == "download" and item["upstream"] == "same", item)
check("wikiHow: the pinned download is accepted", fetch_wikihow.fetch(item, lock) == {"pending": [], "failed": []}
      and fetch_wikihow.ready(lock) == (fetch_wikihow.dest(), WH_SHA))
check("wikiHow: the next plan finds it current", fetch_wikihow.plan(lock)["status"] == "current")
check("wikiHow: the build uses the pinned copy",
      build_library.source_inputs().get("wikihow", {}).get("fingerprint", "").startswith(WH_SHA))

fetch_zims.remove_zim(fetch_wikihow.dest())
SERVED[fetch_wikihow.download_url(COMMIT)] = WIKIHOW.replace(b"Some", b"Evil")  # same size, other bytes
result = fetch_wikihow.fetch(fetch_wikihow.plan(lock), lock)
check("wikiHow: a download that does not match the pin is deleted and fails",
      result["failed"] and not os.path.exists(fetch_wikihow.dest())
      and not os.path.exists(fetch_wikihow.dest() + ".part"), result)
check("wikiHow: nothing unverified is built", "wikihow" not in build_library.source_inputs())
SERVED[fetch_wikihow.download_url(COMMIT)] = WIKIHOW
fetch_wikihow.fetch(fetch_wikihow.plan(lock), lock)

# The dataset changes upstream: a new commit with another file.
NEW_COMMIT = "1" * 40
WIKIHOW2 = WIKIHOW + b'"Step two.","How to Change","Rewritten."\n'
SERVED[fetch_wikihow.revision_url()] = json.dumps({"sha": NEW_COMMIT}).encode()
SERVED[fetch_wikihow.tree_url(NEW_COMMIT)] = json.dumps([{"type": "file", "path": "wikihowAll.csv",
    "lfs": {"oid": hashlib.sha256(WIKIHOW2).hexdigest(), "size": len(WIKIHOW2)}}]).encode()
SERVED[fetch_wikihow.download_url(NEW_COMMIT)] = WIKIHOW2
item = fetch_wikihow.plan(lock)
check("wikiHow changed upstream: the pinned copy is current, the change is noticed",
      item["status"] == "current" and item["upstream"] == "changed", item)
result = fetch_wikihow.fetch(item, lock)
check("wikiHow changed upstream: the new file is held for review", result == {"pending": ["wikihowAll.csv"],
      "failed": []} and os.path.exists(os.path.join(safe_fetch.PENDING_DIR, "wikihowAll.csv")), result)
held_args = ARGS[fetch_wikihow.download_url(NEW_COMMIT)]
check("wikiHow changed upstream: the held download is capped at the published size, with a time limit for 0.6 GB",
      held_args[held_args.index("--max-filesize") + 1] == str(len(WIKIHOW2))
      and int(held_args[held_args.index("--max-time") + 1]) >= 3600, held_args)
import contextlib, io  # noqa: E401,E402
shown = io.StringIO()
with contextlib.redirect_stdout(shown):
    refresh.print_pending(lock)
check("--pending explains a held wikiHow copy as a dataset change, not an archive copy",
      "the wikiHow dataset on Hugging Face changed (commit 1111111" in shown.getvalue()
      and "pinned copy stays in use" in shown.getvalue() and "Internet Archive" not in shown.getvalue(),
      shown.getvalue())
check("wikiHow changed upstream: the pinned copy stays in use and in the build",
      fetch_wikihow.ready(lock) == (fetch_wikihow.dest(), WH_SHA)
      and build_library.source_inputs().get("wikihow", {}).get("fingerprint", "").startswith(WH_SHA))
before = CALLS.count(fetch_wikihow.download_url(NEW_COMMIT))
item = fetch_wikihow.plan(lock)
fetch_wikihow.fetch(item, lock)
check("wikiHow changed upstream: the next week it is still held, and not downloaded again",
      item["upstream"] == "held" and CALLS.count(fetch_wikihow.download_url(NEW_COMMIT)) == before, item)
check("wikiHow: accepting the change puts it into use", safe_fetch.accept_pending(
    lock, "wikihowAll.csv", sha256=hashlib.sha256(WIKIHOW2).hexdigest()))
safe_fetch.save_lock(lock)
check("wikiHow: after accepting, the build uses the new file",
      build_library.source_inputs().get("wikihow", {}).get("fingerprint", "").startswith(
          hashlib.sha256(WIKIHOW2).hexdigest()))
item = fetch_wikihow.plan(lock)
check("wikiHow: after accepting, the dataset matches what is in use", item["status"] == "current"
      and item["upstream"] == "same", item)
del SERVED[fetch_wikihow.revision_url()]
item = fetch_wikihow.plan(lock)
check("wikiHow: Hugging Face not answering changes nothing", item["status"] == "current"
      and item["upstream"] == "unknown" and fetch_wikihow.fetch(item, lock) == {"pending": [], "failed": []}, item)
fetch_wikihow.plan = lambda lock=None, force=(): {"status": "current", "upstream": "same", "error": None}

# ---- a run with nothing to do: exit 0, no notification ----
fetch_zims.plan = lambda wanted=None, force=(): []
fetch_manuals.main = lambda: {"pending": [], "failed": []}
fetch_risk.main = lambda: {"pending": [], "failed": []}
build_library.build_and_swap = lambda force=(): {"status": "noop", "changed": [], "problems": []}
NOTES.clear()
check("no-op: exits 0", main([]) == 0)
check("no-op: no notification", NOTES == [], NOTES)

# ---- failures and reviews are notified ----
build_library.build_and_swap = lambda force=(): {"status": "failed", "changed": ["risk"],
                                                 "problems": ["risk: 1 documents, down from 5"]}
RETIRED = []
real_retire = build_library.retire_originals
build_library.retire_originals = lambda *a, **k: RETIRED.append(1) or []
check("failed check: exits 1", main([]) == 1)
check("failed check: a failure notification", NOTES == [refresh.NOTIFY_FAILED], NOTES)
check("failed check: no original is deleted", RETIRED == [], RETIRED)
build_library.build_and_swap = lambda force=(): {"status": "noop", "changed": [], "problems": []}
fetch_manuals.main = lambda: {"pending": ["manuals/us-gov/00.pdf"], "failed": []}
NOTES.clear()
check("a file held for review: exits 0", main([]) == 0)
check("a no-op run deletes the originals the live build holds", RETIRED == [1], RETIRED)
build_library.retire_originals = real_retire
check("a file held for review: a review notification", NOTES == [refresh.NOTIFY_REVIEW.format(n=1)], NOTES)
fetch_manuals.main = lambda: {"pending": [], "failed": []}
real_wh_fetch = fetch_wikihow.fetch
fetch_wikihow.fetch = lambda item, lock: {"pending": ["wikihowAll.csv"], "failed": []}
NOTES.clear()
check("a changed wikiHow held for review: exits 0 and notifies like a changed PDF", main([]) == 0
      and NOTES == [refresh.NOTIFY_REVIEW.format(n=1)], NOTES)
fetch_wikihow.fetch = real_wh_fetch

# ---- a source the catalog no longer lists: a warning, notified once ----
fetch_zims.plan = lambda wanted=None, force=(): [{"name": "wikem_en_all", "flavour": "nopic", "status": "missing",
                                        "error": "not in the Kiwix catalog"}]
NOTES.clear()
rc, = [main([])]
check("missing source: not a failure, and notified the first time", rc == 0
      and NOTES == [refresh.NOTIFY_MISSING.format(n=1)], (rc, NOTES))
NOTES.clear()
check("missing source: not notified again the next week", main([]) == 0 and NOTES == [], NOTES)
fetch_zims.plan = lambda wanted=None, force=(): []
main([])
fetch_zims.plan = lambda wanted=None, force=(): [{"name": "wikem_en_all", "flavour": "nopic", "status": "missing",
                                        "error": "not in the Kiwix catalog"}]
NOTES.clear()
check("missing source: back and then gone again is news again", main([]) == 0
      and NOTES == [refresh.NOTIFY_MISSING.format(n=1)], NOTES)
fetch_zims.plan = lambda wanted=None, force=(): []

# ---- Ollama's cloud features ----
OLLAMA["answer"] = {"cloud": {"disabled": False}}
NOTES.clear()
check("ollama: cloud not disabled -> a notification, and still exit 0", main([]) == 0
      and NOTES == [refresh.NOTIFY_CLOUD], NOTES)
OLLAMA["answer"] = {"version": "x"}
NOTES.clear()
check("ollama: no cloud field at all counts as not disabled", main([]) == 0 and NOTES == [refresh.NOTIFY_CLOUD], NOTES)
OLLAMA["answer"] = {"cloud": {"disabled": True}}
NOTES.clear()
check("ollama: cloud disabled -> no notification", main([]) == 0 and NOTES == [], NOTES)
server.shutdown()
server.server_close()
NOTES.clear()
check("ollama: not answering -> no notification, no failure", main([]) == 0 and NOTES == [], NOTES)
with open(refresh.__file__, encoding="utf-8") as f:
    check("ollama: the real status URL is loopback only, with no proxy",
          'OLLAMA_STATUS = "http://127.0.0.1:11434/api/status"' in (src := f.read()) and "ProxyHandler({})" in src)

# ---- two runs never overlap ----
held = safe_fetch.take_lock()
check("lock: the first holder gets it", held is not None)
check("lock: a second taker is refused while it is held", safe_fetch.take_lock() is None)
ran = []
fetch_zims.plan = lambda wanted=None, force=(): ran.append(1) or []
NOTES.clear()
check("lock: a refresh while another runs exits 0 and does nothing", main([]) == 0 and not ran and not NOTES,
      (ran, NOTES))
held.close()
check("lock: free again once released", main([]) == 0 and ran == [1])

# ---- --recheck from the command line ----
rc, out = (lambda start: (main(["--recheck", os.path.join(DATA, "libs", "lib-2026-01-01")]),
                          open(os.environ["SURVIVAL_LLM_REFRESH_LOG"]).read()[start:]))(
    os.path.getsize(os.environ["SURVIVAL_LLM_REFRESH_LOG"]))
check("--recheck of a folder that is not a kept failed build: exits 1 and says why",
      rc == 1 and "not re-checked:" in out and "is not a failed build" in out, out)
try:
    main(["--recheck"])
    refused = False
except SystemExit:
    refused = True
check("--recheck with no folder is refused", refused)

# ---- unknown options and source keys ----
for args in (["--force"], ["--rebuild", "nonsense"]):
    try:
        main(args)
        refused = False
    except SystemExit:
        refused = True
    check(f"refused: {' '.join(args)}", refused)

# ---- --pending and --accept ----
def candidate(data):
    fd, path = tempfile.mkstemp(dir=TMP)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


A, B = "manuals/us-gov/00.pdf", "risk/cdc-mortality-2024.pdf"
OLD, NEW, ARCHIVED = b"%PDF-1.7 reviewed", b"%PDF-1.7 changed upstream", b"%PDF-1.7 archive only"
lock = safe_fetch.load_lock()
safe_fetch.settle_locked_file(lock, A, candidate(OLD), "https://example.gov/a.pdf", from_fallback=False)
check("setup: a changed file is held", safe_fetch.settle_locked_file(
    lock, A, candidate(NEW), "https://example.gov/a.pdf", from_fallback=False) == "pending")
check("setup: an archive-only first copy is held", safe_fetch.settle_locked_file(
    lock, B, candidate(ARCHIVED), "https://example.gov/b.pdf", from_fallback=True) == "pending")
safe_fetch.save_lock(lock)
LOG = os.environ["SURVIVAL_LLM_REFRESH_LOG"]


def log_after(run):
    start = os.path.getsize(LOG)
    rc = run()
    with open(LOG) as f:
        f.seek(start)
        return rc, f.read()


rc, out = log_after(lambda: main(["--pending"]))
check("--pending: exits 0 and lists both held files", rc == 0 and f"needs review: {A}" in out
      and f"needs review: {B}" in out, out)
check("--pending: shows why, where the copy is, its hash, and what is in use",
      "changed at the source" in out and "only from the Internet Archive" in out
      and os.path.join(safe_fetch.PENDING_DIR, A) in out and safe_fetch.sha256_bytes(NEW) in out
      and safe_fetch.sha256_bytes(OLD) in out
      and f"--accept {A} --sha256 {safe_fetch.sha256_bytes(NEW)[:16]}" in out, out)
check("--pending: says how long each file has been held", out.count("    held:     ") == 2 and "minutes, since" in out,
      out)
check("held_for: minutes, hours, days", refresh.held_for("2000-01-01T00:00:00Z").split(",")[0].endswith("days")
      and refresh.held_for(None) == "unknown")
check("--pending: changes nothing", safe_fetch.load_lock()["pending"].keys() == {A, B})

rc, out = log_after(lambda: main(["--accept", "risk/not-held.html"]))
check("--accept of a path that is not pending: exits 1, says so, changes nothing",
      rc == 1 and "nothing pending for risk/not-held.html" in out
      and safe_fetch.load_lock()["pending"].keys() == {A, B}, out)

rc, out = log_after(lambda: main(["--accept", A]))
check("--accept PATH without the reviewed hash: exits 1, lists what is held, accepts nothing",
      rc == 1 and f"{safe_fetch.sha256_bytes(NEW)}  {A}" in out and "nothing accepted" in out
      and safe_fetch.load_lock()["pending"].keys() == {A, B}, out)
rc, out = log_after(lambda: main(["--accept", A, "--sha256", "f" * 16]))
check("--accept PATH with a hash that is not the held copy's: exits 1, nothing changes",
      rc == 1 and "not accepted" in out and safe_fetch.load_lock()["pending"].keys() == {A, B}, out)
rc, out = log_after(lambda: main(["--accept", A, "--sha256", safe_fetch.sha256_bytes(NEW)[:16]]))
with open(os.path.join(safe_fetch.RAW_DIR, A), "rb") as f:
    in_use = f.read()
lock = safe_fetch.load_lock()
check("--accept PATH: exits 0 and puts the reviewed copy into use", rc == 0 and in_use == NEW
      and lock["files"][A]["sha256"] == safe_fetch.sha256_bytes(NEW) and lock["files"][A].get("reviewed"), lock)
check("--accept PATH: logs what it accepted before accepting it",
      out.index(f"needs review: {A}") < out.index(f"accepted {A}"), out)
check("--accept PATH: the other file is still held", lock["pending"].keys() == {B}, lock["pending"])

rc, out = log_after(lambda: main(["--accept", "all"]))
check("--accept all without a hash file: exits 1 and lists path, size and hash of each held file",
      rc == 1 and f"{safe_fetch.sha256_bytes(ARCHIVED)}  {B}" in out and f"({len(ARCHIVED):,} bytes)" in out
      and safe_fetch.load_lock()["pending"].keys() == {B}, out)
hash_file = os.path.join(TMP, "reviewed.sha256")
with open(hash_file, "w") as f:
    f.write(f"{safe_fetch.sha256_bytes(ARCHIVED)}  {B}\n")
rc, out = log_after(lambda: main(["--accept", "all", "--sha256-file", hash_file]))
lock = safe_fetch.load_lock()
check("--accept all --sha256-file: accepts every held file whose hash is listed", rc == 0 and not lock["pending"]
      and safe_fetch.locked_ok(lock, B) and f"accepted {B}" in out, (rc, lock, out))
rc, out = log_after(lambda: main(["--accept", "all", "--sha256-file", hash_file]))
check("--accept all with nothing held: exits 0, accepts nothing", rc == 0 and "accepted" not in out, out)

# the lock is saved after each accepted file: an error on a later file leaves the earlier ones recorded
for rel, body in ((A, b"%PDF-1.7 two"), (B, b"%PDF-1.7 three")):
    held = safe_fetch.load_lock()
    safe_fetch.settle_locked_file(held, rel, candidate(body), "https://example.gov/x.pdf", from_fallback=False)
    safe_fetch.save_lock(held)
real_accept = safe_fetch.accept_pending


def accept_then_fail(lock, rel, raw_dir=None, sha256=None):
    if rel == B:
        raise RuntimeError("disk full")
    return real_accept(lock, rel, raw_dir, sha256)


safe_fetch.accept_pending = accept_then_fail
with open(hash_file, "w") as f:
    f.write(f"{safe_fetch.sha256_bytes(b'%PDF-1.7 two')}  {A}\n{safe_fetch.sha256_bytes(b'%PDF-1.7 three')}  {B}\n")
rc = main(["--accept", "all", "--sha256-file", hash_file])
safe_fetch.accept_pending = real_accept
lock = safe_fetch.load_lock()
check("an error part-way through --accept leaves the files already moved recorded in the lock",
      rc == 1 and lock["files"][A]["sha256"] == safe_fetch.sha256_bytes(b"%PDF-1.7 two")
      and A not in lock["pending"] and B in lock["pending"], (rc, lock["pending"].keys()))
try:
    main(["--accept"])
    refused = False
except SystemExit:
    refused = True
check("--accept with no path is refused", refused)

# ---- superseded ZIMs are pruned ----
os.makedirs(zim_dir, exist_ok=True)
lib = os.path.join(DATA, "libs", "lib-2026-05-03")
os.makedirs(lib)
open(os.path.join(lib, "index.sqlite"), "w").close()
with open(os.path.join(lib, "build.json"), "w") as f:
    json.dump({"sources": {"wikem": {"origin": "wikem_en_all_nopic_2026-05.zim", "fingerprint": "x", "docs": 1}}}, f)
with open(os.path.join(lib, build_library.CHECKS_PASSED), "w") as f:
    json.dump({"passed": "2026-05-03T00:00:00Z"}, f)
os.symlink(os.path.join("libs", "lib-2026-05-03"), os.path.join(DATA, "current"))
for name in ("wikem_en_all_nopic_2026-01.zim", "wikem_en_all_nopic_2026-01.zim.verified",
             "wikem_en_all_nopic_2026-05.zim", "wikem_en_all_nopic_2026-05.zim.verified",
             "wikem_en_all_nopic_2026-06.zim", "notes.txt"):
    open(os.path.join(zim_dir, name), "w").close()
refresh.prune_zims([{"name": "wikem_en_all", "file": "wikem_en_all_nopic_2026-06.zim", "status": "download"}])
left = sorted(os.listdir(zim_dir))
check("prune: superseded ZIM and its record removed; the live build's origin, the catalog's file and other files kept",
      left == ["notes.txt", "wikem_en_all_nopic_2026-05.zim", "wikem_en_all_nopic_2026-05.zim.verified",
               "wikem_en_all_nopic_2026-06.zim"], left)

shutil.rmtree(TMP, ignore_errors=True)
print(f"{COUNT[0] - len(FAILS)} of {COUNT[0]} checks passed" if FAILS else f"all {COUNT[0]} checks passed")
sys.exit(1 if FAILS else 0)
