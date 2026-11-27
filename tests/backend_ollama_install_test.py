""""Set up the AI" (app/ollama_install.py and its wiring in app/server.py): the release asset picked per platform, the
SHA-256 check passing and failing (the file deleted and the setup stopped), the skip when Ollama is already
installed, the plain message for each failing step, the Homebrew-only upgrade message, the ~/Applications search
path, the Windows path (built, not verified), and the whole Mac flow end to end through the app's own API: from "not
found" to the hand-off to Ollama's /api/pull.

Nothing is downloaded and no Ollama runs: GitHub is a local server reached through library/downloader.py's CONNECT
(every connection is recorded), the "Ollama-darwin.zip" is a small zip made here holding a fake Ollama.app, it is
unzipped with the real /usr/bin/ditto into a temporary folder, `open -g -j` is recorded instead of run, and "Ollama"
is a fake that starts answering once that open is recorded.

Usage: .venv/bin/python tests/backend_ollama_install_test.py"""
import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()
sandbox.mac_only("the Mac flow unzips with /usr/bin/ditto and opens Ollama.app")
os.environ.pop("OLLAMA_HOST", None)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import emergency  # noqa: E402

emergency.LOCALTIME = os.path.join(TMP, "no-localtime")
emergency.GLOBAL_PREFS = os.path.join(TMP, "no-prefs.plist")
import core  # noqa: E402
from library import downloader  # noqa: E402
import ollama_install as oi  # noqa: E402
import server  # noqa: E402

server.REFRESH_PLIST = os.path.join(TMP, "no.plist")
server.REFRESH_STATUS = os.path.join(TMP, "no-status.json")
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


