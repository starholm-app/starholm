"""The AI engine on Windows (app/ollama_install.py and its wiring in app/server.py), after a tester's PC showed that
opening Ollama's desktop app doesn't work: the engine-only zip picked by the computer's own processor, unpacked into
Starholm's data folder (never outside it), started as `ollama.exe serve` with no window, and never through
`ollama app.exe`; an Ollama the person installed that stays silent replaced by Starholm's own engine; every step and
failure written to the log; and, on every platform, an engine that doesn't start counted as stopped after a while (so
a question shows the library's documents instead of "still starting" forever) and retried with longer and longer
waits instead of every 30 seconds.

Nothing is downloaded and no Ollama runs: the release facts, the zip and every started program are stand-ins. The
checks run on a Mac and on Windows alike (the platform is passed in).

Usage: .venv/bin/python tests/backend_windows_engine_test.py"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"), SURVIVAL_LLM_LOGS=os.path.join(TMP, "logs"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()
os.environ.pop("OLLAMA_HOST", None)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import emergency  # noqa: E402

emergency.LOCALTIME = os.path.join(TMP, "no-localtime")
emergency.GLOBAL_PREFS = os.path.join(TMP, "no-prefs.plist")
if sandbox.WINDOWS:
    emergency.detect = lambda: (None, None)
import ollama_install as oi  # noqa: E402

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


# ======================= which download =======================
check("Windows on Arm gets the arm64 engine zip, whatever the program itself was built for",
      oi.native_arch({}, machine=lambda: 0xAA64) == "arm64" and oi.asset_name("win32", "arm64") == "ollama-windows-arm64.zip")
check("an x64 PC gets the amd64 engine zip",
      oi.native_arch({}, machine=lambda: 0x8664) == "amd64" and oi.asset_name("win32", "amd64") == "ollama-windows-amd64.zip")
check("without Windows' own answer the environment decides (a last resort: an emulated x64 program sees AMD64 there)",
      oi.native_arch({"PROCESSOR_ARCHITECTURE": "AMD64", "PROCESSOR_ARCHITEW6432": "ARM64"}, machine=None) == "arm64"
      and oi.native_arch({"PROCESSOR_ARCHITECTURE": "ARM64"}, machine=lambda: None) == "arm64"
      and oi.native_arch({"PROCESSOR_ARCHITECTURE": "AMD64"}, machine=None) == "amd64"
      and oi.native_arch({}, machine=None) == "amd64")
check("Ollama's installer and desktop app are never what Windows downloads",
      "OllamaSetup.exe" not in set(oi.WINDOWS_ASSETS.values()) | set(oi.ASSET_NAMES.values())
      and not hasattr(oi, "install_windows") and not hasattr(oi, "WINDOWS_FLAGS"))
check("a Mac still gets Ollama-darwin.zip, and other systems nothing",
      oi.asset_name("darwin") == "Ollama-darwin.zip" and oi.asset_name("linux") is None)

TAG = "v0.99.0"


def engine_zip(extra=()):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ollama.exe", b"MZ engine")
        z.writestr("lib/ollama/ggml-base.dll", b"dll" * 1000)
        for name, body in extra:
            z.writestr(name, body)
    return data.getvalue()


ZIP_BYTES = engine_zip()
ZIP_SHA = hashlib.sha256(ZIP_BYTES).hexdigest()


def release_bytes(url, hosts, max_bytes=None):
    assets = [{"name": name, "size": len(ZIP_BYTES), "digest": "sha256:" + ZIP_SHA,
               "browser_download_url": f"{oi.DOWNLOAD_PREFIX}{TAG}/{name}"}
              for name in ("ollama-windows-arm64.zip", "ollama-windows-amd64.zip", "OllamaSetup.exe")]
    return json.dumps({"tag_name": TAG, "draft": False, "prerelease": False, "assets": assets}).encode()


asset = oi.published_asset("win32", release_bytes, "arm64")
check("the release's facts for the arm64 zip: name, address on github.com, size and SHA-256",
      asset == {"name": "ollama-windows-arm64.zip", "tag": TAG, "size": len(ZIP_BYTES), "sha256": ZIP_SHA,
                "url": f"{oi.DOWNLOAD_PREFIX}{TAG}/ollama-windows-arm64.zip"}, asset)

# ======================= unpacking =======================
data = os.path.join(TMP, "data-unpack")
os.makedirs(data)
archive = os.path.join(TMP, "engine.zip")
with open(archive, "wb") as f:
    f.write(ZIP_BYTES)
seen = []
binary = oi.unpack_engine(archive, data, facts={"tag": TAG, "name": "x.zip", "sha256": ZIP_SHA},
                          progress=lambda done, total: seen.append((done, total)))
check("the zip is unpacked into <data>/engine with ollama.exe at its top",
      binary == os.path.join(data, "engine", "ollama.exe") and os.path.isfile(binary)
      and os.path.isfile(os.path.join(data, "engine", "lib", "ollama", "ggml-base.dll")), os.listdir(data))
check("nothing else is left in the data folder (no .incoming, no .old)", os.listdir(data) == ["engine"], os.listdir(data))
check("progress is reported in bytes up to the total", seen and seen[-1][0] == seen[-1][1] > 0, seen[-3:])
check("which release it is, is written beside it", oi.engine_facts(data) == {"tag": TAG, "name": "x.zip", "sha256": ZIP_SHA})
with open(binary, "wb") as f:
    f.write(b"old engine")
oi.unpack_engine(archive, data)
check("unpacking again replaces the engine whole", open(binary, "rb").read() == b"MZ engine" and os.listdir(data) == ["engine"])

for bad in ("../outside.txt", "lib/../../outside.txt", "/abs.txt", "C:/abs.txt", "..\\outside.txt"):
    evil = os.path.join(TMP, "evil.zip")
    with open(evil, "wb") as f:
        f.write(engine_zip([(bad, b"x")]))
    try:
        oi.unpack_engine(evil, data)
        err = None
    except oi.SetupError as exc:
        err = exc
    check(f"a zip with the name {bad!r} is refused, and the engine stays as it was",
          err is not None and err.step == "install" and open(binary, "rb").read() == b"MZ engine"
          and os.listdir(data) == ["engine"] and not os.path.exists(os.path.join(TMP, "outside.txt")), err)
no_exe = os.path.join(TMP, "no-exe.zip")
with zipfile.ZipFile(no_exe, "w") as z:
    z.writestr("readme.txt", "nothing here")
try:
    oi.unpack_engine(no_exe, data)
    err = None
except oi.SetupError as exc:
    err = exc
check("a zip with no ollama.exe is refused", err is not None and str(err).startswith(oi.FAILED["unpack"]), err)
with open(os.path.join(TMP, "broken.zip"), "wb") as f:
    f.write(b"not a zip")
try:
    oi.unpack_engine(os.path.join(TMP, "broken.zip"), data)
    err = None
except oi.SetupError as exc:
    err = exc
check("a file that isn't a zip is refused with the unpack message", err is not None and err.key == "unpack", err)
try:
    oi.unpack_engine(archive, data, stop=lambda: True)
    cancelled = False
except oi.Cancelled:
    cancelled = True
check("Cancel during unpacking stops it and keeps the engine", cancelled and os.listdir(data) == ["engine"])

# ======================= starting it =======================
STARTED = []


class FakeProc:
    def __init__(self, args, kw):
        self.args, self.kw, self.pid, self.returncode = args, kw, 4242, None
        self.ended = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.ended, self.returncode = True, 1

    def wait(self, timeout=None):
        return self.returncode


def fake_popen(args, **kw):
    out = kw.get("stdout")
    if hasattr(out, "write"):
        out.write(b"time=now level=INFO msg=\"Listening on 127.0.0.1:11434\"\n")
    proc = FakeProc(args, kw)
    STARTED.append(proc)
    return proc


os.environ["HTTPS_PROXY"] = "http://proxy.example:8080"
os.environ["no_proxy"] = "localhost"
log = os.path.join(TMP, "logs", "ollama.log")
exe = os.path.join(data, "engine", "ollama.exe")
proc = oi.start_program(exe, "win32", popen=fake_popen, log_path=log)
kw = proc.kw
check("Windows runs `ollama.exe serve`", proc.args == [exe, "serve"], proc.args)
check("with no console window, in its own process group",
      kw.get("creationflags") == 0x08000000 | 0x00000200 and "start_new_session" not in kw, kw.get("creationflags"))
check("listening on 127.0.0.1:11434 only, with no proxy settings",
      kw["env"].get("OLLAMA_HOST") == "127.0.0.1:11434" and not [k for k in kw["env"] if k.lower() in oi.PROXY_VARS])
check("what it prints goes to logs/ollama.log, under a line saying when it was started",
      "starting" in open(log, encoding="utf-8").read() and "Listening on" in open(log, encoding="utf-8").read()
      and kw["stderr"] == subprocess.STDOUT)
os.environ.pop("HTTPS_PROXY")
os.environ.pop("no_proxy")
desktop = os.path.join(TMP, "localappdata", "Programs", "Ollama")
os.makedirs(desktop)
for name in ("ollama.exe", "ollama app.exe"):
    open(os.path.join(desktop, name), "w").close()
STARTED.clear()
oi.start_program(os.path.join(desktop, "ollama.exe"), "win32", popen=fake_popen, log_path=log)
check("an Ollama the person installed is also run as `ollama.exe serve`: its desktop app is never opened",
      [p.args for p in STARTED] == [[os.path.join(desktop, "ollama.exe"), "serve"]]
      and oi.start_app(os.path.join(desktop, "ollama.exe"), "win32", popen=fake_popen) is False, [p.args for p in STARTED])
check("no code path names the desktop app", "ollama app.exe" not in open(oi.__file__, encoding="utf-8").read().split('"""', 2)[2])
with open(log, "wb") as f:
    f.write(b"x" * (oi.ENGINE_LOG_MAX + 1))
