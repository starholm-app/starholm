"""Set up the AI: the "Set up the AI" button in Settings > Model, for a computer with no Ollama.

One click runs these steps in a background thread, with progress and Cancel:

    find      read the latest stable release of Ollama from GitHub's API (RELEASE_API: GitHub's "latest" is the newest
              release that is neither a draft nor a pre-release, and the answer is checked for both): this platform's
              asset by name (ASSET_NAMES), its download address, size and the SHA-256 GitHub publishes for it
              ("digest"). With no digest the setup refuses to download it.
    download  the official asset (Ollama-darwin.zip on a Mac) from github.com, resumable (library/downloader.py).
    check     its SHA-256 against the digest GitHub publishes. On a mismatch the file is deleted and the setup stops.
    install   for this user only, with no admin password: on a Mac, Ollama.app is unzipped with ditto (which keeps the
              app's signature and links) into ~/Applications.
    start     the Ollama app is opened in the background (`open -g -j`), as the research recommends, so Ollama's own
              updater (which lives in the app) keeps it up to date. Before the first start, Ollama's cloud features are
              turned off in ~/.ollama/server.json when that file doesn't exist yet. Then the setup waits until Ollama's
              API answers.
    model     hands over to the existing model download (model_setup.Pulls through /api/pull).

When Ollama is already installed, the first four steps are skipped: it is started if needed, then the model downloads,
as before. When a step fails, the view says which step and why, and the page shows the manual route (ollama.com) and
Try again. Nothing here runs until the person presses the button.

Windows never runs Ollama's installer or its desktop app (which opens its own window, asks for an account and adds a
login item): the setup downloads the release's engine-only zip for the computer's own processor
(ollama-windows-arm64.zip or ollama-windows-amd64.zip), checks it the same way, unpacks it into Starholm's own data
folder (<data>\\engine) and runs `ollama.exe serve` there with no window (start_program). An Ollama the person already
has is used when it answers; when it is installed but doesn't start, the setup installs Starholm's own copy.

Every step, and why a step failed, goes to the app's log through Setup's log(), and what the engine prints goes to
logs\\ollama.log, so a failure can be diagnosed from the logs alone.

Hosts: api.github.com for the release facts, github.com for the download (which redirects to GitHub's file host; the
SHA-256 GitHub published for the asset vouches for the file wherever it comes from). Nothing else. The release
facts are also read, in the background, while Settings > Model shows the setup's offer, so the page can show the
size before anything downloads (look_up).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

from library import downloader

RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/latest"  # the newest stable release
API_HOSTS = frozenset({"api.github.com"})
DOWNLOAD_PREFIX = "https://github.com/ollama/ollama/releases/download/"  # + the release's tag + "/" + the asset
DOWNLOAD_HOSTS = frozenset({"github.com"})
MAX_RELEASE_ANSWER = 2_000_000  # bytes of the release facts
MAX_ASSET = 8 << 30  # a published size above this is refused
LOOKUP_EVERY = 3600  # seconds the release facts are kept for the offer
LOOKUP_RETRY = 60  # seconds before a failed look-up (offline) is tried again
TAG = re.compile(r"v?[0-9][0-9A-Za-z.+-]{0,40}")
DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
GUIDE_URL = "https://ollama.com/download"  # the manual route, shown when a step fails
WAIT_SECONDS = 120  # how long the start step waits for Ollama's API to answer
OWN_WAIT_SECONDS = 45  # Windows: how long an Ollama the person installed gets before Starholm installs its own copy
PROXY_VARS = frozenset({"http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy"})  # never given to Ollama
ENGINE_LOG_MAX = 2_000_000  # bytes of logs/ollama.log kept: above it the file starts again

# Each platform's asset in an Ollama release, by name. On Windows it is the engine-only zip for the computer's own
# processor (native_arch): no installer and no desktop app.
ASSET_NAMES = {"darwin": "Ollama-darwin.zip"}
WINDOWS_ASSETS = {"arm64": "ollama-windows-arm64.zip", "amd64": "ollama-windows-amd64.zip"}
ENGINE_FOLDER = "engine"  # in the app's data folder: the unpacked zip, with ollama.exe at its top
ENGINE_FACTS = "starholm-engine.json"  # in that folder: which release it is ({tag, name, sha256})
MAX_UNPACKED = 24 << 30  # bytes: a zip that would unpack to more is refused
MAX_MEMBERS = 50_000
IMAGE_FILE_MACHINE_ARM64 = 0xAA64

STEPS = ("find", "download", "check", "install", "start", "model")
# What failed, in plain words, for each step. The page adds the manual route and Try again.
FAILED = {
    "platform": "Automatic setup isn't available on this computer yet.",
    "find": "The AI engine's download couldn't be found. Check that this computer is online, then try again.",
    "no_digest": ("The AI engine's download couldn't be checked against its published fingerprint, so it wasn't "
                  "downloaded. Try again later."),
    "download": ("The AI engine's download stopped before it finished. Check that this computer is online, then try "
                 "again."),
    "disk": "There isn't enough free space on this computer for the AI engine. Free up some space, then try again.",
    "check": ("The downloaded file didn't match the published one, so it was deleted and nothing was installed."),
    "install": "The AI engine couldn't be installed in the Applications folder in your home folder.",
    "unpack": "The AI engine couldn't be unpacked on this computer.",
    "start": ("The AI engine is on this computer but didn't start. Questions still show the library's documents. "
              "Restart this computer, then try again."),
    "model": "The AI engine is running, but the model download couldn't start.",
}


class SetupError(Exception):
    """A step failed: step names it, and FAILED[key] says it in plain words."""

    def __init__(self, step, key=None, detail=""):
        super().__init__(detail or FAILED[key or step])
        self.step, self.key, self.detail = step, key or step, detail


class Cancelled(Exception):
    pass


def platform_key(platform=None):
    platform = platform or sys.platform
    return "win32" if platform.startswith("win") else platform


def _native_machine():
    """Windows' own answer for the computer's processor (IsWow64Process2's native machine), or None when it can't be
    asked. It is right for a program that runs emulated too, such as an x64 Python on Windows on Arm."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.IsWow64Process2.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort),
                                             ctypes.POINTER(ctypes.c_ushort)]
        kernel32.IsWow64Process2.restype = ctypes.c_int
        process, native = ctypes.c_ushort(0), ctypes.c_ushort(0)
        if kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
            return native.value
    except (OSError, AttributeError, ValueError):
        pass
    return None