def wait_for(test, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        if test():
            return True
        time.sleep(0.02)
    return test()


# ---- a fake Ollama.app, zipped the way Ollama ships it ----
SRC = os.path.join(TMP, "src")
APP_BIN = os.path.join(SRC, "Ollama.app", "Contents", "Resources", "ollama")
os.makedirs(os.path.dirname(APP_BIN))
with open(APP_BIN, "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
os.chmod(APP_BIN, 0o755)
os.symlink("Resources/ollama", os.path.join(SRC, "Ollama.app", "Contents", "link-to-ollama"))  # ditto keeps links
ZIP = os.path.join(TMP, "Ollama-darwin.zip")
subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", os.path.join(SRC, "Ollama.app"), ZIP], check=True)
with open(ZIP, "rb") as f:
    ZIP_BYTES = f.read()
ZIP_SHA = hashlib.sha256(ZIP_BYTES).hexdigest()
TAG = "v0.99.0"  # the fake "latest" release; the app pins no version
SETUP_EXE = b"setup.exe"

# ---- a fake GitHub: the latest release's facts, and its files (the zip, or the wrong bytes) ----
GH = {"serve": ZIP_BYTES, "digest": "sha256:" + ZIP_SHA, "api_status": 200, "file_status": 200, "seen": [],
      "prerelease": False, "extra": []}


def release():
    assets = [{"name": "ollama-darwin.tgz", "size": 5, "digest": "sha256:" + "1" * 64,
               "browser_download_url": f"{oi.DOWNLOAD_PREFIX}{TAG}/ollama-darwin.tgz"},
              {"name": "Ollama-darwin.zip", "size": len(ZIP_BYTES), "digest": GH["digest"],
               "browser_download_url": f"{oi.DOWNLOAD_PREFIX}{TAG}/Ollama-darwin.zip"},
              {"name": "OllamaSetup.exe", "size": len(SETUP_EXE), "digest": "sha256:" + hashlib.sha256(SETUP_EXE).hexdigest(),
               "browser_download_url": f"{oi.DOWNLOAD_PREFIX}{TAG}/OllamaSetup.exe"}] + GH["extra"]
    if GH["digest"] is None:
        del assets[1]["digest"]
    return {"tag_name": TAG, "draft": False, "prerelease": GH["prerelease"], "assets": assets}


class FakeGitHub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        GH["seen"].append((self.headers.get("Host"), self.path))
        if self.path == "/repos/ollama/ollama/releases/latest":
            body, status = json.dumps(release()).encode(), GH["api_status"]
        elif self.path == f"/ollama/ollama/releases/download/{TAG}/Ollama-darwin.zip":
            body, status = GH["serve"], GH["file_status"]
        elif self.path == f"/ollama/ollama/releases/download/{TAG}/OllamaSetup.exe":
            body, status = SETUP_EXE, 200
        else:
            body, status = b"not found", 404
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"v1"')
        self.end_headers()
        self.wfile.write(body)


gh = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
gh.daemon_threads = True
threading.Thread(target=gh.serve_forever, daemon=True).start()
CONNECTED = []  # every host the setup's downloads connected to


def fake_connect(host, port):
    CONNECTED.append(host)
    return http.client.HTTPConnection("127.0.0.1", gh.server_address[1], timeout=10)


downloader.CONNECT = fake_connect


def find_error(platform="darwin"):
    try:
        oi.published_asset(platform)
        return None
    except oi.SetupError as exc:
        return exc


# ======================= the latest release's asset per platform =======================
check("a Mac gets Ollama-darwin.zip", oi.asset_name("darwin") == "Ollama-darwin.zip")
check("Windows gets OllamaSetup.exe", oi.asset_name("win32") == "OllamaSetup.exe" and oi.asset_name("windows") == "OllamaSetup.exe")
check("Linux and others get nothing (manual route)", oi.asset_name("linux") is None and oi.asset_name("freebsd14") is None)
check("the latest stable release from Ollama's own GitHub only, no version pinned",
      oi.RELEASE_API == "https://api.github.com/repos/ollama/ollama/releases/latest"
      and oi.DOWNLOAD_PREFIX == "https://github.com/ollama/ollama/releases/download/"
      and oi.API_HOSTS == {"api.github.com"} and oi.DOWNLOAD_HOSTS == {"github.com"}
      and not hasattr(oi, "VERSION") and not hasattr(oi, "ASSETS"))
pub = oi.published_asset("darwin")
check("latest release: the Mac asset picked by name, with its address, the size and GitHub's digest", pub == {
    "name": "Ollama-darwin.zip", "tag": TAG, "url": f"{oi.DOWNLOAD_PREFIX}{TAG}/Ollama-darwin.zip",
    "size": len(ZIP_BYTES), "sha256": ZIP_SHA}, pub)
win = oi.published_asset("win32")
check("latest release: the Windows asset picked by name", win["name"] == "OllamaSetup.exe" and win["size"] == len(SETUP_EXE))
GH["digest"] = None
err = find_error()
check("no digest from GitHub: refused before any download", err and err.step == "find" and err.key == "no_digest"
      and str(err) == oi.FAILED["no_digest"], err)
GH["digest"] = "md5:abc"
err = find_error()
check("a digest that isn't a SHA-256: refused", err and err.key == "no_digest", err)
GH["digest"] = "sha256:" + ZIP_SHA
GH["prerelease"] = True
err = find_error()
check("a pre-release is never used", err and err.step == "find", err)
GH["prerelease"] = False
GH["extra"] = []
real_release = release


def moved():
    r = real_release()
    r["assets"][1]["browser_download_url"] = "https://github.com/someone-else/ollama/releases/download/v1/Ollama-darwin.zip"
    return r


release = moved
err = find_error()
check("an asset address outside Ollama's own releases: refused", err and err.step == "find", err)
release = real_release


# ======================= the Setup class with fakes =======================
class Rig:
    """One Setup with recorders: what was installed, started and pulled."""

    def __init__(self, *, installed=None, answers_after_start=True, pull_ok=True, platform="darwin", run=None,
                 wait=0.3, env=None, on_start=None):
        self.home = tempfile.mkdtemp(dir=TMP)
        self.work = os.path.join(self.home, "work")
        self.up = False
        self.started, self.existing, self.pulled, self.runs = [], [], [], []
        self.answers_after_start, self.pull_ok = answers_after_start, pull_ok
        self.setup = oi.Setup(
            find=lambda: installed, answering=lambda: self.up, start_existing=self._existing, pull=self._pull,
            work_dir=self.work, platform=platform, home=self.home, env=env, run=run or self._run,
            popen=self._popen, sleep=lambda s: time.sleep(0.01), wait_seconds=wait, on_start=on_start)

    def _existing(self):
        self.existing.append(1)
        self.up = self.answers_after_start

    def _popen(self, args, **kw):
        self.started.append(args)
        self.up = self.answers_after_start

    def _pull(self, model):
        self.pulled.append(model)
        return self.pull_ok

    def _run(self, args, **kw):
        self.runs.append(args)
        return subprocess.run(args, **kw)

    def go(self, model="gemma4:12b-it-qat"):
        assert self.setup.start(model)
        wait_for(lambda: self.setup.view()["state"] != "running")
        return self.setup.view()


# ---- SHA-256: passing ----
rig = Rig()
v = rig.go()
app = os.path.join(rig.home, "Applications", "Ollama.app")
check("SHA-256 passes: Ollama.app installed in ~/Applications, started, model handed over",
      v["state"] == "done" and os.path.isfile(os.path.join(app, "Contents", "Resources", "ollama"))
      and rig.pulled == ["gemma4:12b-it-qat"], v)
check("ditto unzipped it, keeping the app's links",
      rig.runs and rig.runs[0][:4] == ["/usr/bin/ditto", "-x", "-k", os.path.join(rig.work, "Ollama-darwin.zip")]
      and os.path.islink(os.path.join(app, "Contents", "link-to-ollama")), rig.runs)
check("Ollama is opened as an app in the background (open -g -j), so its updater runs",
      rig.started == [["/usr/bin/open", "-g", "-j", app]], rig.started)
check("the finished zip and the temporary unzip folder are gone",
      not os.path.exists(os.path.join(rig.work, "Ollama-darwin.zip"))
      and [n for n in os.listdir(os.path.join(rig.home, "Applications"))] == ["Ollama.app"])
cloud = os.path.join(rig.home, ".ollama", "server.json")
check("Ollama's cloud features are turned off before its first start",
      os.path.isfile(cloud) and json.load(open(cloud)) == {"disable_ollama_cloud": True})
check("only api.github.com and github.com were contacted", set(CONNECTED) == {"api.github.com", "github.com"},
      set(CONNECTED))

# ---- the app's start throttle is set before the page can see step "start" ----
# A status poll that sees step "start" may start Ollama itself unless the throttle (on_start) is already set. The
# setup's file work after the step changes is slowed down here so a poll has time to land in that gap.
STARTS = []
real_cloud_off = oi.turn_cloud_off
oi.turn_cloud_off = lambda path: (time.sleep(0.3), real_cloud_off(path))[1]
try:
    race = Rig(on_start=lambda: STARTS.append(1))
    SEEN_AT_START = []  # how many on_start calls had happened when a poll first saw step "start"
    assert race.setup.start("gemma4:12b-it-qat")
    wait_for(lambda: race.setup.view()["step"] == "start" or race.setup.view()["state"] != "running")
    SEEN_AT_START.append(len(STARTS))
    wait_for(lambda: race.setup.view()["state"] != "running")
    race_view = race.setup.view()
finally:
    oi.turn_cloud_off = real_cloud_off
check("the start throttle is set before a poll can see step \"start\", even when the setup's file work is slow",
      race_view["state"] == "done" and SEEN_AT_START == [1] and STARTS == [1], (SEEN_AT_START, STARTS, race_view))

# ---- SHA-256: failing (the file is deleted and the setup stops) ----
GH["serve"] = b"X" * len(ZIP_BYTES)  # the right size, the wrong bytes
rig = Rig()
v = rig.go()
check("SHA-256 fails: the setup stops at check with a clear message",
      v["state"] == "error" and v["failed"] == "check" and v["message"].startswith(oi.FAILED["check"]), v)
check("SHA-256 fails: the file is deleted", not os.path.exists(rig.work) or os.listdir(rig.work) == [],
      os.listdir(rig.work) if os.path.exists(rig.work) else None)
check("SHA-256 fails: nothing is installed, started or pulled",
      not rig.runs and not rig.started and not rig.pulled
      and not os.path.exists(os.path.join(rig.home, "Applications", "Ollama.app")))
GH["serve"] = ZIP_BYTES

# ---- already installed: straight to the model ----
CONNECTED.clear()
rig = Rig(installed="/opt/homebrew/bin/ollama")
v = rig.go()
check("already installed: no GitHub, no download, no install", not CONNECTED and not rig.runs and not rig.started,
      (CONNECTED, rig.runs))
check("already installed: started the usual way when it wasn't answering, then the model is pulled",
      v["state"] == "done" and v["skipped_install"] and rig.existing == [1] and rig.pulled == ["gemma4:12b-it-qat"], v)
rig = Rig(installed="/opt/homebrew/bin/ollama")
rig.up = True
v = rig.go()
check("already installed and running: nothing is started, the model is pulled",
      v["state"] == "done" and not rig.existing and rig.pulled == ["gemma4:12b-it-qat"], v)
check("cloud setting untouched when Ollama was already there",
      not os.path.exists(os.path.join(rig.home, ".ollama")))

# ---- the fallback message on each failing step ----
GH["api_status"] = 503
rig = Rig()
v = rig.go()
check("find fails (GitHub doesn't answer): says so", v["state"] == "error" and v["failed"] == "find"
      and v["message"].startswith(oi.FAILED["find"]), v)
GH["api_status"] = 200
GH["file_status"] = 500
rig = Rig()
v = rig.go()
check("download fails: says so", v["state"] == "error" and v["failed"] == "download"
      and v["message"].startswith(oi.FAILED["download"]), v)
GH["file_status"] = 200
rig = Rig(run=lambda args, **kw: subprocess.CompletedProcess(args, 1))
v = rig.go()
check("install fails (ditto refuses): says so, and nothing is left in ~/Applications",
      v["state"] == "error" and v["failed"] == "install" and v["message"].startswith(oi.FAILED["install"])
      and os.listdir(os.path.join(rig.home, "Applications")) == [], v)
rig = Rig()
os.makedirs(os.path.join(rig.home, "Applications", "Ollama.app"))
v = rig.go()
check("install never replaces an Ollama.app already in ~/Applications", v["failed"] == "install"
      and os.listdir(os.path.join(rig.home, "Applications", "Ollama.app")) == [], v)
rig = Rig(answers_after_start=False)
v = rig.go()
check("start fails (the API never answers): says so", v["state"] == "error" and v["failed"] == "start"
      and v["message"] == oi.FAILED["start"] and not rig.pulled, v)
rig = Rig(pull_ok=False)
v = rig.go()
check("model fails (the pull couldn't start): says so", v["state"] == "error" and v["failed"] == "model"
      and v["message"] == oi.FAILED["model"], v)
rig = Rig(platform="linux")
v = rig.go()
check("no automatic setup for this computer: says so", v["state"] == "error"
      and v["message"] == oi.FAILED["platform"], v)
check("every message is a plain sentence that ends with a full stop",
      all(m.endswith(".") and "(" not in m for m in oi.FAILED.values()))

# ---- cancel during the download keeps the part for Try again ----
GH["serve"] = ZIP_BYTES
slow_rig = Rig()
real_fetch = downloader.fetch


def slow_fetch(url, dest, hosts, **kw):
    for _ in range(200):
        time.sleep(0.01)
        if kw["stop"]():
            raise downloader.Stopped()
    return real_fetch(url, dest, hosts, **kw)


slow_rig.setup.fetch = slow_fetch
slow_rig.setup.start("gemma4:12b-it-qat")
wait_for(lambda: slow_rig.setup.view()["step"] == "download")
check("Cancel stops the setup", slow_rig.setup.cancel() and slow_rig.setup.view()["state"] == "cancelled")
slow_rig.setup.thread.join(5)
check("after Cancel nothing is installed or pulled", not slow_rig.runs and not slow_rig.pulled
      and slow_rig.setup.view()["state"] == "cancelled")

# ---- Windows: built behind the same steps, not verified ----
win_env = {"LOCALAPPDATA": os.path.join(TMP, "localappdata")}
win_runs = []


def fake_installer(args, **kw):
    win_runs.append(args)
    os.makedirs(oi.windows_dir(win_env), exist_ok=True)
    open(os.path.join(oi.windows_dir(win_env), "ollama.exe"), "w").close()
    return subprocess.CompletedProcess(args, 0)


rig = Rig(platform="win32", env=win_env, run=fake_installer)
v = rig.go()
check("Windows (not verified): OllamaSetup.exe runs silently for this user, then the model is pulled",
      v["state"] == "done" and win_runs and win_runs[0][1:] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
      and win_runs[0][0].endswith("OllamaSetup.exe") and rig.pulled == ["gemma4:12b-it-qat"], v)
check("Windows is marked not verified", oi.WINDOWS_NOT_VERIFIED is True
      and oi.offer([], 16, "win32")["not_verified"] is True and oi.offer([], 16, "darwin")["not_verified"] is False)
check("Windows: the per-user folder is on the search list",
      oi.candidates("win32", env=win_env) == (os.path.join(TMP, "localappdata", "Programs", "Ollama", "ollama.exe"),))

# ======================= the upgrade message =======================
brew_real = os.path.join(TMP, "opt", "Cellar", "ollama", "0.40.16", "bin", "ollama")
os.makedirs(os.path.dirname(brew_real))
open(brew_real, "w").close()
brew_link = os.path.join(TMP, "homebrew-bin-ollama")
os.symlink(brew_real, brew_link)
app_link = os.path.join(TMP, "usr-local-bin-ollama")
os.symlink(APP_BIN, app_link)  # the link Ollama's own app makes in /usr/local/bin
check("Homebrew's Ollama (followed through its link): brew upgrade ollama",
      oi.how_installed(brew_link) == "homebrew" and "brew upgrade ollama" in oi.upgrade_hint(brew_link)
      and "brew upgrade" in oi.upgrade_hint("/opt/homebrew/bin/ollama"))
check("Ollama's app (even through /usr/local/bin): Restart to update, never brew",
      oi.how_installed(app_link) == "app" and "Restart to update" in oi.upgrade_hint(app_link)
      and "brew" not in oi.upgrade_hint(app_link))
check("another install: ollama.com/download, never brew",
      "ollama.com/download" in oi.upgrade_hint("/somewhere/ollama") and "brew" not in oi.upgrade_hint("/somewhere/ollama"))


class Body:
    def __init__(self, data):
        self.data = data

    def read(self, *a):
        return self.data

    def close(self):
        pass


def needs_newer():
    return urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 412, "Precondition Failed", {},
                                  Body(b'{"error": "this model requires a newer version of Ollama"}'))


saved_bin = server.OLLAMA_BIN
server.OLLAMA_BIN = app_link
check("the app's error for an old Ollama installed as an app has no brew", "brew" not in server.friendly_error(needs_newer()),
      server.friendly_error(needs_newer()))
server.OLLAMA_BIN = brew_link
check("the app's error for Homebrew's old Ollama says brew upgrade ollama",
      "brew upgrade ollama" in server.friendly_error(needs_newer()))
server.OLLAMA_BIN = saved_bin

# ======================= how the app starts an installed Ollama =======================
class PopenShim:
    """server.subprocess with Popen recorded: start_ollama never runs a real program (ditto, in ollama_install's own
    subprocess, still runs)."""

    def __init__(self):
        self.calls = []

    def Popen(self, args, **kw):  # noqa: N802 - stands in for subprocess.Popen
        self.calls.append(list(args))

    def __getattr__(self, name):
        return getattr(subprocess, name)


SHIM = PopenShim()
server.subprocess = SHIM
os.chmod(brew_real, 0o755)
server.OLLAMA_BIN = app_link  # Ollama's app, reached through the link it makes in /usr/local/bin
server.ollama_started_at[0] = 0
server.start_ollama()
check("start_ollama opens Ollama's app as an app (open -g -j), so its updater runs",
      SHIM.calls == [["/usr/bin/open", "-g", "-j", os.path.realpath(os.path.join(SRC, "Ollama.app"))]], SHIM.calls)
SHIM.calls.clear()
server.OLLAMA_BIN = brew_link
server.ollama_started_at[0] = 0
server.start_ollama()
check("start_ollama runs `ollama serve` for Homebrew's Ollama", SHIM.calls == [[brew_link, "serve"]], SHIM.calls)
SHIM.calls.clear()
server.start_ollama()
check("start_ollama starts nothing again within 30 seconds", SHIM.calls == [], SHIM.calls)
server.OLLAMA_BIN = saved_bin

# ======================= the ~/Applications search path =======================
user_app = os.path.join(os.path.expanduser("~"), "Applications", "Ollama.app", "Contents", "Resources", "ollama")
check("~/Applications/Ollama.app is on the app's search list, after /Applications",
      server.OLLAMA_CANDIDATES[3:] == (user_app,)
      and server.OLLAMA_CANDIDATES[2] == "/Applications/Ollama.app/Contents/Resources/ollama", server.OLLAMA_CANDIDATES)
fake_home = os.path.join(TMP, "home")
found_bin = oi.candidates("darwin", home=fake_home)[0]
check("not there: not found", server.find_ollama(candidates=("/nonexistent/ollama", found_bin), search_path="")
      == "/nonexistent/ollama")
os.makedirs(os.path.dirname(found_bin))
with open(found_bin, "w") as f:
    f.write("#!/bin/sh\n")
os.chmod(found_bin, 0o755)
check("an Ollama.app in ~/Applications is found", server.find_ollama(
    candidates=("/nonexistent/ollama", found_bin), search_path="") == found_bin)

# ======================= the Mac flow end to end, through the app's API =======================
FAKE = {"up": False, "pulls": [], "models": [], "deleted": []}


SIZES = {"gemma4:e4b-it-qat": 6_100_000_000, "gemma4:12b-it-qat": 7_200_000_000, "gemma4:26b-a4b-it-qat": 16_000_000_000}


class FakeOllama(BaseHTTPRequestHandler):
    def do_DELETE(self):  # Ollama's delete API: DELETE /api/delete {"model"}
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FAKE["deleted"].append((self.path, body))
        if self.path == "/api/delete" and body.get("model") in FAKE["models"]:
            FAKE["models"].remove(body["model"])
            return self._json({})
        data = b'{"error": "model not found"}'
        self.send_response(404)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


    def log_message(self, *args):
        pass

    def _json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._json({"models": [{"name": m, "digest": "", "size": SIZES.get(m, 1)} for m in FAKE["models"]]})
        return self._json({"models": []})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/pull":
            FAKE["pulls"].append(body)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"pulling abc","digest":"sha256:abc","total":10,"completed":10}\n'
                             b'{"status":"success"}\n')
            FAKE["models"].append(body["model"])
            return
        self._json({})


fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
fake.daemon_threads = True
threading.Thread(target=fake.serve_forever, daemon=True).start()
core.HOST = f"http://127.0.0.1:{fake.server_address[1]}"
real_request = core.ollama_request


def ollama_while_up(path, body=None, timeout=900, method=None):  # refused, like a stopped Ollama, until it is opened
    if not FAKE["up"]:
        raise ConnectionRefusedError("refused: Ollama isn't running yet")
    return real_request(path, body, timeout, method=method)


core.ollama_request = server.ollama_request = ollama_while_up
server.UPDATES.connect = lambda host, port: (_ for _ in ()).throw(ConnectionRefusedError("no registry here"))
server.memory_bytes = lambda: 16 * 2 ** 30  # a 16 GB Mac: the Medium row is the one set up
E2E_HOME = os.path.join(TMP, "e2e-home")
E2E_BIN = oi.candidates("darwin", home=E2E_HOME)[0]
real_find = server.find_ollama
server.find_ollama = lambda: real_find(candidates=(E2E_BIN,), search_path="")  # never this Mac's own Ollama
server.OLLAMA_BIN = E2E_BIN
OPENED = []


def fake_open(args, **kw):
    OPENED.append(args)
    threading.Timer(1.5, lambda: FAKE.update(up=True)).start()  # Ollama takes a moment to answer