oi.serve(exe, "win32", popen=fake_popen, log_path=log)
check("a log over its size limit starts again", os.path.getsize(log) < 1000)
check("the last lines of the log can be read for the app's own log",
      "Listening on" in oi.log_tail(log) and oi.log_tail(os.path.join(TMP, "none.log")) == "")
check("a program that can't be started gives None, not an error",
      oi.serve(exe, "win32", popen=lambda *a, **k: (_ for _ in ()).throw(OSError("no")), log_path=log) is None)
win_env = {"LOCALAPPDATA": os.path.join(TMP, "localappdata")}
check("the search list: Starholm's own engine first, then an Ollama the person installed",
      oi.candidates("win32", env=win_env, data_dir=data) == (exe, os.path.join(desktop, "ollama.exe")))


# ======================= the whole setup on Windows =======================
class Rig:
    """One setup on "Windows": find() gives `installed`, the API answers once `answers(binary)` says a started
    program makes it answer."""

    def __init__(self, installed=None, answers=lambda binary: True, up=False, pull_ok=True, wait=0.3, own_wait=0.2):
        self.home = tempfile.mkdtemp(dir=TMP)
        self.data = os.path.join(self.home, "data")
        os.makedirs(self.data)
        self.up, self.answers, self.pull_ok = up, answers, pull_ok
        self.existing, self.started, self.pulled, self.lines, self.stopped, self.installed_at = [], [], [], [], [], []
        self.elog = os.path.join(self.home, "logs", "ollama.log")
        self.setup = oi.Setup(
            find=lambda: installed, answering=lambda: self.up, start_existing=self._existing, pull=self._pull,
            work_dir=os.path.join(self.home, "work"), platform="win32", home=self.home, env=win_env,
            fetch=self._fetch, fetch_bytes=release_bytes, popen=self._popen, sleep=lambda s: time.sleep(0.01),
            wait_seconds=wait, own_wait_seconds=own_wait, data_dir=self.data, arch="arm64",
            stop_engine=lambda: self.stopped.append(1), on_installed=self.installed_at.append,
            log=self.lines.append, engine_log=self.elog)
        self.installed = installed

    def _existing(self):
        self.existing.append(self.installed)
        self.up = self.answers(self.installed)

    def _popen(self, args, **kw):
        proc = fake_popen(args, **kw)
        self.started.append(args)
        self.up = self.answers(args[0])
        return proc

    def _fetch(self, url, dest, hosts, size=None, sha256=None, progress=None, stop=None):
        self.fetched = (url, set(hosts), size, sha256)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(ZIP_BYTES)
        if progress:
            progress(len(ZIP_BYTES), len(ZIP_BYTES))

    def _pull(self, model):
        self.pulled.append(model)
        return self.pull_ok

    def go(self, model="gemma4:12b-it-qat"):
        assert self.setup.start(model)
        wait_for(lambda: self.setup.view()["state"] != "running")
        return self.setup.view()