def native_arch(env=None, machine=_native_machine):
    """arm64 or amd64: the Windows computer's own processor, whatever this program was built for. Windows is asked
    first (machine()), because the environment is wrong for the app itself: its x64 Python, emulated on Windows on
    Arm, sees PROCESSOR_ARCHITECTURE=AMD64 (measured on an Arm machine on 28 November 2026). The environment is only
    the last resort."""
    native = machine() if machine else None
    if native is not None:
        return "arm64" if native == IMAGE_FILE_MACHINE_ARM64 else "amd64"
    env = os.environ if env is None else env
    name = (env.get("PROCESSOR_ARCHITEW6432") or env.get("PROCESSOR_ARCHITECTURE") or "").upper()
    return "arm64" if name == "ARM64" else "amd64"


def asset_name(platform=None, arch=None):
    """The release asset's name for this platform, or None where there is none (no automatic setup). arch: the
    Windows processor (native_arch), asked when not given."""
    key = platform_key(platform)
    if key == "win32":
        return WINDOWS_ASSETS.get(arch or native_arch())
    return ASSET_NAMES.get(key)


def user_apps(home=None):
    """The Mac's per-user Applications folder."""
    return os.path.join(home or os.path.expanduser("~"), "Applications")


def windows_dir(env=None):
    """Where Ollama's own Windows installer puts it for one user (its ollama.iss: {localappdata}\\Programs\\Ollama).
    Starholm never installs there; an Ollama the person installed is found there."""
    base = (env if env is not None else os.environ).get("LOCALAPPDATA") or ""
    return os.path.join(base, "Programs", "Ollama") if base else ""


