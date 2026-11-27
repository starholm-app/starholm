"""What the content fetchers and refresh.py share: the data folder's layout, a hardened curl wrapper with a URL
allow-list, the Kiwix .meta4 checksum reader, and the hash lock for PDFs and risk tables.

Nothing here runs at import. Every network call goes through run_curl(), so tests can stand in for curl.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET

# ---------- the data folder ----------
# Everything the pipeline writes lives in data.noindex/ inside the checkout this code runs from (git ignores it;
# ".noindex" keeps Spotlight out). SURVIVAL_LLM_DATA moves it (tests use a temporary folder). The logs go to logs/ in
# the checkout, or to SURVIVAL_LLM_LOGS (the Mac app sets both, since its code folder is read-only).
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.environ.get("SURVIVAL_LLM_DATA") or os.path.join(REPO, "data.noindex"))
LOG_DIR = os.path.abspath(os.environ.get("SURVIVAL_LLM_LOGS") or os.path.join(REPO, "logs"))
ZIM_DIR = os.path.join(DATA_DIR, "zim")
RAW_DIR = os.path.join(DATA_DIR, "raw")
LIBS_DIR = os.path.join(DATA_DIR, "libs")
CURRENT = os.path.join(DATA_DIR, "current")      # symlink to the live build, swapped atomically
HASH_LOCK = os.path.join(DATA_DIR, "hashes.lock.json")
PENDING_DIR = os.path.join(RAW_DIR, ".pending")  # changed PDFs and risk tables waiting for review
INCOMING_DIR = os.path.join(RAW_DIR, ".incoming")  # downloads before they are checked
ORIGINALS = os.path.join(DATA_DIR, "originals.json")  # the ZIMs and wikiHow CSV deleted once the live build held them
MISSING_SOURCES = os.path.join(DATA_DIR, "missing-sources.json")  # sources the Kiwix catalog no longer lists

# ---------- URL allow-lists ----------
# ZIMs and their checksums come only from Kiwix's own download server. download.kiwix.org answers every request
# with a 301 to lb.download.kiwix.org (checked 2026-11-12), so both names are Kiwix's own.
KIWIX_DOWNLOAD_HOSTS = frozenset({"download.kiwix.org", "lb.download.kiwix.org"})
# The OPDS catalog that names the current ZIM files (library.kiwix.org redirects here).
KIWIX_CATALOG_HOSTS = frozenset({"opds.library.kiwix.org"})


class FetchError(Exception):
    """A download, a checksum or a URL that cannot be trusted. The caller rejects the file."""


def check_url(url, allowed_hosts):
    """url, if it is a plain https URL on one of allowed_hosts; else FetchError. No user names, no other ports,
    no whitespace or control characters (curl would see them as separate options or a different host)."""
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise FetchError(f"refusing a malformed URL: {url!r}")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(f"refusing a non-https URL: {url}")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise FetchError(f"refusing a URL with a user name: {url}")
    try:
        port = parts.port
    except ValueError:
        raise FetchError(f"refusing a URL with a bad port: {url}") from None
    if port not in (None, 443):
        raise FetchError(f"refusing a URL on port {port}: {url}")
    host = (parts.hostname or "").lower()
    if host not in allowed_hosts:
        raise FetchError(f"refusing {host or 'a URL with no host'}: not on the allow-list")
    return url


def hosts_of(urls):
    """The allow-list for a fixed list of URLs written into this repo."""
    return frozenset((urllib.parse.urlsplit(u).hostname or "").lower() for u in urls)


# ---------- curl ----------

# Apple's own curl on macOS, called by its full path: under launchd the PATH can put Homebrew's curl first, and the
# weekly job should use the one macOS ships and updates. Elsewhere, the curl on the PATH.
CURL = "/usr/bin/curl" if sys.platform == "darwin" else "curl"
# The name the manual and risk-table downloads give publishers, so they can see what is fetching. Some government
# sites have refused scripted downloads before; when one refuses this name (REFUSED), that one download is tried once
# more as Safari, and the log says which name worked. Every other safeguard applies to both attempts.
HONEST_UA = "starholm-library/1.0"
SAFARI_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
             "Safari/605.1.15")
# Only a plain refusal of the request: 403 Forbidden, or 406 Not Acceptable (what some web filters send for a user
# agent they don't accept). Not 404, 429 (rate limiting: asking again as someone else would dodge it) or a 5xx.
REFUSED = ("403", "406")
SPEED_LIMIT = 1024   # bytes per second; slower than this for SPEED_TIME seconds counts as a stall
SPEED_TIME = 120
CONNECT_TIMEOUT = 30


def curl_args(url, dest, *, follow=True, resume=False, user_agent=None, max_bytes=None, max_time=None, retries=3):
    """The curl command line, as a list (never through a shell). -q first, so no ~/.curlrc can add options.
    HTTPS only, for redirects too; a stall limit; "--" before the URL so it can never be read as an option.
    Writes the HTTP status code and the final URL to stdout, so a caller can see a 301 it refused, or the host a
    redirect ended on."""
    args = [CURL, "-q", "--fail", "--silent", "--show-error",
            "--proto", "=https", "--proto-redir", "=https",
            "--speed-limit", str(SPEED_LIMIT), "--speed-time", str(SPEED_TIME),
            "--connect-timeout", str(CONNECT_TIMEOUT),
            "--retry", str(retries),
            "--write-out", "%{http_code} %{url_effective}"]
    if follow:
        args += ["--location", "--max-redirs", "5"]
    if resume:
        args += ["--continue-at", "-"]
    if user_agent:
        args += ["--user-agent", user_agent]
    if max_bytes:
        args += ["--max-filesize", str(int(max_bytes))]
    if max_time:
        # --max-time limits each attempt; --retry-max-time stops curl starting another attempt after that long, so
        # one call never runs much past twice max_time in all.
        args += ["--max-time", str(int(max_time)), "--retry-max-time", str(int(max_time))]
    args += ["--output", dest, "--", url]
    return args


_curl_logged = []


def log_curl_version():
    """Print which curl this run uses (its path and the first line of `curl --version`), once per run."""
    if _curl_logged:
        return
    _curl_logged.append(True)
    try:
        r = subprocess.run([CURL, "-q", "--version"], capture_output=True, text=True, timeout=30)
        line = (r.stdout.strip().splitlines() or [f"no version (exit {r.returncode})"])[0]
    except (OSError, subprocess.SubprocessError) as exc:
        line = f"can't run it: {exc}"
    print(f"    curl: {CURL}: {line[:300]}", flush=True)


def run_curl(args):
    """Run curl; returns (exit code, the HTTP status, the final URL after redirects). Tests replace this function
    (a fake may return just the first two; the final URL is then taken to be the one asked for)."""
    log_curl_version()
    r = subprocess.run(args, capture_output=True, text=True)
    status, _, effective = r.stdout.strip().partition(" ")
    if r.returncode and r.stderr.strip():
        print("    curl: " + r.stderr.strip().splitlines()[-1][:300], flush=True)
    return r.returncode, status, effective


def download_ex(url, dest, allowed_hosts, *, follow=True, resume=False, user_agent=None, max_bytes=None,
                max_time=None, fallback_user_agent=None):
    """Fetch url (which must be on allowed_hosts) into dest. Returns (ok, final URL): ok on HTTP 200/206.
    With follow=False a redirect counts as a failure. With fallback_user_agent, a refusal of user_agent (HTTP 403 or
    406) is tried once more with fallback_user_agent, through the same checks, and the log says which one worked."""
    check_url(url, allowed_hosts)
    agents = [user_agent] + ([fallback_user_agent] if fallback_user_agent and fallback_user_agent != user_agent else [])
    for n, agent in enumerate(agents):
        result = run_curl(curl_args(url, dest, follow=follow, resume=resume, user_agent=agent,
                                    max_bytes=max_bytes, max_time=max_time))
        code, status = result[0], result[1]
        effective = (result[2] if len(result) > 2 else "") or url
        ok = code == 0 and status in ("200", "206")
        if len(agents) > 1:
            if ok:
                print(f"    user agent: {host_of(url)} accepted " + ("the fallback (Safari)" if n else repr(agent)), flush=True)
            elif n == 0 and status in REFUSED:
                print(f"    user agent: {host_of(url)} refused {agent!r} (HTTP {status}); trying once more as Safari",
                      flush=True)
                if os.path.exists(dest) and not resume:
                    os.truncate(dest, 0)  # nothing of the refusal page is kept
                continue
        return ok, effective
    return False, url


def download(url, dest, allowed_hosts, **kw):
    """download_ex() without the final URL: True on HTTP 200/206."""
    return download_ex(url, dest, allowed_hosts, **kw)[0]


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def fetch_small(url, allowed_hosts, *, max_bytes=8 << 20, follow=False, max_time=120):
    """A small metadata file (a .meta4 or the catalog) as bytes, fetched with redirects off by default.
    Raises FetchError if it cannot be fetched."""
    fd, tmp = tempfile.mkstemp(prefix="survival-fetch-")
    os.close(fd)
    try:
        if not download(url, tmp, allowed_hosts, follow=follow, max_bytes=max_bytes, max_time=max_time):
            raise FetchError(f"could not fetch {url}")
        with open(tmp, "rb") as f:
            data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FetchError(f"{url} is larger than {max_bytes} bytes")
        return data
    finally:
        os.remove(tmp)


# ---------- hashes ----------

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ---------- Kiwix .meta4 ----------

METALINK_NS = "{urn:ietf:params:xml:ns:metalink}"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZIM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zim$")


def parse_meta4(data, expected_name):
    """(size, sha256) for expected_name from a Metalink 4 file, or FetchError. Only the whole-file hash counts,
    never the per-piece SHA-1 list. A document type or entity declaration is refused outright."""
    if not data or len(data) > (2 << 20):
        raise FetchError("checksum file is empty or too large")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise FetchError("checksum file declares a document type")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FetchError(f"checksum file is not valid XML: {exc}") from None
    if root.tag != METALINK_NS + "metalink":
        raise FetchError("not a Metalink 4 file")
    files = [f for f in root.findall(METALINK_NS + "file") if f.get("name") == expected_name]
    if len(files) != 1:
        raise FetchError(f"checksum file does not describe {expected_name} exactly once")
    node = files[0]
    size_text = (node.findtext(METALINK_NS + "size") or "").strip()
    if not size_text.isdigit() or int(size_text) <= 0:
        raise FetchError(f"no size for {expected_name}")
    hashes = [(h.text or "").strip().lower() for h in node.findall(METALINK_NS + "hash")
              if (h.get("type") or "").lower() == "sha-256"]
    if len(hashes) != 1 or not SHA256_RE.match(hashes[0]):
        raise FetchError(f"no SHA-256 for {expected_name}")
    return int(size_text), hashes[0]


# ---------- the hash lock for PDFs and risk tables ----------
# HASH_LOCK records the SHA-256 of every accepted file, keyed by its path under raw/. A file whose hash differs
# is kept back in raw/.pending/ and listed under "pending" until someone runs refresh.py --accept.

def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_lock(path=None):
    path = path or HASH_LOCK
    try:
        with open(path, encoding="utf-8") as f:
            lock = json.load(f)
    except FileNotFoundError:
        return {"version": 1, "files": {}, "pending": {}}
    if not isinstance(lock, dict) or not isinstance(lock.get("files"), dict):
        raise FetchError(f"{path} is damaged; fix or delete it by hand")
    lock.setdefault("pending", {})
    return lock


def save_lock(lock, path=None):
    """Write the lock atomically: a temporary file in the same folder, then a rename over the old one."""
    path = path or HASH_LOCK
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".hashes-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _install(src, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(src, dest)


def settle_locked_file(lock, rel, candidate, url, *, from_fallback, raw_dir=None, redirected_to=None, reason=None,
                       content_key=None):
    """Decide what happens to a freshly downloaded file (candidate) that belongs at raw/<rel>.

    Returns one of:
      "unchanged"  its hash matches the lock (the copy in raw/ is (re)installed from it if missing or different),
                   or, with content_key, its content matches the locked copy's even though the bytes differ (a
                   web page whose navigation moves around): the locked copy stays as it is
      "recorded"   first fetch from the primary URL: its hash is now in the lock and it is installed
      "pending"    it differs from the lock, or is a first copy that came from a fallback or from a primary URL
                   that redirected to another host (redirected_to): kept back in raw/.pending/ for review; the
                   previous copy, if any, stays in use
      "still-pending"  a change already held from an earlier run (the same copy, or a newer one that replaces it):
                   still listed by --pending, but not news, so no second notification
      "rejected"   a fallback copy that differs from the lock: deleted, it never counts
    The candidate file is always moved or deleted. The caller saves the lock."""
    raw_dir = raw_dir or RAW_DIR
    dest = os.path.join(raw_dir, rel)
    pending = os.path.join(raw_dir, ".pending", rel)
    digest = sha256_file(candidate)
    size = os.path.getsize(candidate)
    known = lock["files"].get(rel)
    if known and content_key and known.get("sha256") != digest and os.path.exists(dest):
        try:
            if "content_sha256" not in known and sha256_file(dest) == known.get("sha256"):
                known["content_sha256"] = content_key(dest)  # locks recorded before content keys existed
            same = known.get("content_sha256") and content_key(candidate) == known["content_sha256"]
        except Exception:  # the content cannot be read: compare bytes only
            same = False
        if same:
            os.remove(candidate)
            if not from_fallback and lock["pending"].pop(rel, None) and os.path.exists(pending):
                os.remove(pending)  # the source itself is back to the locked content: the held change is moot
            return "unchanged"
    if known and known.get("sha256") == digest:
        if not os.path.exists(dest) or sha256_file(dest) != digest:
            _install(candidate, dest)
        else:
            os.remove(candidate)
        if not from_fallback and lock["pending"].pop(rel, None) and os.path.exists(pending):
            os.remove(pending)  # the source went back to the reviewed version (an archive copy never clears a hold)
        return "unchanged"
    if known and from_fallback:
        os.remove(candidate)
        return "rejected"
    if not known and not from_fallback and not redirected_to:
        _install(candidate, dest)
        lock["files"][rel] = {"sha256": digest, "size": size, "url": url, "recorded": _now()}
        if content_key:
            try:
                lock["files"][rel]["content_sha256"] = content_key(dest)
            except Exception:
                pass
        return "recorded"
    # A changed file, or a first copy that only an archive mirror or another host had: hold it for a person.
    earlier = lock["pending"].get(rel)
    if earlier and earlier.get("sha256") == digest and os.path.exists(pending):
        os.remove(candidate)
        return "still-pending"  # flagged on an earlier run; not news
    _install(candidate, pending)
    replaced = (earlier or {}).get("replaced", [])
    if earlier and earlier.get("sha256") != digest:
        replaced = replaced + [{"at": _now(), "was": earlier.get("sha256")}]
    lock["pending"][rel] = {"sha256": digest, "size": size, "url": url, "seen": _now(), "replaced": replaced,
                            "held_since": (earlier or {}).get("held_since") or (earlier or {}).get("seen") or _now(),
                            "previous": known.get("sha256") if known else None,
                            "from_fallback": bool(from_fallback), "redirected_to": redirected_to, "reason": reason}
    # A source already held is notified once: a newer copy replaces the held one quietly until someone reviews it.
    return "still-pending" if earlier else "pending"


MIN_ACCEPT_HEX = 12  # how much of the reviewed SHA-256 an accept must name


def accept_pending(lock, rel, raw_dir=None, sha256=None):
    """A person reviewed raw/.pending/<rel>: move it into place and record its hash. sha256 is the hash (at least
    its first MIN_ACCEPT_HEX hex digits) of the copy that person reviewed; a held copy can be replaced by a newer
    download between review and accept, and then the accept is refused (FetchError). False if nothing is pending."""
    raw_dir = raw_dir or RAW_DIR
    info = lock["pending"].get(rel)
    pending = os.path.join(raw_dir, ".pending", rel)
    if not info or not os.path.exists(pending):
        return False
    reviewed = (sha256 or "").strip().lower()
    if len(reviewed) < MIN_ACCEPT_HEX or not re.fullmatch(r"[0-9a-f]+", reviewed):
        raise FetchError(f"name the reviewed copy's SHA-256 (at least {MIN_ACCEPT_HEX} hex digits) to accept {rel}")
    digest = sha256_file(pending)
    if digest != info.get("sha256"):
        raise FetchError(f"{pending} changed since it was flagged; fetch it again")
    if not digest.startswith(reviewed):
        raise FetchError(f"{rel} is now {digest[:16]}..., not the {reviewed[:16]}... that was reviewed: the held copy "
                         "was replaced; review it again")
    _install(pending, os.path.join(raw_dir, rel))
    lock["files"][rel] = {"sha256": digest, "size": os.path.getsize(os.path.join(raw_dir, rel)),
                          "url": info.get("url"), "recorded": _now(), "reviewed": True}
    del lock["pending"][rel]
    return True


def locked_ok(lock, rel, raw_dir=None):
    """True if raw/<rel> exists and matches its hash in the lock (only such files go into a build)."""
    raw_dir = raw_dir or RAW_DIR
    known = lock["files"].get(rel)
    path = os.path.join(raw_dir, rel)
    return bool(known) and os.path.exists(path) and sha256_file(path) == known.get("sha256")


def fetch_locked(lock, rel, urls, allowed_hosts, *, user_agent=None, max_bytes=256 << 20, check=None,
                 normalize=None, raw_dir=None, untrusted=False, max_time=1800, reason=None, content_key=None,
                 fallback_user_agent=None):
    """Download raw/<rel> from urls[0], falling back to the later URLs, and settle it against the lock.
    check(path) -> bool rejects a copy that is plainly the wrong kind of file (an HTML error page, say).
    normalize(path) rewrites a download in place before it is hashed (an archive's gzip wrapper). A copy that
    check or normalize fails on (a broken gzip, say) counts as a failed fetch from that URL.
    A primary URL that ends on another host after redirects is treated like a fallback for a first fetch: held for
    review, never recorded as trusted. untrusted=True treats every URL that way (no published hash to check).
    reason, when given, is what refresh.py --pending shows for a held copy. fallback_user_agent: see download_ex().
    Returns the settle_locked_file() result, or "failed" if no URL gave a usable file."""
    raw_dir = raw_dir or RAW_DIR
    incoming = os.path.join(raw_dir, ".incoming")
    os.makedirs(incoming, exist_ok=True)
    for n, url in enumerate(urls):
        fd, tmp = tempfile.mkstemp(dir=incoming, prefix=".dl-")
        os.close(fd)
        try:
            ok, effective = download_ex(url, tmp, allowed_hosts, user_agent=user_agent, max_bytes=max_bytes,
                                        max_time=max_time, fallback_user_agent=fallback_user_agent)
            if ok and normalize:
                normalize(tmp)
            if ok and check and not check(tmp):
                ok = False
            if ok:
                moved = host_of(effective) if host_of(effective) != host_of(url) else None
                return settle_locked_file(lock, rel, tmp, urls[0], from_fallback=n > 0 or untrusted, raw_dir=raw_dir,
                                          redirected_to=moved, reason=reason, content_key=content_key)
        except FetchError as exc:
            print(f"    {exc}", flush=True)
        except Exception as exc:  # a broken download (bad gzip, unreadable file) must not stop the other files
            print(f"    {rel}: unusable copy from {host_of(url)}: {exc!r}", flush=True)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return "failed"


def is_pdf(path):
    with open(path, "rb") as f:
        return f.read(5) == b"%PDF-"


# ---------- one run at a time ----------

_HELD = {}  # lock path -> a weak reference to the handle this process holds it with (dropping the handle frees the lock)


def take_lock(path=None):
    """An exclusive lock held for the life of the process (refresh.py and build_library.py share it), or None
    when another run holds it."""
    from library import oscompat  # flock on macOS, msvcrt.locking on Windows
    path = os.path.realpath(path or os.path.join(DATA_DIR, ".refresh.lock"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a")
    if not oscompat.try_lock(handle):
        handle.close()
        return None
    import weakref
    _HELD[path] = weakref.ref(handle)
    return handle


def holds_lock(path=None):
    """True if this process took the lock at path (default: the data folder's refresh lock) with take_lock() and
    has not closed it, so no other refresh or build can be running."""
    ref = _HELD.get(os.path.realpath(path or os.path.join(DATA_DIR, ".refresh.lock")))
    handle = ref() if ref else None
    return handle is not None and not handle.closed
