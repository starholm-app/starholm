"""The hash lock for PDFs and risk tables: the first copy is recorded, a changed copy is kept back for review while
the previous one stays in use, and an archive (fallback) copy counts only if it matches the lock.
No network: curl is replaced by a fake (safe_fetch.run_curl).

Usage: .venv/bin/python tests/pipeline_lock_test.py"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="pipeline-lock-")
os.environ["SURVIVAL_LLM_DATA"] = os.path.join(TMP, "data")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import fetch_manuals, safe_fetch  # noqa: E402

FAILS = []
COUNT = [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


RESPONSES = {}


def fake_curl(args):
    body = RESPONSES.get(args[-1])
    if body is None:
        return 22, "403"
    with open(args[args.index("--output") + 1], "wb") as f:
        f.write(body)
    return 0, "200"


safe_fetch.run_curl = fake_curl
RAW = os.path.join(TMP, "raw")
LOCK_PATH = os.path.join(TMP, "hashes.lock.json")
PRIMARY = "https://www.example.gov/guide.pdf"
FALLBACK = "https://web.archive.org/web/2025id_/https://www.example.gov/guide.pdf"
HOSTS = safe_fetch.hosts_of([PRIMARY, FALLBACK])
REL = "manuals/us-gov/00.pdf"
DEST = os.path.join(RAW, REL)
PENDING = os.path.join(RAW, ".pending", REL)
V1, V2, V3 = b"%PDF-1.7 first edition", b"%PDF-1.7 second edition", b"%PDF-1.7 tampered mirror copy"


def read(path):
    with open(path, "rb") as f:
        return f.read()


def fetch(lock, rel=REL, urls=(PRIMARY, FALLBACK)):
    out = safe_fetch.fetch_locked(lock, rel, list(urls), HOSTS, check=safe_fetch.is_pdf, raw_dir=RAW)
    safe_fetch.save_lock(lock, LOCK_PATH)
    return out


lock = safe_fetch.load_lock(LOCK_PATH)
RESPONSES.update({PRIMARY: V1})
check("first fetch from the official address is recorded", fetch(lock) == "recorded" and read(DEST) == V1
      and lock["files"][REL]["sha256"] == safe_fetch.sha256_bytes(V1), lock)
check("the lock file is in the data folder's place, not the repo", os.path.exists(LOCK_PATH))
check("the same file again is unchanged", fetch(lock) == "unchanged" and read(DEST) == V1)

RESPONSES[PRIMARY] = V2
check("a changed file is kept back", fetch(lock) == "pending", lock.get("pending"))
check("the previous copy stays in use", read(DEST) == V1 and safe_fetch.locked_ok(lock, REL, RAW))
check("the changed copy waits in raw/.pending/", read(PENDING) == V2)
check("the change is flagged with both hashes",
      lock["pending"][REL]["sha256"] == safe_fetch.sha256_bytes(V2)
      and lock["pending"][REL]["previous"] == safe_fetch.sha256_bytes(V1), lock["pending"])
check("the same change on the next run is not news", fetch(lock) == "still-pending")
on_disk = json.load(open(LOCK_PATH))
check("the lock on disk lists the pending file", REL in on_disk["pending"], on_disk)

del RESPONSES[PRIMARY]
RESPONSES[FALLBACK] = V3
check("an archive copy that differs from the lock is rejected", fetch(lock) == "rejected" and read(DEST) == V1)
RESPONSES[FALLBACK] = V1
check("an archive copy that matches the lock counts", fetch(lock) == "unchanged" and read(DEST) == V1)
check("an archive copy that matches the lock does not clear a held real change", REL in lock["pending"]
      and os.path.exists(PENDING), lock["pending"])

REL2 = "manuals/us-gov/01.pdf"
check("a first copy that only an archive had is held for review, not used",
      fetch(lock, REL2) == "pending" and not os.path.exists(os.path.join(RAW, REL2))
      and not safe_fetch.locked_ok(lock, REL2, RAW))
def raises(fn):
    try:
        fn()
    except safe_fetch.FetchError:
        return True
    return False


reviewed2 = lock["pending"][REL2]["sha256"]
check("accepting without the reviewed hash is refused", raises(lambda: safe_fetch.accept_pending(lock, REL2, RAW))
      and raises(lambda: safe_fetch.accept_pending(lock, REL2, RAW, sha256=reviewed2[:8])) and REL2 in lock["pending"])
check("accepting it after review puts it into use", safe_fetch.accept_pending(lock, REL2, RAW, sha256=reviewed2[:12])
      and safe_fetch.locked_ok(lock, REL2, RAW) and REL2 not in lock["pending"])

RESPONSES.clear()
RESPONSES[PRIMARY] = b"<html>403 Forbidden</html>"
check("an HTML error page instead of a PDF counts as a failed fetch", fetch(lock) == "failed" and read(DEST) == V1)
RESPONSES.clear()
check("nothing reachable: failed, previous copy kept", fetch(lock) == "failed" and read(DEST) == V1)
RESPONSES[PRIMARY] = V2
fetch(lock)
reviewed = lock["pending"][REL]["sha256"]  # the owner reviews this copy ...
V2B = b"%PDF-1.7 second edition, quietly revised"
RESPONSES[PRIMARY] = V2B
check("a newer download while held replaces the held copy quietly but is recorded",
      fetch(lock) == "still-pending" and read(PENDING) == V2B and lock["pending"][REL]["replaced"][-1]["was"] == reviewed,
      lock["pending"][REL])
check("... and the owner's accept of the copy they reviewed is refused, nothing changes",
      raises(lambda: safe_fetch.accept_pending(lock, REL, RAW, sha256=reviewed)) and read(DEST) == V1
      and REL in lock["pending"])
check("accepting the copy held now, named by its hash, swaps it in",
      safe_fetch.accept_pending(lock, REL, RAW, sha256=safe_fetch.sha256_bytes(V2B)) and read(DEST) == V2B
      and lock["files"][REL]["sha256"] == safe_fetch.sha256_bytes(V2B))
check("accepting with nothing pending is refused", not safe_fetch.accept_pending(lock, REL, RAW, sha256="0" * 64))
check("no download is left behind in raw/.incoming/", not os.listdir(os.path.join(RAW, ".incoming")))

calls = []


def refuse_host(args):
    calls.append(args[-1])
    return 22, "000"


safe_fetch.run_curl = refuse_host
check("a URL off the allow-list never reaches curl",
      safe_fetch.fetch_locked(lock, REL, ["https://evil.example/guide.pdf"], HOSTS, raw_dir=RAW) == "failed"
      and not calls, calls)

# ---- a primary URL that redirects to another host ----
def redirecting(args):
    with open(args[args.index("--output") + 1], "wb") as f:
        f.write(V1)
    return 0, "200", "https://cdn.elsewhere.example/guide.pdf"


safe_fetch.run_curl = redirecting
REL3 = "manuals/us-gov/02.pdf"
check("redirect to another host on a first fetch: held for review, not trusted",
      fetch(lock, REL3, (PRIMARY,)) == "pending" and not os.path.exists(os.path.join(RAW, REL3))
      and lock["pending"][REL3]["redirected_to"] == "cdn.elsewhere.example", lock["pending"].get(REL3))


def same_host_redirect(args):
    with open(args[args.index("--output") + 1], "wb") as f:
        f.write(V1)
    return 0, "200", "https://www.example.gov/files/guide-v2.pdf"


safe_fetch.run_curl = same_host_redirect
REL4 = "manuals/us-gov/03.pdf"
check("a redirect on the same host is a normal first fetch", fetch(lock, REL4, (PRIMARY,)) == "recorded")

# ---- a download the normalize step cannot handle ----
safe_fetch.run_curl = fake_curl
RESPONSES.clear()
RESPONSES[PRIMARY] = b"\x1f\x8b not really gzip"


def gunzip(path):
    import gzip
    with gzip.open(path, "rb") as g:
        data = g.read()
    with open(path, "wb") as f:
        f.write(data)


before = read(DEST)
out = safe_fetch.fetch_locked(lock, REL, [PRIMARY], HOSTS, normalize=gunzip, raw_dir=RAW)
check("a broken gzip: that file fails, nothing raised", out == "failed", out)
check("a broken gzip: raw/.incoming/ is clean and the copy in use is untouched",
      not os.listdir(os.path.join(RAW, ".incoming")) and read(DEST) == before)
RESPONSES[PRIMARY] = V2
check("the next file still works after a broken one",
      safe_fetch.fetch_locked(lock, REL, [PRIMARY], HOSTS, raw_dir=RAW) in ("unchanged", "pending", "still-pending"))

# ---- pages with moving parts: compared by content, and a held source is notified once ----
def menu_free(path):
    with open(path, "rb") as f:
        return safe_fetch.sha256_bytes(b"".join(l for l in f.read().splitlines(True) if not l.startswith(b"MENU")))


PAGE = "risk/page.html"
PAGE_URL = "https://www.example.gov/page"
P1, P1_MOVED, P2, P3 = (b"MENU a b\n<p>odds 1 in 15,300</p>", b"MENU b a\n<p>odds 1 in 15,300</p>",
                        b"MENU a b\n<p>odds 1 in 16,000</p>", b"MENU a b\n<p>odds 1 in 17,000</p>")


def fetch_page(body):
    RESPONSES.clear()
    RESPONSES[PAGE_URL] = body
    out = safe_fetch.fetch_locked(lock, PAGE, [PAGE_URL], HOSTS | {"www.example.gov"}, raw_dir=RAW,
                                  content_key=menu_free)
    return out


safe_fetch.run_curl = fake_curl
check("a page is recorded with its content key", fetch_page(P1) == "recorded"
      and lock["files"][PAGE].get("content_sha256"))
check("the same content with the menu reordered is unchanged, and the locked bytes stay",
      fetch_page(P1_MOVED) == "unchanged" and read(os.path.join(RAW, PAGE)) == P1 and PAGE not in lock["pending"])
del lock["files"][PAGE]["content_sha256"]  # a lock recorded before content keys existed
check("an older lock entry gets its content key on the next fetch", fetch_page(P1_MOVED) == "unchanged"
      and lock["files"][PAGE].get("content_sha256"))
check("a real change to the content is held for review", fetch_page(P2) == "pending")
first_held = lock["pending"][PAGE]["held_since"]
check("a second change while held is not news (notified once), and replaces the held copy",
      fetch_page(P3) == "still-pending" and read(os.path.join(RAW, ".pending", PAGE)) == P3
      and lock["pending"][PAGE]["held_since"] == first_held, lock["pending"][PAGE])
RESPONSES.clear()
RESPONSES["https://web.archive.org/web/2026id_/" + PAGE_URL] = P1_MOVED
check("an archive copy with the recorded text does not clear the held change",
      safe_fetch.fetch_locked(lock, PAGE, [PAGE_URL, "https://web.archive.org/web/2026id_/" + PAGE_URL],
                              HOSTS | {"www.example.gov"}, raw_dir=RAW, content_key=menu_free) == "unchanged"
      and PAGE in lock["pending"])
check("going back to the recorded content clears the hold", fetch_page(P1_MOVED) == "unchanged"
      and PAGE not in lock["pending"])
from library import fetch_risk  # noqa: E402
nav = ('<html><body><nav><a>{0}</a><a>{1}</a></nav><main><p>Odds of being struck: 1 in 15,300. '
       + 'Lightning safety advice for the outdoors. ' * 10 + '</p></main></body></html>')
pa, pb = os.path.join(TMP, "pa.html"), os.path.join(TMP, "pb.html")
open(pa, "w").write(nav.format("Brochures", "Weather-Ready Nation"))
open(pb, "w").write(nav.format("Weather-Ready Nation", "Brochures"))
check("the risk pages' content key ignores a reordered menu", fetch_risk.page_text_key(pa) == fetch_risk.page_text_key(pb))
open(pb, "w").write(nav.format("Brochures", "Weather-Ready Nation").replace("15,300", "16,000"))
check("... but not a changed number", fetch_risk.page_text_key(pa) != fetch_risk.page_text_key(pb))

# ---- the manual list itself ----
nwss = next(url for _, title, url in fetch_manuals.MANUALS if title.startswith("Nuclear War Survival Skills"))
check("Nuclear War Survival Skills comes from ORNL's HTTPS copy, not the OSTI link that redirects to http",
      nwss == "https://info.ornl.gov/sites/publications/Files/Pub57110.pdf" and "info.ornl.gov" in fetch_manuals.ALLOWED_HOSTS)
check("every manual and fallback URL is https", all(u.startswith("https://") for _, _, u in fetch_manuals.MANUALS)
      and all(u.startswith("https://") for u in fetch_manuals.FALLBACK.values()))

# ---- fetch_manuals end to end: every manual comes back as a PDF, then one changes ----
safe_fetch.run_curl = fake_curl
RESPONSES.clear()
for _, title, url in fetch_manuals.MANUALS:
    RESPONSES[url] = b"%PDF-1.4 " + title.encode()
RAW2, LOCK2 = os.path.join(TMP, "raw2"), os.path.join(TMP, "lock2.json")
result = fetch_manuals.main(raw_dir=RAW2, lock_path=LOCK2)
listed = json.load(open(os.path.join(RAW2, "manuals", "us-gov", "manifest.json")))
check("manuals: all fetched and recorded", result == {"pending": [], "failed": []}
      and len(json.load(open(LOCK2))["files"]) == len(fetch_manuals.MANUALS), result)
first_url = fetch_manuals.MANUALS[0][2]
RESPONSES[first_url] = b"%PDF-1.4 a quietly replaced file"
result = fetch_manuals.main(raw_dir=RAW2, lock_path=LOCK2)
check("manuals: the changed one is flagged for review", result["pending"] == ["manuals/us-gov/00.pdf"], result)
check("manuals: the manifest still lists the reviewed copy",
      json.load(open(os.path.join(RAW2, "manuals", "us-gov", "manifest.json"))) == listed)

shutil.rmtree(TMP, ignore_errors=True)
print(f"{COUNT[0] - len(FAILS)} of {COUNT[0]} checks passed" if FAILS else f"all {COUNT[0]} checks passed")
sys.exit(1 if FAILS else 0)
