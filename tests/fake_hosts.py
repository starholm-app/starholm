"""A local HTTP server that stands in for the original hosts in the free library's tests (not a test itself:
run_all.py runs *_test.py files only).

FakeHosts serves files by (host, path): the Host header names the real host (download.kiwix.org, www.ready.gov ...)
and the connection goes to 127.0.0.1. Each file can honour range requests or ignore them, drop the connection after
some bytes, send slowly, or answer with another status. connect is the stand-in for library/downloader.CONNECT.

guard() makes sure no test reaches a real host: every socket connection to an address other than the loopback one
raises, and downloader.CONNECT itself refuses, so a download that was not given the stand-in fails instead of going
out. Call it before anything else.
"""
import http.client
import http.server
import ipaddress
import socket
import threading
import time

LOOPBACK = ("127.0.0.1", "::1", "localhost")
_real_connect = socket.socket.connect
_real_create = socket.create_connection


def _loopback(address):
    host = address[0] if isinstance(address, tuple) else address
    if not isinstance(host, str):
        return False
    if host in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class RealHostReached(AssertionError):
    pass


def guard():
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
            raise RealHostReached(f"a test tried to reach {address}")
        return _real_connect(sock, address)

    def create_connection(address, *args, **kwargs):
        if not _loopback(address):
            raise RealHostReached(f"a test tried to reach {address}")
        return _real_create(address, *args, **kwargs)

    socket.socket.connect = connect
    socket.create_connection = create_connection
    from library import downloader

    def refuse(host, port):
        raise RealHostReached(f"a download went to the real {host} without the stand-in")
    downloader.CONNECT = refuse


class File:
    """One file on the fake host. body: bytes. ranges: honour Range. drop_after: send only that many bytes of the
    body, then close (each time, for the first `drops` requests). delay: seconds between chunks of `step` bytes.
    status: answer with this status and no body instead. location: a redirect target. etag: None sends no ETag
    (and the server never sends Last-Modified). stall_at: after that many bytes, send nothing for `stall` seconds
    (once, for a request that starts after arm_stall), as a stalled network read."""

    def __init__(self, body=b"", ranges=True, drop_after=None, drops=1, delay=0.0, step=64, status=200,
                 location=None, etag='"v1"', stall_at=None, stall=0.0):
        self.body, self.ranges, self.drop_after, self.drops = body, ranges, drop_after, drops
        self.delay, self.step, self.status, self.location, self.etag = delay, step, status, location, etag
        self.stall_at, self.stall, self.armed = stall_at, stall, 0.0

    def arm_stall(self, at, seconds):
        """Stall the next request that starts from now on (not one still sending from an earlier test)."""
        self.stall_at, self.stall, self.armed = at, seconds, time.monotonic()


class FakeHosts:
    def __init__(self):
        self.files = {}
        self.log = []         # (host, path, Range header) of every request
        self.connected = []   # hosts the stand-in connect was asked for
        hosts = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                began = time.monotonic()
                host = (self.headers.get("Host") or "").split(":")[0]
                rng = self.headers.get("Range")
                hosts.log.append((host, self.path, rng))
                f = hosts.files.get((host, self.path))
                if f is None:
                    return self._plain(404)
                if f.location:
                    self.send_response(f.status)
                    self.send_header("Location", f.location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if f.status != 200:
                    return self._plain(f.status)
                body, start = f.body, 0
                if rng and f.ranges and rng.startswith("bytes=") and rng.endswith("-") \
                        and (not self.headers.get("If-Range") or self.headers.get("If-Range") == f.etag):
                    start = int(rng[6:-1])
                    if start >= len(body):
                        return self._plain(416)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    self.send_response(200)
                part = body[start:]
                self.send_header("Content-Length", str(len(part)))
                if f.etag:
                    self.send_header("ETag", f.etag)
                self.end_headers()
                limit = len(part)
                if f.drop_after is not None and f.drops > 0:
                    f.drops -= 1
                    limit = min(limit, f.drop_after)
                sent = 0
                try:
                    while sent < limit:
                        n = min(f.step, limit - sent)
                        self.wfile.write(part[sent:sent + n])
                        self.wfile.flush()
                        sent += n
                        if f.stall_at is not None and sent >= f.stall_at and began >= f.armed:
                            f.stall_at = None
                            time.sleep(f.stall)
                        if f.delay:
                            time.sleep(f.delay)
                except (BrokenPipeError, ConnectionResetError):
                    return
                if limit < len(part):
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            def _plain(self, status):
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        class Quiet(http.server.ThreadingHTTPServer):
            request_queue_size = 64  # eight download workers connect at once; the default 5 resets some under load
            def handle_error(self, request, client_address):
                pass  # a client that hung up on purpose (pause, a dropped connection)

        self.server = Quiet(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, url, body=b"", **kw):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        self.files[(parts.hostname, path)] = File(body, **kw)
        return self.files[(parts.hostname, path)]

    def connect(self, host, port):
        self.connected.append(host)
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def ranges_asked(self, path_end):
        return [r for h, p, r in self.log if p.endswith(path_end)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def catalog_xml(entries):
    """A Kiwix OPDS catalog naming each (name, flavour, path) with its .meta4 link."""
    items = "".join(
        f"<entry><name>{name}</name><flavour>{flavour}</flavour>"
        f'<link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim" '
        f'href="https://download.kiwix.org{path}.meta4" /></entry>' for name, flavour, path in entries)
    return f"<feed>{items}</feed>".encode()


def meta4(name, size, sha256):
    return (f'<?xml version="1.0" encoding="UTF-8"?><metalink xmlns="urn:ietf:params:xml:ns:metalink">'
            f'<file name="{name}"><size>{size}</size><hash type="sha-256">{sha256}</hash></file></metalink>').encode()
