"""Resumable file downloads with progress, pause and checksums: the free library's raw sources today, the upgrade's
content packs later. Python only (no curl), so the app's server can show progress per file and pause between two
chunks; the rules are library/safe_fetch.py's.

The interface:

    fetch(url, dest, allowed_hosts, *, size=None, sha256=None, progress=None, stop=None, follow=True,
          max_bytes=MAX_BYTES, check=None, user_agent=HONEST_UA, fallback_user_agent=None, same_file=None) -> Result
        Download url into dest. Bytes go to dest + ".part" first (with dest + ".part.json" saying what they are), so
        a later call with the same url, size and sha256 continues from that byte offset with an HTTP range request
        and If-Range, in this process or after a restart; when the host sent no ETag or Last-Modified, it starts
        again from zero instead. When the server ignores the range, or the file changed on the server
        (If-Range), the download starts again from zero. progress(done, total) is called after each chunk (total is
        None when unknown); stop() is asked after each chunk and, when it says True, the call raises Stopped with
        the partial file kept. Once complete the file must have size bytes and the SHA-256 sha256 (when given), and
        check(path) must accept it; otherwise it is deleted and ChecksumError is raised. Only then is it moved to
        dest. Returns Result(bytes, sha256, url): the size, the file's SHA-256 and the address it finally came from.
        same_file: what names the file when its address changes between calls (a short-lived signed link replaced by
        a fresh one): a partial file is continued when same_file, size and sha256 match, whatever the address.

    fetch_bytes(url, allowed_hosts, *, max_bytes, follow=False) -> bytes
        A small file (a catalog, a checksum file) in memory, refused when larger than max_bytes.

    fetch_segment(url, part, start, end, allowed_hosts, *, total, validator=None, progress=None, stop=None,
                  keep=False) -> validator
        Bytes start..end (inclusive) of a file of `total` bytes, written at that offset into the existing file
        part, with a range request (If-Match validator, when given, so a file changed on the server is refused).
        For splitting one big file over several connections; the caller checks the whole file's SHA-256 at the
        end. Returns the server's validator (ETag or Last-Modified). An incomplete segment raises (DownloadError,
        Stopped) and is simply asked for again.

    fetch_frames(url, part, allowed_hosts, *, frame_size, frames, first, last, size, stored_size, validator=None,
                 progress=None, stop=None, keep=False) -> validator
        A compressed (zstd) file stored as independent frames, each unpacking to frame_size bytes (the last one to
        the rest of size): frames first..last (inclusive) with one range request for their compressed bytes, each
        frame unpacked as it arrives and written at its unpacked offset (first * frame_size on) into part, which is
        made when it doesn't exist. frames lists every frame's compressed length; stored_size is the stored file's
        length. A frame that doesn't unpack to exactly its length raises Unpacked (not retried; the caller checks
        the whole file's SHA-256 at the end anyway). progress(n) gets each chunk written. Returns the validator.

    close_kept()
        Close the connections this thread kept open (keep=True).

    partial_size(dest) -> int
        How many bytes of dest are already downloaded (0 when none), for showing progress before a resume.

    discard(dest)
        Delete dest's partial download.

Errors: safe_fetch.FetchError (the address is refused: not https, not on allowed_hosts, a user name, another
port), DownloadError (the network or the server failed; the partial file is kept, so calling again resumes),
ChecksumError (a DownloadError: the finished file was wrong and is deleted, so calling again starts over), Stopped.

The rules kept from safe_fetch.py: https only, redirects included; the first address on an allow-list; at most
MAX_REDIRECTS redirects, never to a host written as an IP address, and for a file with no SHA-256 to check (and
fetch_bytes) only to allowed_hosts; certificates always checked (the system's trust store through truststore when installed,
else Python's defaults); a size cap on every download; a stall limit (STALL_SECONDS without a byte); no proxy (the
connection goes straight to the host); the app's own User-Agent, with one retry as Safari only when a server refuses
it with 403 or 406 and the caller allows it. Tests replace CONNECT, which opens every connection.

keep=True (fetch, fetch_segment) keeps the connection open after a complete answer, in this thread, and the next
request to the same host reuses it: a new TLS connection for every file of a pack costs a handshake and TCP's slow
start each time, which measured on 25 November 2026 as 5.4 MB/s for 12 MB pieces against 12.8 MB/s on one reused
connection. A kept connection the server has closed in the meantime is replaced once, at once.
"""
import collections
import http.client
import ipaddress
import json
import os
import re
import ssl
import threading
import urllib.parse