server.SETUP.apps_dir = os.path.join(E2E_HOME, "Applications")
server.SETUP.cloud_config = os.path.join(E2E_HOME, ".ollama", "server.json")
server.SETUP.popen = fake_open
server.SETUP.sleep = lambda s: time.sleep(0.02)
SHIM.calls.clear()  # the real start_ollama runs from here on, with Popen recorded (server.subprocess is SHIM)
server.ollama_started_at[0] = 0

httpd = server.make_server(0)
server.httpd = httpd
server.PORT = httpd.server_address[1]
server.private_dir(server.APP_DIR)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def api(method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=30)
    headers = {"X-Token": server.TOKEN}
    raw = None
    if body is not None:
        raw = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=raw, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"null")
    conn.close()
    return resp.status, data


CONNECTED.clear()
GH["seen"].clear()
code, before = api("GET", "/api/models")
check("end to end: Ollama starts out not found, with the offer's sizes and model",
      code == 200 and before["ollama"] == "missing" and before["setup"]["state"] == "idle"
      and before["setup_offer"]["available"] is True and before["setup_offer"]["model"] == "gemma4:12b-it-qat"
      and before["setup_offer"]["model_gb"] == 7.2, before.get("setup_offer"))
wait_for(lambda: server.SETUP.known_release() is not None)
code, shown = api("GET", "/api/models")
check("end to end: the offer then shows the size GitHub's API reports for the latest release",
      shown["setup_offer"]["engine_bytes"] == len(ZIP_BYTES), shown["setup_offer"])