rig = Rig()
v = rig.go()
own = os.path.join(rig.data, "engine", "ollama.exe")
check("a PC with no Ollama: the arm64 zip is downloaded from github.com with its SHA-256, unpacked, started, and "
      "the model handed over",
      v["state"] == "done" and rig.fetched == (f"{oi.DOWNLOAD_PREFIX}{TAG}/ollama-windows-arm64.zip", {"github.com"},
                                               len(ZIP_BYTES), ZIP_SHA)
      and os.path.isfile(own) and rig.started == [[own, "serve"]] and rig.pulled == ["gemma4:12b-it-qat"], (v, rig.started))
check("the app is told where the engine is, and the downloaded zip is gone",
      rig.installed_at == [own] and not os.path.exists(os.path.join(rig.home, "work", "ollama-windows-arm64.zip")))
check("nothing was installed outside Starholm's data folder", not os.path.exists(os.path.join(rig.home, "AppData"))
      and sorted(os.listdir(rig.home)) == [".ollama", "data", "logs", "work"], sorted(os.listdir(rig.home)))
cloud = os.path.join(rig.home, ".ollama", "server.json")
check("Ollama's cloud features are turned off before its first start", json.load(open(cloud)) == {"disable_ollama_cloud": True})
text = "\n".join(rig.lines)
check("every step is in the log: the start, the download with its release, the SHA-256 check, the install, the answer",
      all(part in text for part in ("Set up the AI: started", f"Downloading ollama-windows-arm64.zip {TAG}",
                                    "matches its published SHA-256", f"Installed {own}", "The AI engine answers",
                                    "Set up the AI: done")), text)