def engine_dir(data_dir):
    """Where the Windows setup unpacks the engine: <the app's data folder>\\engine."""
    return os.path.join(data_dir, ENGINE_FOLDER)


def engine_binary(data_dir):
    return os.path.join(engine_dir(data_dir), "ollama.exe")


def candidates(platform=None, home=None, env=None, data_dir=None):
    """Where Ollama is looked for beyond its usual places, in order: ~/Applications/Ollama.app on a Mac (where this
    setup installs it); on Windows, Starholm's own engine (data_dir) and then Ollama's own per-user install."""
    key = platform_key(platform)
    if key == "darwin":
        return (os.path.join(user_apps(home), "Ollama.app", "Contents", "Resources", "ollama"),)
    if key == "win32":
        own = (engine_binary(data_dir),) if data_dir else ()
        return own + ((os.path.join(windows_dir(env), "ollama.exe"),) if windows_dir(env) else ())
    return ()


def how_installed(binary):
    """homebrew, app (Ollama's own app, on a Mac), or other, from where the program really is."""
    real = os.path.realpath(binary or "")
    if "/Cellar/ollama/" in real or "/linuxbrew/" in real or real.startswith("/opt/homebrew/"):
        return "homebrew"
    if "/Ollama.app/" in real:
        return "app"
    return "other"


def upgrade_hint(binary):
    """How to get a newer Ollama, for the way this one was installed."""
    how = how_installed(binary)
    if how == "homebrew":
        return "This model needs a newer Ollama. Update it from the terminal with `brew upgrade ollama`."
    if how == "app":
        return ("This model needs a newer Ollama. Click the Ollama icon in the menu bar and choose Restart to update. "
                "If there is no update there, download the latest Ollama from ollama.com/download.")
    return "This model needs a newer Ollama. Download the latest one from ollama.com/download and install it."


def published_asset(platform=None, fetch_bytes=None, arch=None):
    """This platform's asset in Ollama's latest stable release, as GitHub's API publishes it: {name, tag, url, size,
    sha256}. Raises SetupError("find") when GitHub doesn't answer, when the release is a draft or a pre-release, or
    when the asset is missing or its address or size isn't plain; SetupError("find", "no_digest") when GitHub gives
    no SHA-256 for it."""
    name = asset_name(platform, arch)
    if not name:
        raise SetupError("find", "platform")
    try:
        data = json.loads((fetch_bytes or downloader.fetch_bytes)(RELEASE_API, API_HOSTS, max_bytes=MAX_RELEASE_ANSWER))
    except (OSError, ValueError, downloader.DownloadError, downloader.FetchError) as exc:
        raise SetupError("find", detail=f"{FAILED['find']} ({exc})") from None
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        raise SetupError("find", detail="GitHub's latest Ollama release isn't a stable release.")
    tag = str(data.get("tag_name") or "")
    found = next((a for a in data.get("assets") or [] if isinstance(a, dict) and a.get("name") == name), None)
    if not TAG.fullmatch(tag) or not found:
        raise SetupError("find")
    size = found.get("size")
    url = str(found.get("browser_download_url") or "")
    if url != f"{DOWNLOAD_PREFIX}{tag}/{name}" or not isinstance(size, int) or not 0 < size <= MAX_ASSET:
        raise SetupError("find", detail="Ollama's release on GitHub doesn't look as expected, so nothing was downloaded.")
    digest = DIGEST.fullmatch(str(found.get("digest") or ""))
    if not digest:
        raise SetupError("find", "no_digest")
    return {"name": name, "tag": tag, "url": url, "size": size, "sha256": digest.group(1)}