check("end to end: before the button only the release facts were read (api.github.com), nothing downloaded",
      set(CONNECTED) == {"api.github.com"} and all(p == "/repos/ollama/ollama/releases/latest" for _, p in GH["seen"]),
      (CONNECTED, GH["seen"]))
CONNECTED.clear()
GH["seen"].clear()
code, out = api("POST", "/api/ollama/setup", {})
check("end to end: Set up the AI starts", code == 200 and out["ok"], out)
code, again = api("POST", "/api/ollama/setup", {})
check("end to end: a second press while it runs is refused", code in (409, 200), again)
POLLS = []  # what the page saw while the setup ran: it asks every second, and each ask may start Ollama


def poll():
    view = api("GET", "/api/models")[1]
    api("GET", "/api/status")
    POLLS.append(view["setup"]["step"])
    return view["setup"]["state"] != "running"


wait_for(poll, 30)
code, after = api("GET", "/api/models")
check("end to end: the setup finished", after["setup"]["state"] == "done" and after["setup"]["failed"] is None,
      after["setup"])
check("end to end: Ollama.app is in ~/Applications and was opened in the background",
      os.path.isfile(E2E_BIN) and OPENED == [["/usr/bin/open", "-g", "-j", os.path.join(E2E_HOME, "Applications", "Ollama.app")]],
      OPENED)