rig = Rig(answers=lambda binary: False)
v = rig.go()
text = "\n".join(rig.lines)
check("the engine never answers: the setup stops at 'start' with a plain message that names no program",
      v["state"] == "error" and v["failed"] == "start" and v["message"] == oi.FAILED["start"]
      and "ollama" not in v["message"].lower() and not rig.pulled, v)
check("and the log says so, with the engine's own last lines",
      "didn't answer within" in text and "Listening on 127.0.0.1:11434" in text and "stopped at step 'start'" in text, text)

silent = os.path.join(desktop, "ollama.exe")
rig = Rig(installed=silent, answers=lambda binary: binary != silent)
v = rig.go()
own = os.path.join(rig.data, "engine", "ollama.exe")
check("an Ollama the person installed that stays silent (as on the tester's PC): it is tried once, then Starholm "
      "installs and starts its own engine",
      v["state"] == "done" and rig.existing == [silent] and rig.started == [[own, "serve"]]
      and rig.installed_at == [own] and rig.pulled == ["gemma4:12b-it-qat"], (v, rig.existing, rig.started))
check("the log says why", "didn't answer within" in "\n".join(rig.lines) and "its own engine" in "\n".join(rig.lines))
rig = Rig(installed=silent, answers=lambda binary: True)
v = rig.go()
check("an Ollama the person installed that answers once started is used as it is: nothing is downloaded",
      v["state"] == "done" and v["skipped_install"] is True and rig.existing == [silent] and not rig.started
      and not hasattr(rig, "fetched") and not os.path.exists(os.path.join(rig.data, "engine")), v)
rig = Rig(up=True)
v = rig.go()
check("an engine that already answers: nothing is installed or started", v["state"] == "done" and v["skipped_install"]
      and not rig.started and not rig.existing and not hasattr(rig, "fetched"), v)
rig = Rig()
own = os.path.join(rig.data, "engine", "ollama.exe")
os.makedirs(os.path.dirname(own))
open(own, "w").close()
rig2 = Rig(installed=own)
rig2.installed = own
v = rig2.go()
check("Starholm's own engine already unpacked: it is started, not downloaded again",
      v["state"] == "done" and v["skipped_install"] and rig2.existing == [own] and not hasattr(rig2, "fetched"), v)
check("no failure message names Ollama or sends the person to a website",
      not [m for m in oi.FAILED.values() if "ollama" in m.lower() or "http" in m.lower()])

# ======================= this computer's memory (the recommended model depends on it) =======================
import model_setup  # noqa: E402

memory = model_setup.machine_memory_bytes()
check("this computer's memory is read (on Windows too, where os.sysconf doesn't exist), so a model is recommended",
      isinstance(memory, int) and memory >= 2 ** 30 and model_setup.memory_gb(memory) >= 1, memory)
if sandbox.WINDOWS:
    check("Windows: the firmware's figure or what Windows can use, in bytes",
          model_setup.windows_memory_bytes() == memory and memory % 1024 == 0, memory)

# ======================= the app: starting, giving up on "starting", trying again =======================
import core  # noqa: E402
import server  # noqa: E402

LOG = []
server.log_line = LOG.append
server.installed_models = lambda: None  # Ollama's API never answers
fake_bin = os.path.join(TMP, "fake-engine", "ollama.exe")
os.makedirs(os.path.dirname(fake_bin))
with open(fake_bin, "w") as f:
    f.write("x")
os.chmod(fake_bin, 0o755)
server.OLLAMA_BIN = fake_bin
server.PLATFORM = "win32"
LAUNCHED = []


def fake_start(binary, platform=None, popen=None, log_path=None):
    proc = FakeProc([binary, "serve"], {})
    LAUNCHED.append((binary, platform, log_path, proc))
    return proc


server.ollama_install.start_program = fake_start