from library import safe_fetch
from library.safe_fetch import FetchError, HONEST_UA, REFUSED

MAX_REDIRECTS = 5
STALL_SECONDS = 120            # a read that waits this long for a byte fails the attempt (the partial file stays)
CHUNK = 1 << 20                # bytes read and written at a time; stop() and progress() run between chunks
MAX_BYTES = 64 << 30           # the cap when the expected size is not known
REDIRECTS = (301, 302, 303, 307, 308)

Result = collections.namedtuple("Result", "bytes sha256 url")


class DownloadError(Exception):
    """The download failed; a partial file, if any, is kept for the next attempt. status: the HTTP status when the
    server answered with one the download can't use (an expired link's 410, say), else None."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class ChecksumError(DownloadError):
    """The finished file had the wrong size or SHA-256, or check() refused it; it was deleted."""


class Unpacked(DownloadError):
    """A compressed frame didn't unpack, or unpacked to another length than the pack says: the bytes are wrong."""


class ConnectionDropped(DownloadError):
    """The connection failed or was reset before the whole answer came (no HTTP status to go on). Worth trying again:
    the partial file is kept, so the next attempt carries on from the bytes already in."""


class Stopped(Exception):
    """stop() asked for a pause; the partial file is kept."""


_context = []


def tls_context():
    """Certificates checked against the system's trust store (truststore), or Python's defaults without it."""
    if not _context:
        try:
            import truststore
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            ctx = ssl.create_default_context()
        _context.append(ctx)
    return _context[0]


def _https_connection(host, port):
    return http.client.HTTPSConnection(host, port or 443, timeout=STALL_SECONDS, context=tls_context())


CONNECT = _https_connection  # (host, port) -> an http.client connection; tests point it at a local server


def ip_literal(host):
    """True for a host written as an address rather than a name: IPv4 or IPv6 (loopback, private and link-local
    included), and the numeric forms a resolver reads as IPv4 ("2130706433", "127.1", "0x7f.0.0.1")."""
    host = (host or "").strip("[]").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    last = host.rsplit(".", 1)[-1]
    return bool(re.fullmatch(r"[0-9]+|0x[0-9a-f]*", last))


def check_redirect(url, hosts=None):
    """A redirect target: an https address with no user name and no other port, never on a host written as an IP
    address. hosts: the only hosts it may go to (a file with no published checksum stays on its source's own
    hosts); None: any host named by name (a file whose size and SHA-256 are checked, such as a ZIM on Kiwix's
    mirrors, as curl's --proto-redir =https in safe_fetch.py)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if ip_literal(host):
        raise FetchError(f"refusing a redirect to an IP address: {url}")
    return safe_fetch.check_url(url, frozenset(hosts) if hosts is not None else frozenset({host}))


_kept = threading.local()  # this thread's open connections: {(host, port, opener): connection}


def _pool():
    if not hasattr(_kept, "conns"):
        _kept.conns = {}
    return _kept.conns


def close_kept():
    """Close every connection this thread kept open."""
    pool = _pool()
    while pool:
        _, conn = pool.popitem()
        try:
            conn.close()
        except OSError:
            pass


def _keep(conn, resp, host, port, connect):
    """After a complete answer: keep conn for this thread's next request to host, unless the server closes it."""
    if resp is not None and not resp.will_close and resp.isclosed():
        old = _pool().pop((host, port, connect or CONNECT), None)
        if old is not None and old is not conn:
            old.close()
        _pool()[(host, port, connect or CONNECT)] = conn
    else:
        conn.close()