check("end to end: status polls while Ollama was starting never started a second Ollama",
      "start" in POLLS and SHIM.calls == [] and len(OPENED) == 1, (POLLS, SHIM.calls, OPENED))
check("end to end: the app's own search now finds it", server.OLLAMA_BIN == E2E_BIN and server.ollama_installed())
wait_for(lambda: FAKE["models"] == ["gemma4:12b-it-qat"])
check("end to end: handed over to Ollama's /api/pull with the recommended model",
      [p.get("model") for p in FAKE["pulls"]] == ["gemma4:12b-it-qat"] and FAKE["pulls"][0].get("stream") is True,
      FAKE["pulls"])
code, last = api("GET", "/api/models")
row = next(r for r in last["rows"] if r["model"] == "gemma4:12b-it-qat")
check("end to end: Ollama is running and the model is installed and chosen",
      last["ollama"] == "running" and row["state"] == "in_use", (last["ollama"], row["state"]))
check("end to end: only GitHub's API and download hosts were contacted, the API first",
      CONNECTED[:1] == ["api.github.com"] and set(CONNECTED) == {"api.github.com", "github.com"}, CONNECTED)
check("end to end: the cancel endpoint answers 409 when nothing runs", api("POST", "/api/ollama/setup/cancel", {})[0] == 409)