def reset():
    LAUNCHED.clear()
    LOG.clear()
    server.OLLAMA_PROC[0] = None
    server.ollama_started_at[0] = 0.0
    server.ollama_trying_since[0] = 0.0
    server.ollama_attempts[0] = 0


def age(seconds):
    """As if that many seconds had passed since the last start."""
    server.ollama_started_at[0] -= seconds
    server.ollama_trying_since[0] -= seconds


reset()
server.start_ollama()
check("the app starts the engine once, as `serve`, with its log in logs/ollama.log",
      len(LAUNCHED) == 1 and LAUNCHED[0][:3] == (fake_bin, "win32", os.path.join(TMP, "logs", "ollama.log")), LAUNCHED)
check("and writes the start to its own log", LOG == [f"Starting the AI engine ({fake_bin})."], LOG)
check("just started: the state is 'starting'", server.ollama_starting() and server.ollama_state(None) == "starting")
server.start_ollama()
server.start_ollama()
check("asked again within 30 seconds, it isn't started again", len(LAUNCHED) == 1)
age(31)
server.start_ollama()
check("after 30 seconds without an answer, a running engine is given more time, never started a second time",
      len(LAUNCHED) == 1 and "doesn't answer yet" in LOG[-1], LOG)
age(server.START_GRACE)
check("after the grace period the state is 'stopped', not 'starting' forever",
      not server.ollama_starting() and server.ollama_state(None) == "stopped")
check("so a question gets the library's documents: the reason is 'stopped'",
      server.no_model_reason("gemma4:12b-it-qat", None) == "stopped")

reset()
server.start_ollama()
LAUNCHED[0][3].returncode = 3  # the engine exited
with open(os.path.join(TMP, "logs", "ollama.log"), "w", encoding="utf-8") as f:
    f.write("Error: listen tcp 127.0.0.1:11434: bind: address already in use\n")
age(31)
server.start_ollama()
check("an engine that exited is started again after 30 seconds, and the log has its exit code and last lines",
      len(LAUNCHED) == 2 and "exited with code 3" in LOG[-1] and "address already in use" in LOG[-1], LOG)
LAUNCHED[1][3].returncode = 3
age(31)
server.start_ollama()
check("the next try waits longer (60 seconds)", len(LAUNCHED) == 2 and server.retry_after() == 60)
age(30)
server.start_ollama()
check("and happens then", len(LAUNCHED) == 3)
waits = []
for _ in range(8):
    waits.append(server.retry_after())
    server.ollama_attempts[0] += 1
check("the waits double up to ten minutes: the engine is not started every 30 seconds for ever",
      waits[:3] == [120, 240, 480] and waits[-1] == 600 and max(waits) == 600, waits)
age(server.START_GRACE)
check("a failing engine never counts as 'starting' again while the app keeps trying", not server.ollama_starting())

reset()
server.start_ollama()
age(31)
server.restart_ollama()
check("Start (a button press) starts it at once and the state is 'starting' again",
      len(LAUNCHED) == 2 and server.ollama_starting() and server.ollama_attempts[0] == 1, (len(LAUNCHED), LOG))
server.installed_models = lambda: ["gemma4:12b-it-qat"]
models = server.chat_models()
check("once the API answers, the count starts again and the log says so",
      models == ["gemma4:12b-it-qat"] and server.ollama_attempts[0] == 0 and not server.ollama_trying_since[0]
      and "The AI engine answers" in LOG[-1], LOG)

reset()
server.installed_models = lambda: None
server.start_ollama()
answers_at = time.time() + 1.5
server.installed_models = lambda: ["gemma4:12b-it-qat"] if time.time() >= answers_at else None
t0 = time.time()
models = server.wait_for_ollama(10)
check("a question asked while the engine starts waits for it for a few seconds",
      models == ["gemma4:12b-it-qat"] and 1 <= time.time() - t0 < 8, (models, time.time() - t0))
reset()
server.installed_models = lambda: None
t0 = time.time()
check("and doesn't wait at all when nothing is starting", server.wait_for_ollama(10) is None and time.time() - t0 < 1)

reset()
server.start_ollama()
proc = LAUNCHED[0][3]
server.stop_engine()
check("the engine this app started is ended when the Windows app quits", proc.ended and server.OLLAMA_PROC[0] is None)
server.stop_engine()  # nothing running: no error

reset()
server.SETUP.job["state"] = "running"
server.start_ollama()
check("while Set up the AI runs, the app doesn't start an engine of its own", not LAUNCHED)
server.SETUP.job["state"] = "idle"

print(f"{len(FAILS)} failed" if FAILS else "all Windows engine checks passed")
sys.exit(1 if FAILS else 0)