def _open(url, allowed_hosts, headers, follow, connect, redirect_hosts=None, keep=False):
    """(response, connection, final url) for a GET of url, following at most MAX_REDIRECTS https redirects (to
    redirect_hosts only, when given; see check_redirect). keep: reuse this thread's kept connection to the host."""
    safe_fetch.check_url(url, allowed_hosts)
    for hop in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        kept = _pool().pop((parts.hostname, parts.port, connect or CONNECT), None) if keep else None
        conn = kept or (connect or CONNECT)(parts.hostname, parts.port)
        try:
            try:
                conn.request("GET", target, headers={"Host": parts.hostname, **headers})
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException):
                if kept is None:
                    raise
                conn.close()  # the server closed the kept connection: once more on a fresh one
                conn = (connect or CONNECT)(parts.hostname, parts.port)
                conn.request("GET", target, headers={"Host": parts.hostname, **headers})
                resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise ConnectionDropped(f"{parts.hostname}: {exc}") from None
        if resp.status not in REDIRECTS:
            return resp, conn, url
        location = resp.getheader("Location") or ""
        conn.close()
        if not follow:
            raise DownloadError(f"{parts.hostname} redirected (HTTP {resp.status}), and redirects are off here")
        if hop == MAX_REDIRECTS:
            raise DownloadError(f"more than {MAX_REDIRECTS} redirects from {safe_fetch.host_of(url)}")
        url = check_redirect(urllib.parse.urljoin(url, location.strip()), redirect_hosts)
    raise DownloadError("too many redirects")  # not reached


