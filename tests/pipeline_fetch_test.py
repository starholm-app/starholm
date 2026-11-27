"""ZIM downloads fail closed: the .meta4 reader, the URL allow-list, the curl command line, and verification
(missing checksum, wrong checksum, wrong size). No network: curl is replaced by a fake (safe_fetch.run_curl).

Usage: .venv/bin/python tests/pipeline_fetch_test.py"""
import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="pipeline-fetch-")
os.environ["SURVIVAL_LLM_DATA"] = os.path.join(TMP, "data")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import fetch_zims, safe_fetch  # noqa: E402
from library.safe_fetch import FetchError  # noqa: E402

FAILS = []
COUNT = [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


def raises(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except FetchError:
        return True
    return False


CONTENT = b"ZIM" * 1000
SHA = hashlib.sha256(CONTENT).hexdigest()
NAME = "zimgit-knots_en_2024-08.zim"


def meta4(name=NAME, size=len(CONTENT), sha=SHA, extra=""):
    hash_line = f'<hash type="sha-256">{sha}</hash>' if sha is not None else ""
    size_line = f"<size>{size}</size>" if size is not None else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <generator>MirrorBrain/2.19.0</generator>
  <file name="{name}">
    {size_line}
    <!-- internal id: 190 -->
    <hash type="md5">01aad14f7b42567ad8ff0a665491b22e</hash>
    {hash_line}
    {extra}
    <pieces length="4194304" type="sha-1"><hash>0a98d5bf6520f5194a2c18446f7e1a61b90517b1</hash></pieces>
    <url location="us" priority="1">https://mirror.example/zim/{name}</url>
  </file>
</metalink>""".encode()


# ---- the .meta4 reader ----
check("meta4: size and SHA-256 read", safe_fetch.parse_meta4(meta4(), NAME) == (len(CONTENT), SHA))
check("meta4: no SHA-256 is refused", raises(safe_fetch.parse_meta4, meta4(sha=None), NAME))
check("meta4: a malformed SHA-256 is refused", raises(safe_fetch.parse_meta4, meta4(sha="abc123"), NAME))
check("meta4: no size is refused", raises(safe_fetch.parse_meta4, meta4(size=None), NAME))
check("meta4: a zero size is refused", raises(safe_fetch.parse_meta4, meta4(size=0), NAME))
check("meta4: another file's checksum is refused", raises(safe_fetch.parse_meta4, meta4(name="other.zim"), NAME))
check("meta4: two SHA-256 values are refused",
      raises(safe_fetch.parse_meta4, meta4(extra=f'<hash type="sha-256">{"0" * 64}</hash>'), NAME))
check("meta4: a DOCTYPE is refused",
      raises(safe_fetch.parse_meta4, b'<!DOCTYPE x [<!ENTITY a "b">]>' + meta4()[38:], NAME))
check("meta4: not XML is refused", raises(safe_fetch.parse_meta4, b"<html>301 Moved</html", NAME))
check("meta4: empty is refused", raises(safe_fetch.parse_meta4, b"", NAME))

# ---- the URL allow-list ----
ZIM_HOSTS = safe_fetch.KIWIX_DOWNLOAD_HOSTS
for url in ("https://download.kiwix.org/zim/other/x.zim", "https://lb.download.kiwix.org/zim/other/x.zim.meta4",
            "https://DOWNLOAD.kiwix.org:443/zim/x.zim"):
    check(f"allowed: {url}", not raises(safe_fetch.check_url, url, ZIM_HOSTS))
for url in ("http://download.kiwix.org/zim/x.zim", "https://ftpmirror.your.org/pub/kiwix/zim/x.zim",
            "https://download.kiwix.org.evil.example/zim/x.zim", "https://evil.example/download.kiwix.org/x.zim",
            "https://user@download.kiwix.org/zim/x.zim", "https://download.kiwix.org:8443/zim/x.zim",
            "https://download.kiwix.org/zim/x .zim", "https://download.kiwix.org/zim/x\n.zim", "-K/etc/passwd",
            "ftp://download.kiwix.org/zim/x.zim", "file:///etc/passwd", "", None):
    check(f"refused: {url!r}", raises(safe_fetch.check_url, url, ZIM_HOSTS))

# ---- the curl command line ----
args = safe_fetch.curl_args("https://download.kiwix.org/zim/x.zim", "/tmp/out", follow=False)
check("curl: -q comes first (no ~/.curlrc)", args[:2] == [safe_fetch.CURL, "-q"], args)
check("curl: on macOS, Apple's own /usr/bin/curl by its full path, never the first curl on the PATH",
      safe_fetch.CURL == ("/usr/bin/curl" if sys.platform == "darwin" else "curl"), safe_fetch.CURL)
check("curl: https only", args[args.index("--proto") + 1] == "=https", args)
check("curl: https only on redirects", args[args.index("--proto-redir") + 1] == "=https", args)
check("curl: stall limit", "--speed-limit" in args and "--speed-time" in args, args)
check("curl: -- right before the URL, which comes last",
      args[-2:] == ["--", "https://download.kiwix.org/zim/x.zim"], args)
check("curl: no redirects when follow=False", "--location" not in args and "-L" not in args, args)
args = safe_fetch.curl_args("https://download.kiwix.org/zim/x.zim", "/tmp/out", follow=True, resume=True)
check("curl: follows redirects, resumes", "--location" in args and "--continue-at" in args, args)

# ---- which curl ran goes in the log, once per run (subprocess.run faked: nothing is fetched) ----
ran = []


def fake_run(cmd, **kw):
    ran.append(cmd)
    out = "curl 8.7.1 (x86_64-apple-darwin25.0) libcurl/8.7.1 (SecureTransport) LibreSSL/3.3.6\nRelease-Date: x\n" \
        if "--version" in cmd else "200 https://download.kiwix.org/zim/x.zim"
    return subprocess.CompletedProcess(cmd, 0, out, "")


real_run, safe_fetch.subprocess.run = safe_fetch.subprocess.run, fake_run
log = io.StringIO()
with contextlib.redirect_stdout(log):
    for _ in range(3):
        safe_fetch.run_curl(safe_fetch.curl_args("https://download.kiwix.org/zim/x.zim", "/tmp/out"))
safe_fetch.subprocess.run = real_run
versions = [c for c in ran if "--version" in c]
check("curl's version is logged once per run, from the same curl that fetches, before the first fetch",
      versions == [[safe_fetch.CURL, "-q", "--version"]] and ran[0] == versions[0] and len(ran) == 4
      and log.getvalue().count("curl 8.7.1 (x86_64-apple-darwin25.0)") == 1 and f"curl: {safe_fetch.CURL}: " in log.getvalue(),
      (ran, log.getvalue()))

calls = []


def fake_curl(responses):
    """responses: URL -> (bytes or None, status). Writes the bytes where curl would."""
    def run(args):
        url = args[-1]
        calls.append(url)
        body, status = responses.get(url, (None, "404"))
        if body is None:
            return 22, status
        dest = args[args.index("--output") + 1]
        mode = "ab" if "--continue-at" in args else "wb"
        with open(dest, mode) as f:
            f.write(body)
        return 0, status
    return run


safe_fetch.run_curl = fake_curl({})
calls.clear()
check("download: a host off the list never reaches curl",
      raises(safe_fetch.download, "https://evil.example/x.zim", "/tmp/never", ZIM_HOSTS) and not calls, calls)
safe_fetch.run_curl = fake_curl({"https://lb.download.kiwix.org/a.meta4": (b"<html>moved</html>", "301")})
check("metadata: a redirect counts as a failure",
      raises(safe_fetch.fetch_small, "https://lb.download.kiwix.org/a.meta4", ZIM_HOSTS))

# ---- the catalog ----
CATALOG = """<feed>
<entry><name>zimgit-knots_en</name><flavour></flavour>
<link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
 href="https://lb.download.kiwix.org/zim/other/zimgit-knots_en_2024-08.zim.meta4" length="1" /></entry>
<entry><name>evil_en</name><flavour></flavour>
<link rel="http://opds-spec.org/acquisition/open-access" href="https://evil.example/zim/evil_en_2024-08.zim.meta4"/>
</entry>
<entry><name>sneaky_en</name><flavour></flavour>
<link rel="http://opds-spec.org/acquisition/open-access"
 href="https://download.kiwix.org/zim/../../etc/sneaky_en_2024-08.zim.meta4"/></entry>
</feed>"""
found = fetch_zims.parse_catalog(CATALOG)
check("catalog: a Kiwix entry is read", found.get(("zimgit-knots_en", "")) == "/zim/other/" + NAME, found)
check("catalog: another host is left out", ("evil_en", "") not in found, found)
check("catalog: a path with .. is left out", ("sneaky_en", "") not in found, found)
check("catalog: ZIM from download.kiwix.org, .meta4 from its own load balancer",
      fetch_zims.zim_url("/zim/other/" + NAME).startswith("https://download.kiwix.org/")
      and fetch_zims.meta4_url("/zim/other/" + NAME) == f"https://lb.download.kiwix.org/zim/other/{NAME}.meta4")

# ---- verifying a download ----
ZDIR = fetch_zims.zim_dir()
os.makedirs(ZDIR, exist_ok=True)
DEST = os.path.join(ZDIR, NAME)


def put(data):
    with open(DEST, "wb") as f:
        f.write(data)


put(CONTENT)
check("verify: no checksum -> rejected and deleted", not fetch_zims.verify_zim(DEST, None, None)
      and not os.path.exists(DEST))
put(CONTENT)
check("verify: wrong checksum -> rejected and deleted", not fetch_zims.verify_zim(DEST, len(CONTENT), "0" * 64)
      and not os.path.exists(DEST))
put(CONTENT + b"x")
check("verify: wrong size -> rejected and deleted", not fetch_zims.verify_zim(DEST, len(CONTENT), SHA)
      and not os.path.exists(DEST))
put(CONTENT)
check("verify: a match is accepted and recorded", fetch_zims.verify_zim(DEST, len(CONTENT), SHA)
      and fetch_zims.is_verified(DEST, len(CONTENT), SHA))
check("verify: the newest verified file is found",
      fetch_zims.latest_verified("zimgit-knots_en") == DEST, fetch_zims.latest_verified("zimgit-knots_en"))
with open(DEST, "ab") as f:
    f.write(b"tampered")
check("verify: a file changed after verifying no longer counts", not fetch_zims.verified_record(DEST)
      and fetch_zims.latest_verified("zimgit-knots_en") is None)
fetch_zims.remove_zim(DEST)

# ---- Kiwix file names ----
check("file_prefix: a plain monthly file", fetch_zims.file_prefix("wikem_en_all_nopic_2026-10.zim") == "wikem_en_all_nopic")
check("file_prefix: a re-release with a letter",
      fetch_zims.file_prefix("nhs.uk_en_medicines_2026-09b.zim") == "nhs.uk_en_medicines")
check("file_prefix: not a Kiwix name", fetch_zims.file_prefix("notes.txt") is None)
NHS_OLD, NHS_NEW = (os.path.join(ZDIR, n) for n in ("nhs.uk_en_medicines_2026-09.zim", "nhs.uk_en_medicines_2026-09b.zim"))
for path in (NHS_OLD, NHS_NEW):
    with open(path, "wb") as f:
        f.write(CONTENT)
    fetch_zims.verify_zim(path, len(CONTENT), SHA)
check("latest_verified: a lettered re-release is found, and is newer than the plain month",
      fetch_zims.latest_verified("nhs.uk_en_medicines") == NHS_NEW, fetch_zims.latest_verified("nhs.uk_en_medicines"))
for path in (NHS_OLD, NHS_NEW):
    fetch_zims.remove_zim(path)
check("the Gardenology source uses its catalog name; its files keep the old prefix",
      ("gardenology_en_all", "") in fetch_zims.SOURCES
      and "gardenology.org_en_all" in __import__("library.build_library", fromlist=["x"]).ZIM_SOURCES)

# ---- planning and downloading, end to end with a fake Kiwix ----
fetch_zims.fetch_catalog = lambda: {("zimgit-knots_en", ""): "/zim/other/" + NAME}
fetch_zims.SOURCES = [("zimgit-knots_en", ""), ("zimgit-water_en", "")]
META = fetch_zims.meta4_url("/zim/other/" + NAME)
ZIM = fetch_zims.zim_url("/zim/other/" + NAME)

safe_fetch.run_curl = fake_curl({})  # the checksum cannot be fetched
put(b"partial")
items = fetch_zims.plan()
knots = next(i for i in items if i["name"] == "zimgit-knots_en")
water = next(i for i in items if i["name"] == "zimgit-water_en")
check("plan: no checksum -> error, not a download", knots["status"] == "error", knots)
check("plan: a source missing from the catalog is reported", water["status"] == "missing", water)
calls.clear()
check("download: no checksum -> no download, and the partial copy is kept for a later resume",
      not fetch_zims.download_zim(knots) and not calls and os.path.exists(DEST)
      and not fetch_zims.verified_record(DEST) and fetch_zims.latest_verified("zimgit-knots_en") is None, calls)

safe_fetch.run_curl = fake_curl({META: (meta4(sha="1" * 64), "200"), ZIM: (CONTENT, "200")})
item = fetch_zims.plan()[0]
check("plan: a new file is planned as a download", item["status"] == "download", item)
check("download: a wrong checksum -> rejected and deleted",
      not fetch_zims.download_zim(item) and not os.path.exists(DEST))
check("download: the ZIM was fetched from download.kiwix.org", ZIM in calls, calls)

safe_fetch.run_curl = fake_curl({META: (meta4(size=len(CONTENT) + 5), "200"), ZIM: (CONTENT, "200")})
item = fetch_zims.plan()[0]
check("download: a wrong size -> rejected and deleted", not fetch_zims.download_zim(item)
      and not os.path.exists(DEST))

safe_fetch.run_curl = fake_curl({META: (meta4(), "200"), ZIM: (CONTENT, "200")})
item = fetch_zims.plan()[0]
check("download: a match is accepted", fetch_zims.download_zim(item) and fetch_zims.is_verified(DEST, len(CONTENT), SHA))
calls.clear()
item = fetch_zims.plan()[0]
check("plan: a verified copy is current and not fetched again",
      item["status"] == "current" and ZIM not in calls, (item, calls))

safe_fetch.run_curl = fake_curl({})  # Kiwix's checksum server is down this week
item = fetch_zims.plan()[0]
check("meta4 down: the source is an error this run", item["status"] == "error", item)
check("meta4 down: the verified ZIM the live build came from is kept",
      not fetch_zims.download_zim(item) and os.path.exists(DEST) and fetch_zims.is_verified(DEST, len(CONTENT), SHA))
check("meta4 down: the build still finds it", fetch_zims.latest_verified("zimgit-knots_en") == DEST)

seen_args = []
inner = fake_curl({META: (meta4(), "200"), ZIM: (CONTENT, "200")})


def recording(args):
    seen_args.append(args)
    return inner(args)


safe_fetch.run_curl = recording
fetch_zims.remove_zim(DEST)
item = fetch_zims.plan()[0]
fetch_zims.download_zim(item)
zim_args = next(a for a in seen_args if a[-1] == ZIM)
check("download: capped at the .meta4 size", zim_args[zim_args.index("--max-filesize") + 1] == str(len(CONTENT)),
      zim_args)
check("download: has a time limit generous for a slow line", int(zim_args[zim_args.index("--max-time") + 1]) >= 3600,
      zim_args)
check("download: retries cannot stretch the time limit",
      zim_args[zim_args.index("--retry-max-time") + 1] == zim_args[zim_args.index("--max-time") + 1], zim_args)
meta_args = next(a for a in seen_args if a[-1] == META)
check("meta4: fetched with redirects off", "--location" not in meta_args, meta_args)

safe_fetch.run_curl = fake_curl({META: (meta4(), "200"), ZIM: (CONTENT[100:], "206")})
fetch_zims.remove_zim(DEST)
put(CONTENT[:100])
item = fetch_zims.plan()[0]
check("download: an interrupted download resumes and verifies",
      fetch_zims.download_zim(item) and fetch_zims.is_verified(DEST, len(CONTENT), SHA))

# ---- a catalog reply that is empty or mostly missing is an error, not "missing" ----
fetch_zims.SOURCES = [("a_en", ""), ("b_en", ""), ("c_en", ""), ("d_en", "")]
safe_fetch.run_curl = fake_curl({})
fetch_zims.fetch_catalog = lambda: {}
items = fetch_zims.plan()
check("catalog empty: every source is an error", [i["status"] for i in items] == ["error"] * 4, items)
fetch_zims.fetch_catalog = lambda: {("a_en", ""): "/zim/other/a_en_2026-01.zim"}
items = fetch_zims.plan()
check("catalog with 3 of 4 sources missing: an error, the reply looks incomplete",
      all(i["status"] == "error" and "incomplete" in i["error"] for i in items), items)
fetch_zims.fetch_catalog = lambda: {(n, ""): f"/zim/other/{n}_2026-01.zim" for n in ("a_en", "b_en", "c_en")}
items = fetch_zims.plan()
check("catalog with 1 of 4 missing: that one is a missing source (a warning)",
      [i["status"] for i in items if i["status"] == "missing"] == ["missing"]
      and next(i for i in items if i["name"] == "d_en")["status"] == "missing", items)

# ---- the honest user agent and its Safari fallback (curl faked: nothing is fetched) ----
from library import fetch_manuals, fetch_risk  # noqa: E402

UA_URL = "https://www.example.gov/manual.pdf"
UA_HOSTS = safe_fetch.hosts_of([UA_URL, "https://web.archive.org/web/2025id_/" + UA_URL])
seen = []


def answering(by_agent):
    """by_agent: user agent -> status; a status other than 200 is a --fail exit (22), as curl gives."""
    def run(args):
        agent = args[args.index("--user-agent") + 1] if "--user-agent" in args else None
        seen.append((args[-1], agent, args))
        status = by_agent.get(agent, "200")
        if status != "200":
            return 22, status
        with open(args[args.index("--output") + 1], "wb") as f:
            f.write(b"%PDF-1.7 manual")
        return 0, status
    return run


def ua_fetch(by_agent, fallback=safe_fetch.SAFARI_UA):
    seen.clear()
    safe_fetch.run_curl = answering(by_agent)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok, _ = safe_fetch.download_ex(UA_URL, os.path.join(TMP, "ua.pdf"), UA_HOSTS, user_agent=safe_fetch.HONEST_UA,
                                       max_bytes=1 << 20, max_time=60, fallback_user_agent=fallback)
    return ok, [a for _, a, _ in seen], out.getvalue()


H, SAF = safe_fetch.HONEST_UA, safe_fetch.SAFARI_UA
check("the honest name is starholm-library/1.0, and both fetchers send it first with Safari as the fallback",
      H == "starholm-library/1.0" and "Safari/" in SAF and "starholm" not in SAF
      and (fetch_manuals.UA, fetch_manuals.FALLBACK_UA) == (fetch_risk.UA, fetch_risk.FALLBACK_UA) == (H, SAF))
ok, agents, log = ua_fetch({})
check("a publisher that accepts the honest name: one request, no Safari, and the log says so",
      ok and agents == [H] and f"accepted '{H}'" in log, (ok, agents, log))
for status in safe_fetch.REFUSED:
    ok, agents, log = ua_fetch({H: status})
    check(f"HTTP {status} to the honest name: tried once more as Safari, which worked, and the log says which",
          ok and agents == [H, SAF] and f"refused '{H}' (HTTP {status})" in log and "accepted the fallback (Safari)" in log,
          (ok, agents, log))
ok, agents, log = ua_fetch({H: "403", SAF: "403"})
check("refused under both names: two requests in all, never a third, and the download fails", not ok and agents == [H, SAF],
      (ok, agents, log))
for status in ("404", "429", "500", "000"):
    ok, agents, _ = ua_fetch({H: status})
    check(f"HTTP {status} is not a refusal of the name: no Safari retry", not ok and agents == [H], agents)
ok, agents, _ = ua_fetch({H: "403"}, fallback=None)
check("without a fallback (the ZIM downloads) a 403 stays a failure, one request", not ok and agents == [H], agents)
ua_fetch({H: "403"})
first, second = (a[2] for a in seen)
strip = lambda args: [x for i, x in enumerate(args) if x not in (H, SAF) and not (i + 1 < len(args) and args[i + 1] in (H, SAF))]  # noqa: E731
check("the Safari retry keeps every safeguard: the same curl, flags, https-only, redirect and size limits",
      strip(first) == strip(second) and second[:2] == [safe_fetch.CURL, "-q"] and "--max-filesize" in second
      and second[second.index("--proto-redir") + 1] == "=https" and second[second.index("--max-redirs") + 1] == "5",
      (first, second))
seen.clear()
safe_fetch.run_curl = answering({H: "403"})
try:
    safe_fetch.download_ex("https://evil.example/x.pdf", os.path.join(TMP, "ua.pdf"), UA_HOSTS, user_agent=H,
                           fallback_user_agent=SAF)
    check("a host off the allow-list is refused before either name is tried", False)
except FetchError:
    check("a host off the allow-list is refused before either name is tried", seen == [], seen)
seen.clear()
safe_fetch.run_curl = answering({H: "403"})
lock = safe_fetch.load_lock(os.path.join(TMP, "no-lock.json"))
with contextlib.redirect_stdout(io.StringIO()):
    got = safe_fetch.fetch_locked(lock, "manuals/us-gov/00.pdf", [UA_URL], UA_HOSTS, user_agent=H, fallback_user_agent=SAF,
                                  check=safe_fetch.is_pdf, raw_dir=os.path.join(TMP, "uaraw"))
check("fetch_locked hands the fallback down, and the Safari copy still goes through the lock and the PDF check",
      got == "recorded" and [a for _, a, _ in seen] == [H, SAF] and os.path.isfile(os.path.join(TMP, "uaraw", "manuals/us-gov/00.pdf")),
      (got, [a for _, a, _ in seen]))

shutil.rmtree(TMP, ignore_errors=True)
print(f"{COUNT[0] - len(FAILS)} of {COUNT[0]} checks passed" if FAILS else f"all {COUNT[0]} checks passed")
sys.exit(1 if FAILS else 0)
