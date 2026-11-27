"""The Ollama lock: with a hostile OLLAMA_HOST and every proxy variable pointing at a decoy, core.py still only connects
to 127.0.0.1:11434, never through a proxy, never follows a redirect, and keeps num_ctx at 32768. The server starts only
Homebrew's Ollama by its absolute path, and none of the backend files still holds update, download or Windows code.
No network and no Ollama: connections are intercepted before they leave the process.

Usage: .venv/bin/python tests/backend_loopback_test.py"""
import os
import re
import socket
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()

# A decoy "proxy" and "remote Ollama": anything reaching it is a leak.
decoy = socket.socket()
decoy.bind(("127.0.0.1", 0))
decoy.listen(8)
decoy.setblocking(False)
DECOY = f"127.0.0.1:{decoy.getsockname()[1]}"
os.environ.update(OLLAMA_HOST="ollama.attacker.example:11434", http_proxy=f"http://{DECOY}",
                  HTTP_PROXY=f"http://{DECOY}", https_proxy=f"http://{DECOY}", HTTPS_PROXY=f"http://{DECOY}",
                  all_proxy=f"http://{DECOY}", ALL_PROXY=f"http://{DECOY}", no_proxy="", NO_PROXY="",
                  SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()  # temporary folders only

# Every outgoing TCP connection made through socket.create_connection (http.client's way) is recorded and refused.
attempts = []
real_create_connection = socket.create_connection


def recording_create_connection(address, *args, **kwargs):
    attempts.append(tuple(address[:2]))
    raise ConnectionRefusedError(61, "refused by the test")


socket.create_connection = recording_create_connection

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import core  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


def leaked():
    try:
        conn, _ = decoy.accept()
        conn.close()
        return True
    except (BlockingIOError, OSError):
        return False


check("HOST ignores OLLAMA_HOST", core.HOST == "http://127.0.0.1:11434", core.HOST)
check("the address is loopback", core.OLLAMA_ADDRESS == ("127.0.0.1", 11434), core.OLLAMA_ADDRESS)

check("installed_models answers None when Ollama refuses", core.installed_models() is None)
try:
    core._post("/api/show", {"model": "granite4.2:8b"})
    check("_post raises when Ollama refuses", False, "no exception")
except OSError:
    check("_post raises when Ollama refuses", True)
check("capabilities is empty when Ollama refuses", core.capabilities("granite4.2:8b") == set())
check("every connection went to 127.0.0.1:11434", attempts and set(attempts) == {("127.0.0.1", 11434)}, attempts)
check("nothing reached the decoy proxy or host", not leaked())

# ProxyHandler({}) stands in for urllib's default one (which reads the proxy variables); having no proxies, it adds no
# handler methods, so the opener ends up with no proxy handling at all.
proxies = [h for h in core._opener.handlers if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
check("the opener has no proxy handler with proxies", not proxies, proxies)
check("urllib's default opener would have used the decoy proxy (so the test means something)",
      any(isinstance(h, urllib.request.ProxyHandler) and h.proxies
          for h in urllib.request.build_opener().handlers))
redirects = [h for h in core._opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
check("the opener refuses redirects", redirects and all(isinstance(h, core._NoRedirect) for h in redirects)
      and redirects[0].redirect_request(None, None, 302, "Found", {}, "http://evil.example/") is None, redirects)

try:
    core.ollama_request("/api/tags", method="PUT")
    check("ollama_request takes no method but GET, POST and DELETE", False, "no exception")
except ValueError:
    check("ollama_request takes no method but GET, POST and DELETE", True)
for bad in ("http://evil.example/api/tags", "//evil.example/api/tags", "/api@evil.example/tags", "api/tags",
            "/v1/models", None):
    try:
        core.ollama_request(bad)
        check(f"ollama_request refuses {bad!r}", False, "no exception")
    except ValueError:
        check(f"ollama_request refuses {bad!r}", True)

# num_ctx stays 32768 on every request the answer makes
seen_options = []


def fake_stream(model, messages, tools, think, options):
    seen_options.append(dict(options))
    yield {"message": {"content": "Boil it for one minute."}}
    yield {"done": True, "eval_count": 3, "eval_duration": 1_000_000}


core._stream = fake_stream
events = list(core.chat_events("granite4.2:8b", None, [{"role": "system", "content": "s"},
                                                       {"role": "user", "content": "How long do I boil water?"}],
                               caps=set(), length="long"))
check("the answer came through", any(e["type"] == "done" for e in events), [e["type"] for e in events])
check("num_ctx is 32768", seen_options and all(o.get("num_ctx") == 32768 for o in seen_options), seen_options)
check("NUM_CTX is 32768", core.NUM_CTX == 32768)

# The server starts Homebrew's Ollama only, by its absolute path, listening on loopback, with no proxy variables.
import server  # noqa: E402

started = []


class FakePopen:
    def __init__(self, args, **kwargs):
        started.append((args, kwargs))


server.subprocess.Popen = FakePopen
server.ollama_installed = lambda: True
found_bin = server.OLLAMA_BIN
server.OLLAMA_BIN = "/opt/homebrew/bin/ollama"  # Homebrew's (an app install is opened as an app instead)
server.ollama_started_at[0] = 0
server.start_ollama()
args, kwargs = started[0] if started else ([], {})
check("start_ollama runs OLLAMA_BIN serve", args == [server.OLLAMA_BIN, "serve"], args)
server.OLLAMA_BIN = found_bin
env = kwargs.get("env") or {}
check("Ollama is told to listen on 127.0.0.1:11434", env.get("OLLAMA_HOST") == "127.0.0.1:11434", env.get("OLLAMA_HOST"))
check("Ollama gets no proxy variables",
      not [k for k in env if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy")],
      [k for k in env if "proxy" in k.lower()])
check("OLLAMA_BIN is an absolute path: a usual install place first, then an absolute PATH folder outside this checkout",
      os.path.isabs(server.OLLAMA_BIN) and all(os.path.isabs(p) for p in server.OLLAMA_CANDIDATES)
      and (sandbox.WINDOWS or server.OLLAMA_CANDIDATES[0] == "/opt/homebrew/bin/ollama")
      and all(os.path.isabs(p) and not p.startswith(ROOT + os.sep) for p in server.path_candidates()),
      server.OLLAMA_BIN)
started.clear()
server.ollama_installed = lambda: False
server.ollama_started_at[0] = 0
server.start_ollama()
check("nothing is started when Homebrew's Ollama isn't installed", not started, started)
check("still nothing reached the decoy", not leaked())

# Starting the server makes no name lookup. HTTPServer's own server_bind() calls socket.getfqdn("127.0.0.1"), a
# reverse DNS lookup, and on a Mac that alone makes macOS ask "Allow Starholm to find devices on local networks?"
# (shown with a test app that made only that call). QuietServer binds and listens without it.
lookups = []


def record_lookup(event, args):
    if event in ("socket.gethostbyaddr", "socket.gethostbyname", "socket.getnameinfo", "socket.getaddrinfo"):
        lookups.append((event, args))


sys.addaudithook(record_lookup)
httpd = server.make_server(0)
bound = httpd.server_address
httpd.server_close()
check("the server listens on 127.0.0.1", bound[0] == "127.0.0.1", bound)
check("starting the server makes no name lookup", not lookups, lookups)
check("its server_name is the address, not a looked-up name", httpd.server_name == "127.0.0.1", httpd.server_name)

# No update, download, manifest, Windows or bundled-engine code is left in the backend files. The one download is a
# model the person picks in Settings > Model, pulled by the local Ollama: app/model_setup.py asks for it through
# core.ollama_request (127.0.0.1:11434 only), and nowhere else names Ollama's pull API. The model update check
# (model_setup.Updates, W205) only reads a model's digest from the Ollama registry; it downloads nothing itself.
FILES = ["app/server.py", "app/model_setup.py", "core.py", "memory.py", "chat.py", "library/myfiles.py"]
with open(os.path.join(ROOT, "app", "model_setup.py"), encoding="utf-8") as f:
    setup_code = f.read()
check("the model download goes only through core.ollama_request",
      re.findall(r'.*"/api/pull".*', setup_code) == ['            with request("/api/pull", {"model": model, "stream": True}, '
                                                    'timeout=PULL_READ_TIMEOUT) as resp:']
      and "request = self._request or core.ollama_request" in setup_code and "urllib" not in setup_code
      and "socket.create" not in setup_code)
check("the model update check contacts the Ollama registry only, and only reads a digest (GET, never a pull)",
      re.findall(r'REGISTRY = "([^"]+)"', setup_code) == ["registry.ollama.ai"]
      and re.findall(r'conn\.request\("([A-Z]+)"', setup_code) == ["GET"]
      and "self.connect(REGISTRY, 443)" in setup_code)
# "Set up the AI" (app/ollama_install.py) is the one place that downloads a program: the official Ollama, from
# Ollama's own GitHub releases, when the person presses the button. It reads the release's facts from api.github.com,
# downloads from github.com through library/downloader.py with the pinned SHA-256, and only shows ollama.com.
with open(os.path.join(ROOT, "app", "ollama_install.py"), encoding="utf-8") as f:
    install_code = f.read()
check("the Ollama setup names only GitHub's API, Ollama's GitHub releases and ollama.com (shown, never fetched)",
      set(re.findall(r'https://([^/"]+)', install_code)) == {"api.github.com", "github.com", "ollama.com"}
      and 'API_HOSTS = frozenset({"api.github.com"})' in install_code
      and 'DOWNLOAD_HOSTS = frozenset({"github.com"})' in install_code
      and "sha256=asset[\"sha256\"]" in install_code and "GUIDE_URL" not in install_code.split("class Setup")[1])
# The Windows app's platform branches (PLATFORM == "win32", os.startfile, WebView2) are allowed since the Windows
# port; registry access, PowerShell and the network update code that the first version had are still not.
FORBIDDEN = [r"urlopen\(", r"os\.name\s*==\s*.nt",
             r"\bwinreg\b", r"update\.json", r"library-packs", r"MANIFEST", r"ENGINE", r"engine\.json",
             r"github\.io", r"https://", r"truststore", r"xdg-open", r"powershell"]
for path in FILES:
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        text = f.read()
    hits = [p for p in FORBIDDEN if re.search(p, text)]
    if path != "app/model_setup.py" and re.search(r"/api/pull", text):
        hits.append("/api/pull")
    check(f"{path} has no update or download code", not hits, hits)

decoy.close()
socket.create_connection = real_create_connection
print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