def fetch_bytes(url, allowed_hosts, *, max_bytes, follow=False, user_agent=HONEST_UA, connect=None):
    resp, conn, _ = _open(url, allowed_hosts, {"User-Agent": user_agent, "Accept-Encoding": "identity"}, follow, connect,
                          redirect_hosts=allowed_hosts)  # no checksum vouches for it: stay on its own hosts
    try:
        if resp.status != 200:
            raise DownloadError(f"{safe_fetch.host_of(url)}: HTTP {resp.status}")
        length = resp.getheader("Content-Length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise DownloadError(f"{url} is larger than {max_bytes} bytes")
        data = resp.read(max_bytes + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise DownloadError(f"{safe_fetch.host_of(url)}: {exc}") from None
    finally:
        conn.close()
    if len(data) > max_bytes:
        raise DownloadError(f"{url} is larger than {max_bytes} bytes")
    return data


# ---------- partial files ----------

def _part(dest):
    return dest + ".part"


def _sidecar(dest):
    return dest + ".part.json"


def _read_sidecar(dest):
    try:
        with open(_sidecar(dest), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_sidecar(dest, data):
    tmp = _sidecar(dest) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _sidecar(dest))


def discard(dest):
    for path in (_part(dest), _sidecar(dest), _sidecar(dest) + ".tmp"):
        if os.path.lexists(path):
            os.remove(path)


def partial_size(dest):
    try:
        return os.path.getsize(_part(dest))
    except OSError:
        return 0


CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")


def fetch(url, dest, allowed_hosts, *, size=None, sha256=None, progress=None, stop=None, follow=True,
          max_bytes=MAX_BYTES, check=None, user_agent=HONEST_UA, fallback_user_agent=None, connect=None,
          same_file=None, keep=False):
    sha256 = sha256.lower() if sha256 else None
    if sha256 and not safe_fetch.SHA256_RE.match(sha256):
        raise FetchError(f"not a SHA-256: {sha256!r}")
    cap = int(size) if size else int(max_bytes)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    meta = {"url": same_file or url, "size": size, "sha256": sha256}
    known = _read_sidecar(dest)
    if any(known.get(k) != v for k, v in meta.items()):
        discard(dest)  # a partial file of something else (another address, a newer file): start over
        known = {}
    agents = [user_agent] + ([fallback_user_agent] if fallback_user_agent and fallback_user_agent != user_agent else [])
    restarted = False
    final = url
    while True:
        offset = partial_size(dest)
        if size and offset > size:
            discard(dest)
            offset = 0
        if offset and not known.get("validator"):
            # the host named no version (no ETag, no Last-Modified): the rest might come from another file, so the
            # file starts again from zero rather than splicing two files together
            discard(dest)
            offset = 0
        if size and offset == size:
            break  # every byte is here already (a stop right at the end): only the checks are left
        headers = {"User-Agent": agents[0], "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = known["validator"]
        resp, conn, final = _open(url, allowed_hosts, headers, follow, connect,
                                  redirect_hosts=None if sha256 else allowed_hosts, keep=keep)
        complete = False
        try:
            if resp.status in (int(c) for c in REFUSED) and len(agents) > 1:
                agents = agents[1:]  # refused the app's own name: once more as Safari
                continue
            if resp.status == 416 and offset and not restarted:
                discard(dest)  # the server has fewer bytes than the partial file: start over once
                known, restarted = {}, True
                continue
            if resp.status not in (200, 206):
                raise DownloadError(f"{safe_fetch.host_of(final)}: HTTP {resp.status}", resp.status)
            if resp.status == 206:
                m = CONTENT_RANGE.match(resp.getheader("Content-Range") or "")
                if not m or int(m.group(1)) != offset:
                    raise DownloadError(f"{safe_fetch.host_of(final)} sent the wrong part of the file")
                if size and m.group(3) != "*" and int(m.group(3)) != size:
                    raise DownloadError(f"{safe_fetch.host_of(final)} offers {m.group(3)} bytes, not {size}")
            else:
                offset = 0  # no range (none asked, or the server ignored it or the file changed): from zero
            length = resp.getheader("Content-Length")
            expect = int(length) if length and length.isdigit() else None
            if expect is not None and offset + expect > cap:
                raise DownloadError(f"{safe_fetch.host_of(final)} offers more than the {cap:,} bytes expected")
            validator = resp.getheader("ETag") or resp.getheader("Last-Modified")
            if validator and validator.startswith("W/"):
                validator = None  # a weak ETag cannot guard a range
            known = dict(meta, validator=validator)
            _write_sidecar(dest, known)
            total = size or (offset + expect if expect is not None else None)
            done = offset
            got = 0
            with open(_part(dest), "r+b" if offset else "wb") as out:
                out.seek(offset)
                out.truncate()
                try:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        out.flush()  # on disk at once, so a crash keeps every byte that came and progress is true
                        got += len(chunk)
                        done += len(chunk)
                        if done > cap:
                            out.close()
                            discard(dest)
                            raise ChecksumError(f"{safe_fetch.host_of(final)} sent more than {cap:,} bytes")
                        if progress:
                            progress(done, total)
                        if stop and stop():
                            raise Stopped()
                except (OSError, http.client.HTTPException) as exc:
                    raise ConnectionDropped(f"{safe_fetch.host_of(final)}: the connection dropped after "
                                            f"{done:,} bytes ({exc.__class__.__name__})") from None
            if expect is not None and got < expect:
                raise ConnectionDropped(f"{safe_fetch.host_of(final)}: the connection dropped after {done:,} bytes")
            complete = True
            break
        finally:
            if keep and complete:
                fp = urllib.parse.urlsplit(final)
                _keep(conn, resp, fp.hostname, fp.port, connect)
            else:
                conn.close()
    path = _part(dest)
    have = os.path.getsize(path)
    if size and have != size:
        discard(dest)
        raise ChecksumError(f"{os.path.basename(dest)}: {have:,} bytes, expected {size:,}")
    digest = safe_fetch.sha256_file(path)
    if sha256 and digest != sha256:
        discard(dest)
        raise ChecksumError(f"{os.path.basename(dest)}: the SHA-256 does not match the one published")
    if check and not check(path):
        discard(dest)
        raise ChecksumError(f"{os.path.basename(dest)} is not the kind of file expected (an error page?)")
    os.replace(path, dest)
    discard(dest)
    return Result(have, digest, final)


def fetch_segment(url, part, start, end, allowed_hosts, *, total, validator=None, progress=None, stop=None,
                  keep=False, connect=None, user_agent=HONEST_UA):
    """Bytes start..end of a file of `total` bytes into part (which exists, `total` bytes long) at their offset.
    progress(n) gets each chunk's size. Returns the server's validator."""
    headers = {"User-Agent": user_agent, "Accept-Encoding": "identity", "Range": f"bytes={start}-{end}"}
    if validator:
        headers["If-Match"] = validator
    resp, conn, final = _open(url, allowed_hosts, headers, True, connect, redirect_hosts=None, keep=keep)
    complete = False
    host = safe_fetch.host_of(final)
    try:
        if resp.status != 206:
            raise DownloadError(f"{host}: HTTP {resp.status} for a part of the file", resp.status)
        m = CONTENT_RANGE.match(resp.getheader("Content-Range") or "")
        if not m or int(m.group(1)) != start or int(m.group(2)) != end or m.group(3) != str(total):
            raise DownloadError(f"{host} sent the wrong part of the file")
        got_validator = resp.getheader("ETag") or resp.getheader("Last-Modified")
        if validator and got_validator and got_validator != validator:
            raise DownloadError(f"{host}: the file changed on the server", 412)
        want, got = end - start + 1, 0
        with open(part, "r+b") as out:
            out.seek(start)
            try:
                while got < want:
                    chunk = resp.read(min(CHUNK, want - got))
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(len(chunk))
                    if stop and stop():
                        raise Stopped()
            except (OSError, http.client.HTTPException) as exc:
                raise ConnectionDropped(f"{host}: the connection dropped ({exc.__class__.__name__})") from None
        if got < want:
            raise ConnectionDropped(f"{host}: the connection dropped after {got:,} bytes of a part")
        resp.read()  # nothing should be left; reading it lets the connection be kept
        complete = True
        return got_validator or validator
    finally:
        if keep and complete:
            fp = urllib.parse.urlsplit(final)
            _keep(conn, resp, fp.hostname, fp.port, connect)
        else:
            conn.close()


class _FrameBytes:
    """The next n compressed bytes of resp, as a file for zstandard's stream_reader; a network failure or a short
    answer raises ConnectionDropped."""

    def __init__(self, resp, n, host):
        self.resp, self.left, self.host = resp, n, host

    def read(self, size=-1):
        if self.left <= 0:
            return b""
        want = self.left if size is None or size < 0 else min(size, self.left)
        try:
            chunk = self.resp.read(want)
        except (OSError, http.client.HTTPException) as exc:
            raise ConnectionDropped(f"{self.host}: the connection dropped ({exc.__class__.__name__})") from None
        if not chunk:
            raise ConnectionDropped(f"{self.host}: the connection dropped in a compressed part")
        self.left -= len(chunk)
        return chunk


def fetch_frames(url, part, allowed_hosts, *, frame_size, frames, first, last, size, stored_size, validator=None,
                 progress=None, stop=None, keep=False, connect=None, user_agent=HONEST_UA):
    import zstandard  # only the compressed packs need it

    if not (0 <= first <= last < len(frames)):
        raise ValueError("no such frames")
    zstart = sum(frames[:first])
    zend = zstart + sum(frames[first:last + 1]) - 1
    headers = {"User-Agent": user_agent, "Accept-Encoding": "identity", "Range": f"bytes={zstart}-{zend}"}
    if validator:
        headers["If-Match"] = validator
    resp, conn, final = _open(url, allowed_hosts, headers, True, connect, redirect_hosts=None, keep=keep)
    complete = False
    host = safe_fetch.host_of(final)
    try:
        if resp.status != 206:
            raise DownloadError(f"{host}: HTTP {resp.status} for a part of the file", resp.status)
        m = CONTENT_RANGE.match(resp.getheader("Content-Range") or "")
        if not m or int(m.group(1)) != zstart or int(m.group(2)) != zend or m.group(3) != str(stored_size):
            raise DownloadError(f"{host} sent the wrong part of the file")
        got_validator = resp.getheader("ETag") or resp.getheader("Last-Modified")
        if validator and got_validator and got_validator != validator:
            raise DownloadError(f"{host}: the file changed on the server", 412)
        dctx = zstandard.ZstdDecompressor()
        mode = "r+b" if os.path.exists(part) else "w+b"
        with open(part, mode) as out:
            out.seek(first * frame_size)
            for k in range(first, last + 1):
                want = min(frame_size, size - k * frame_size)
                source = _FrameBytes(resp, frames[k], host)
                got = 0
                try:
                    reader = dctx.stream_reader(source, read_size=CHUNK, read_across_frames=False, closefd=False)
                    while True:
                        chunk = reader.read(CHUNK)
                        if not chunk:
                            break
                        got += len(chunk)
                        if got > want:
                            raise Unpacked(f"{os.path.basename(part)}: a part unpacks to more bytes than it should")
                        out.write(chunk)
                        if progress:
                            progress(len(chunk))
                        if stop and stop():
                            raise Stopped()
                except zstandard.ZstdError as exc:
                    raise Unpacked(f"{os.path.basename(part)}: a part doesn't unpack ({exc})") from None
                except OSError as exc:  # the disk (the network's errors are ConnectionDropped already)
                    raise DownloadError(f"{os.path.basename(part)}: {exc}") from None
                if got != want or source.left:  # fewer bytes, or compressed bytes left over: not the frame listed
                    raise Unpacked(f"{os.path.basename(part)}: a part doesn't unpack to its length")
        resp.read()  # nothing should be left; reading it lets the connection be kept
        complete = True
        return got_validator or validator
    finally:
        if keep and complete:
            fp = urllib.parse.urlsplit(final)
            _keep(conn, resp, fp.hostname, fp.port, connect)
        else:
            conn.close()