# ======================= Remove: confirm on the page, then Ollama's delete API =======================
FAKE["models"] += ["gemma4:e4b-it-qat", "gemma4:26b-a4b-it-qat"]  # three models; 12B (set up above) is in use
code, view = api("GET", "/api/models")
rows = {r["model"]: r for r in view["rows"]}
check("Remove: each installed row carries the space it frees and what answers next",
      rows["gemma4:12b-it-qat"]["state"] == "in_use" and rows["gemma4:12b-it-qat"]["bytes"] == 7_200_000_000
      and rows["gemma4:12b-it-qat"]["remove_then"] == "gemma4:26b-a4b-it-qat"
      and rows["gemma4:12b-it-qat"]["remove_last"] is False
      and rows["gemma4:e4b-it-qat"]["state"] == "installed" and rows["gemma4:e4b-it-qat"]["remove_then"] is None,
      {m: (r["state"], r.get("bytes"), r.get("remove_then"), r.get("remove_last")) for m, r in rows.items()})
code, out = api("POST", "/api/models/remove", {"model": "gemma4:e4b-it-qat"})
check("Remove a model not in use: deleted through DELETE /api/delete, the model in use stays",
      code == 200 and out["message"] == "Removed Gemma 4, 4B." and out["model"] == "gemma4:12b-it-qat"
      and FAKE["deleted"][-1] == ("/api/delete", {"model": "gemma4:e4b-it-qat"})
      and "gemma4:e4b-it-qat" not in FAKE["models"], (code, out.get("message"), FAKE["deleted"]))