def turn_cloud_off(path):
    """Before Ollama's first start: its cloud features off (~/.ollama/server.json), only when the file doesn't exist
    yet, so a setting the person made is never changed."""
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(json.dumps({"disable_ollama_cloud": True}, indent=2) + "\n")


def install_mac(archive, apps_dir, run=subprocess.run):
    """Unzip Ollama.app into apps_dir (made if missing) and return the program inside it. ditto keeps the app's
    links, permissions and signature, which Python's zipfile would lose. Never replaces an Ollama.app already there."""
    target = os.path.join(apps_dir, "Ollama.app")
    if os.path.lexists(target):
        raise SetupError("install", detail=f"{FAILED['install']} An Ollama.app is already there.")
    try:
        os.makedirs(apps_dir, exist_ok=True)
        work = tempfile.mkdtemp(prefix=".ollama-install-", dir=apps_dir)
    except OSError as exc:
        raise SetupError("install", detail=f"{FAILED['install']} ({exc.strerror or exc})") from None
    try:
        done = run(["/usr/bin/ditto", "-x", "-k", archive, work], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        unpacked = os.path.join(work, "Ollama.app")
        binary = os.path.join(unpacked, "Contents", "Resources", "ollama")
        if done.returncode != 0 or not os.path.isfile(binary):
            raise SetupError("install")
        os.rename(unpacked, target)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError("install", detail=f"{FAILED['install']} ({exc})") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return os.path.join(target, "Contents", "Resources", "ollama")


def unpack_engine(archive, data_dir, facts=None, progress=None, stop=None):
    """Windows: unpack the engine-only zip into <data_dir>/engine and return its ollama.exe. The zip is unpacked beside
    the folder first and swapped in whole, so a stopped or failed unpack leaves the engine as it was. Every name in
    the zip must stay inside the folder. facts ({tag, name, sha256}) is written into it as ENGINE_FACTS.
    progress(done, total) in bytes; stop() true cancels."""
    target = engine_dir(data_dir)
    work, old = target + ".incoming", target + ".old"
    try:
        for leftover in (work, old):
            shutil.rmtree(leftover, ignore_errors=True)
        os.makedirs(work)
        root = os.path.realpath(work)
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            total = sum(m.file_size for m in members)
            if len(members) > MAX_MEMBERS or total > MAX_UNPACKED:
                raise SetupError("install", "unpack", detail=f"{FAILED['unpack']} (the download is larger than expected)")
            if shutil.disk_usage(work).free < total + (1 << 30):
                raise SetupError("install", "disk")
            done = 0
            for m in members:
                if stop and stop():
                    raise Cancelled()
                parts = m.filename.replace("\\", "/").split("/")
                if m.filename.startswith(("/", "\\")) or ".." in parts or any(":" in part for part in parts):
                    raise SetupError("install", "unpack", detail=f"{FAILED['unpack']} (an unexpected file name)")
                dest = os.path.realpath(os.path.join(root, *[part for part in parts if part]))
                if dest != root and not dest.startswith(root + os.sep):
                    raise SetupError("install", "unpack", detail=f"{FAILED['unpack']} (an unexpected file name)")
                if m.is_dir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(m) as src, open(dest, "wb") as out:
                    while True:
                        if stop and stop():
                            raise Cancelled()
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        if not os.path.isfile(os.path.join(work, "ollama.exe")):
            raise SetupError("install", "unpack", detail=f"{FAILED['unpack']} (no ollama.exe in the download)")
        with open(os.path.join(work, ENGINE_FACTS), "w", encoding="utf-8") as f:
            json.dump(facts or {}, f)
        if os.path.lexists(target):
            os.rename(target, old)  # fails while the old engine still runs: the setup stops it first
        os.rename(work, target)
    except (OSError, zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError) as exc:
        key = "disk" if getattr(exc, "errno", None) == 28 else "unpack"
        raise SetupError("install", key, detail=f"{FAILED[key]} ({getattr(exc, 'strerror', None) or exc})") from None
    finally:
        for leftover in (work, old):
            shutil.rmtree(leftover, ignore_errors=True)
    return engine_binary(data_dir)


def engine_facts(data_dir):
    """Which release Starholm's own engine is ({tag, name, sha256}), or {} when there is none or it can't be read."""
    try:
        with open(os.path.join(engine_dir(data_dir), ENGINE_FACTS), encoding="utf-8") as f:
            facts = json.load(f)
        return facts if isinstance(facts, dict) else {}
    except (OSError, ValueError):
        return {}


def start_app(binary, platform=None, popen=subprocess.Popen):
    """A Mac: open the Ollama app in the background (`open -g -j Ollama.app`), so its updater runs and only one
    Ollama ever starts. False when it couldn't be started, and on every other platform (Windows never opens
    Ollama's desktop app: see start_program)."""
    if platform_key(platform) != "darwin":
        return False
    app = binary.split("/Contents/Resources/")[0]
    try:
        popen(["/usr/bin/open", "-g", "-j", app], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, cwd=os.path.expanduser("~"), start_new_session=True)
        return True
    except OSError:
        return False


def serve(binary, platform=None, popen=subprocess.Popen, log_path=None, env=None):
    """Run `<binary> serve` in the background: listening on 127.0.0.1:11434 only, with no proxy settings, no window
    on Windows, and what it prints appended to log_path (started again above ENGINE_LOG_MAX). The process, or None
    when it couldn't be started."""
    env = {k: v for k, v in (os.environ if env is None else env).items() if k.lower() not in PROXY_VARS}
    env["OLLAMA_HOST"] = "127.0.0.1:11434"
    out = None
    try:
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            fresh = os.path.exists(log_path) and os.path.getsize(log_path) > ENGINE_LOG_MAX
            out = open(log_path, "wb" if fresh else "ab")
            out.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting {binary} serve\n".encode("utf-8"))
            out.flush()
        how = {"start_new_session": True}
        if platform_key(platform) == "win32":  # no console window, and its own group: closing Starholm doesn't end it
            how = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
        return popen([binary, "serve"], env=env, cwd=os.path.expanduser("~"), stdin=subprocess.DEVNULL,
                     stdout=out or subprocess.DEVNULL, stderr=subprocess.STDOUT if out else subprocess.DEVNULL, **how)
    except OSError:
        return None
    finally:
        if out:
            out.close()


def start_program(binary, platform=None, popen=subprocess.Popen, log_path=None):
    """Start an installed Ollama the way that fits where it is. On a Mac, Ollama's own app is opened as an app
    (start_app). Everything else runs `ollama serve` (serve): Homebrew's or another bare program on a Mac, and every
    Ollama on Windows, where the desktop app is never opened: it shows its own window with an account prompt.
    Truthy when it was started (the process for serve)."""
    key = platform_key(platform)
    if key == "darwin" and how_installed(binary) == "app":  # also through the link its app makes in /usr/local/bin
        return start_app(binary if "/Ollama.app/" in binary else os.path.realpath(binary), "darwin", popen=popen)
    return serve(binary, key, popen=popen, log_path=log_path)


def log_tail(path, lines=25, max_bytes=20_000):
    """The last lines of a log, for the app's own log when the engine didn't start ("" when there is none)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


class Setup:
    """The setup's one run at a time, and what the page shows of it.

    find(): the installed Ollama program's path, or None (the app's own search). answering(): whether Ollama's API
    answers. start_existing(): starts an installed Ollama the way the app always has. pull(model): starts the model
    download, False when it couldn't. The rest are replaced by tests: fetch and fetch_bytes (library/downloader.py),
    run and popen (subprocess), sleep, and where files go (work_dir, apps_dir, cloud_config, data_dir). on_start() is
    called just before the setup opens Ollama, so the app's own start (server.start_ollama) waits instead of starting
    it a second time. Windows: data_dir is where the engine is unpacked, arch its processor (native_arch when None),
    stop_engine() ends an engine of Starholm's that still runs before its folder is replaced, on_installed(binary)
    tells the app where the engine now is, and on_started(process) hands it the `ollama serve` the setup started.
    log(text) gets one line per step and failure; engine_log is the file the engine prints to."""

    def __init__(self, *, find, answering, start_existing, pull, work_dir, platform=None, home=None, env=None,
                 apps_dir=None, cloud_config=None, fetch=None, fetch_bytes=None, run=subprocess.run,
                 popen=subprocess.Popen, sleep=time.sleep, wait_seconds=WAIT_SECONDS, on_start=None, data_dir=None,
                 arch=None, stop_engine=None, on_installed=None, on_started=None, log=None, engine_log=None,
                 own_wait_seconds=OWN_WAIT_SECONDS):
        self.find, self.answering, self.start_existing, self.pull = find, answering, start_existing, pull
        self.work_dir, self.platform, self.env = work_dir, platform_key(platform), env
        home = home or os.path.expanduser("~")
        self.apps_dir = apps_dir or user_apps(home)
        self.cloud_config = cloud_config or os.path.join(home, ".ollama", "server.json")
        self.fetch, self.fetch_bytes = fetch, fetch_bytes
        self.run, self.popen, self.sleep, self.wait_seconds = run, popen, sleep, wait_seconds
        self.on_start = on_start  # told when the setup opens Ollama (the app's start throttle, so it starts once)
        self.data_dir, self.arch, self.stop_engine, self.on_installed = data_dir, arch, stop_engine, on_installed
        self.on_started, self.engine_log, self.own_wait_seconds = on_started, engine_log, own_wait_seconds
        self._log = log
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.thread = None
        self.job = {"state": "idle", "step": None, "completed": 0, "total": 0, "message": "", "failed": None,
                    "skipped_install": False, "model": None}
        self.release = None  # the latest release's asset for the offer, from look_up (or the last setup)
        self.looked = (0.0, False)  # (when the facts were last asked for, whether they came)
        self.lookup_thread = None

    def log(self, text):
        """One line for the app's log. Never raises."""
        if self._log:
            try:
                self._log(text)
            except Exception:  # noqa: BLE001 - a full disk or a closed log must not stop the setup
                pass

    def known_release(self):
        """The release asset the offer shows ({name, tag, url, size, sha256}), or None until look_up has found it."""
        with self._lock:
            return self.release

    def look_up(self, wait=False):
        """Read the latest release's facts in the background (at most every LOOKUP_EVERY seconds, LOOKUP_RETRY after a
        failure), for the size the offer shows. Never raises."""
        if not asset_name(self.platform, self.arch):
            return
        with self._lock:
            when, got = self.looked
            due = time.time() - when >= (LOOKUP_EVERY if got else LOOKUP_RETRY)
            if not due or (self.lookup_thread and self.lookup_thread.is_alive()):
                return
            self.looked = (time.time(), False)
            self.lookup_thread = threading.Thread(target=self._look_up, daemon=True, name="ollama-release")
            self.lookup_thread.start()
        if wait:
            self.lookup_thread.join(30)

    def _look_up(self):
        try:
            asset = published_asset(self.platform, self.fetch_bytes, self.arch)
        except SetupError:
            return
        with self._lock:
            self.release, self.looked = asset, (time.time(), True)

    def view(self):
        with self._lock:
            return dict(self.job)

    def busy(self):
        with self._lock:
            return self.job["state"] == "running"

    def start(self, model):
        """Start the setup; model: what to download once Ollama answers (None: Ollama only). False when it is
        already running."""
        with self._lock:
            if self.job["state"] == "running":
                return False
            self._stop = threading.Event()
            self.job = {"state": "running", "step": "find", "completed": 0, "total": 0, "message": "",
                        "failed": None, "skipped_install": False, "model": model}
            self.thread = threading.Thread(target=self._run, args=(model, self._stop), daemon=True,
                                           name="ollama-setup")
            self.thread.start()
        return True

    def cancel(self):
        """Stop the setup between two chunks of the download, or before the next step. False when it isn't running.
        A partial download is kept, so Try again goes on from there."""
        with self._lock:
            if self.job["state"] != "running":
                return False
            self._stop.set()
            self.job.update(state="cancelled", message="Cancelled.")
        return True

    def _set(self, **changes):
        with self._lock:
            if self.job["state"] == "running":
                self.job.update(changes)

    def _check_stop(self, stop):
        if stop.is_set():
            raise Cancelled()

    def _run(self, model, stop):
        archive = None
        try:
            self.log(f"Set up the AI: started (model to download: {model or 'none'}).")
            answering = self.answering()
            binary = self.find()
            own = engine_binary(self.data_dir) if self.platform == "win32" and self.data_dir else None
            if answering:
                self.log("An AI engine already answers on this computer, so nothing is installed.")
                self._set(skipped_install=True, step="start")
            elif binary and own and os.path.normcase(binary) != os.path.normcase(own):
                # Windows, an Ollama the person installed that isn't running: it gets own_wait_seconds to answer.
                # When it doesn't (its desktop app can fail to start its server), Starholm installs its own engine.
                self._set(step="start")
                self.log(f"Starting the Ollama already on this computer ({binary}).")
                self.start_existing()
                if self._answers_within(stop, self.own_wait_seconds):
                    self._set(skipped_install=True)
                else:
                    self.log(f"It didn't answer within {self.own_wait_seconds} seconds, so Starholm installs its "
                             "own engine.")
                    binary = None
            elif binary:
                self._set(skipped_install=True, step="start")
                self.log(f"Starting the AI engine already on this computer ({binary}).")
                self.start_existing()
            if not answering and not binary:
                if self.platform == "win32" and not self.data_dir:
                    raise SetupError("find", "platform")
                asset = published_asset(self.platform, self.fetch_bytes, self.arch)  # read again: the latest, now
                with self._lock:
                    self.release, self.looked = asset, (time.time(), True)
                self._check_stop(stop)
                self.log(f"Downloading {asset['name']} {asset['tag']} ({asset['size']} bytes).")
                archive = os.path.join(self.work_dir, asset["name"])
                self._download(asset, archive, stop)
                self._check_stop(stop)
                self.log("The download matches its published SHA-256. Installing.")
                self._set(step="install", completed=0, total=0)
                if self.platform == "darwin":
                    binary = install_mac(archive, self.apps_dir, run=self.run)
                else:
                    if self.stop_engine:
                        self.stop_engine()
                    binary = unpack_engine(archive, self.data_dir, stop=stop.is_set,
                                           facts={k: asset[k] for k in ("tag", "name", "sha256")},
                                           progress=lambda done, total: self._set(completed=done, total=total))
                self.log(f"Installed {binary}.")
                if self.on_installed:
                    self.on_installed(binary)
                self._check_stop(stop)
                answering = self.answering()
                if not answering and self.on_start:
                    self.on_start()  # the app's start throttle is set before the page can see step "start"
                self._set(step="start", completed=0, total=0)
                turn_cloud_off(self.cloud_config)
                if not answering:
                    started = start_program(binary, self.platform, popen=self.popen, log_path=self.engine_log)
                    if not started:
                        self.log(f"{binary} couldn't be started.")
                        raise SetupError("start")
                    if self.on_started:
                        self.on_started(started)
            self._wait(stop)
            self.log("The AI engine answers.")
            self._set(step="model")
            if model and not self.pull(model):
                raise SetupError("model")
            self.log(f"Set up the AI: done{f'; {model} is downloading' if model else ''}.")
            with self._lock:
                if self.job["state"] == "running":
                    self.job.update(state="done", message="")
        except Cancelled:
            self.log("Set up the AI: cancelled.")
        except SetupError as exc:
            self.log(f"Set up the AI stopped at step '{exc.step}' ({exc.key}): {exc}")
            with self._lock:
                if self.job["state"] == "running":
                    self.job.update(state="error", failed=exc.step, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - an unexpected failure is reported on the page, never raised
            with self._lock:
                step = self.job["step"] or "find"
                if self.job["state"] == "running":
                    self.job.update(state="error", failed=step, message=f"{FAILED.get(step) or FAILED['find']} ({exc})")
            self.log(f"Set up the AI stopped at step '{step}' with an unexpected error: {exc!r}")
        finally:
            if archive and os.path.exists(archive):  # the finished file is only needed for the install
                try:
                    os.remove(archive)
                except OSError:
                    pass

    def _download(self, asset, archive, stop):
        """Download the asset, then check its SHA-256 against GitHub's digest. downloader.fetch hashes the finished
        file and deletes it when the digest differs (ChecksumError), so a wrong file never reaches the install."""
        self._set(step="download", completed=downloader.partial_size(archive), total=asset["size"])

        def progress(done, total):
            self._set(completed=done, total=total or asset["size"])
            if total and done >= total:
                self._set(step="check")

        try:
            (self.fetch or downloader.fetch)(asset["url"], archive, DOWNLOAD_HOSTS, size=asset["size"],
                                             sha256=asset["sha256"], progress=progress, stop=stop.is_set)
        except downloader.Stopped:
            raise Cancelled() from None
        except downloader.ChecksumError as exc:
            for path in (archive, archive + ".part"):
                if os.path.exists(path):
                    os.remove(path)
            raise SetupError("check", detail=f"{FAILED['check']} ({exc})") from None
        except (downloader.DownloadError, downloader.FetchError) as exc:
            raise SetupError("download", detail=f"{FAILED['download']} ({exc})") from None
        except OSError as exc:
            key = "disk" if getattr(exc, "errno", None) == 28 else "download"
            raise SetupError("download", key, detail=f"{FAILED[key]} ({exc.strerror or exc})") from None
        self._set(step="check")

    def _answers_within(self, stop, seconds):
        """Whether Ollama's API answers within that many seconds."""
        deadline = time.monotonic() + seconds
        while not self.answering():
            self._check_stop(stop)
            if time.monotonic() >= deadline:
                return False
            self.sleep(1)
        return True

    def _wait(self, stop):
        """Until Ollama's API answers (wait_seconds at most). When it never does, the engine's last lines go to the
        app's log, so the reason can be read there."""
        if not self._answers_within(stop, self.wait_seconds):
            tail = log_tail(self.engine_log) if self.engine_log else ""
            self.log(f"The AI engine didn't answer within {self.wait_seconds} seconds."
                     + (f" Its last lines:\n{tail}" if tail else " It printed nothing."))
            raise SetupError("start")


def offer(rows, gb, platform=None, release=None):
    """What the page says before anything downloads: whether automatic setup is available here, the engine's size as
    GitHub's API reports it (release: Setup.known_release(); None until it is known), and the model the setup will
    download for this computer ({available, engine_bytes, model, model_name, model_gb}; model is None when no row
    fits)."""
    from model_setup import recommended  # app/model_setup.py
    usable = [r for r in rows if r["available"]]
    model = recommended(usable, gb)
    row = next((r for r in usable if r["model"] == model), None)
    return {"available": bool(asset_name(platform)), "engine_bytes": release["size"] if release else None, "model": model,
            "model_name": row["name"] if row else None, "model_gb": row["download_gb"] if row else None}