check("Remove: the list is refreshed in the answer",
      next(r for r in out["models"]["rows"] if r["model"] == "gemma4:e4b-it-qat")["state"] == "not_installed")
code, out = api("POST", "/api/models/remove", {"model": "gemma4:12b-it-qat"})
check("Remove the model in use: answers switch to the largest other installed model, and it says so",
      code == 200 and out["message"] == "Removed Gemma 4, 12B. Answers now come from Gemma 4, 26B."
      and out["model"] == "gemma4:26b-a4b-it-qat", (code, out))
code, view = api("GET", "/api/models")
last_row = next(r for r in view["rows"] if r["model"] == "gemma4:26b-a4b-it-qat")
check("Remove: the last model is marked as the last one before it goes", last_row["remove_last"] is True
      and last_row["remove_then"] is None, last_row)
code, out = api("POST", "/api/models/remove", {"model": "gemma4:26b-a4b-it-qat"})
check("Remove the last model: says no model is left and how questions are answered now",
      code == 200 and out["message"] == ("Removed Gemma 4, 26B. No model is left, so questions show the library's "
                                         "documents with no written answer until you download one.")
      and FAKE["models"] == [], (code, out.get("message"), FAKE["models"]))
server.ollama_started_at[0] = 0  # the setup opened Ollama seconds ago; its "starting" minute is long over in real use
code, status = api("GET", "/api/status")
check("Remove the last model: the app is in library-only mode, never silently broken",
      status["library_only"] == "not_installed" and status["model_installed"] is False,
      (status.get("library_only"), status.get("model_installed")))
code, out = api("POST", "/api/models/remove", {"model": "gemma4:12b-it-qat"})
check("Remove a model that isn't installed: refused", code == 400, (code, out))
code, out = api("POST", "/api/models/remove", {"model": "not-in-the-table:1b"})
check("Remove a model outside the table: refused", code == 400, (code, out))

httpd.shutdown()
print(f"\n{len(FAILS)} failed" if FAILS else "\nall Ollama setup checks passed")
sys.exit(1 if FAILS else 0)
