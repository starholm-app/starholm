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

Windows (OllamaSetup.exe, Inno Setup, per-user under %LOCALAPPDATA%\\Programs\\Ollama, run with /VERYSILENT) is built
behind the same steps but is NOT VERIFIED: the app has not been ported to Windows, and /VERYSILENT is undocumented by
Ollama. See WINDOWS_NOT_VERIFIED.

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
WINDOWS_NOT_VERIFIED = True  # the Windows path has never run: no Windows port yet, and /VERYSILENT is undocumented

# Each platform's asset in an Ollama release, by name.
ASSET_NAMES = {"darwin": "Ollama-darwin.zip", "win32": "OllamaSetup.exe"}  # win32: not verified
# Inno Setup's standard switches: no pages, no message boxes, no restart. Not verified for OllamaSetup.exe.
WINDOWS_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-")

STEPS = ("find", "download", "check", "install", "start", "model")
# What failed, in plain words, for each step. The page adds the manual route and Try again.
FAILED = {
    "platform": "Automatic setup isn't available on this computer yet.",
    "find": "Ollama's download couldn't be found on GitHub. Check that this computer is online, then try again.",
    "no_digest": ("GitHub didn't publish a fingerprint for Ollama's download, so it couldn't be checked and wasn't "
                  "downloaded. Try again later."),
    "download": "The Ollama download stopped before it finished. Check that this computer is online, then try again.",
    "disk": "There isn't enough free space on this computer for Ollama. Free up some space, then try again.",
    "check": ("The downloaded file didn't match the one Ollama published, so it was deleted and nothing was "
              "installed."),
    "install": "Ollama couldn't be installed in the Applications folder in your home folder.",
    "start": "Ollama was installed but didn't start.",
    "model": "Ollama is running, but the model download couldn't start.",
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


def asset_name(platform=None):
    """The release asset's name for this platform, or None where there is none (no automatic setup)."""
    return ASSET_NAMES.get(platform_key(platform))


def user_apps(home=None):
    """The Mac's per-user Applications folder."""
    return os.path.join(home or os.path.expanduser("~"), "Applications")


def windows_dir(env=None):
    """Where OllamaSetup.exe installs for one user (its ollama.iss: DefaultDirName={localappdata}\\Programs\\Ollama)."""
    base = (env if env is not None else os.environ).get("LOCALAPPDATA") or ""
    return os.path.join(base, "Programs", "Ollama") if base else ""


def candidates(platform=None, home=None, env=None):
    """The places this setup installs Ollama to, for the app's search list: ~/Applications/Ollama.app on a Mac, the
    per-user folder on Windows (not verified)."""
    key = platform_key(platform)
    if key == "darwin":
        return (os.path.join(user_apps(home), "Ollama.app", "Contents", "Resources", "ollama"),)
    if key == "win32" and windows_dir(env):
        return (os.path.join(windows_dir(env), "ollama.exe"),)
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


def published_asset(platform=None, fetch_bytes=None):
    """This platform's asset in Ollama's latest stable release, as GitHub's API publishes it: {name, tag, url, size,
    sha256}. Raises SetupError("find") when GitHub doesn't answer, when the release is a draft or a pre-release, or
    when the asset is missing or its address or size isn't plain; SetupError("find", "no_digest") when GitHub gives
    no SHA-256 for it."""
    name = asset_name(platform)
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


def install_windows(setup_exe, env=None, run=subprocess.run):
    """NOT VERIFIED. Run OllamaSetup.exe silently for this user and return the installed ollama.exe. Ollama's installer
    asks for no admin rights (PrivilegesRequired=lowest) and starts Ollama when it finishes."""
    folder = windows_dir(env)
    if not folder:
        raise SetupError("install")
    try:
        done = run([setup_exe, *WINDOWS_FLAGS], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError("install", detail=f"{FAILED['install']} ({exc})") from None
    binary = os.path.join(folder, "ollama.exe")
    if done.returncode != 0 or not os.path.isfile(binary):
        raise SetupError("install")
    return binary


def start_app(binary, platform=None, popen=subprocess.Popen):
    """Open the Ollama app in the background, so its updater runs: `open -g -j Ollama.app` on a Mac, the tray app on
    Windows (not verified). False when it couldn't be started."""
    key = platform_key(platform)
    if key == "darwin":
        app = binary.split("/Contents/Resources/")[0]
        args = ["/usr/bin/open", "-g", "-j", app]
    elif key == "win32":
        args = [os.path.join(os.path.dirname(binary), "ollama app.exe")]
    else:
        return False
    try:
        popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=os.path.expanduser("~"), start_new_session=True)
        return True
    except OSError:
        return False


class Setup:
    """The setup's one run at a time, and what the page shows of it.

    find(): the installed Ollama program's path, or None (the app's own search). answering(): whether Ollama's API
    answers. start_existing(): starts an installed Ollama the way the app always has. pull(model): starts the model
    download, False when it couldn't. The rest are replaced by tests: fetch and fetch_bytes (library/downloader.py),
    run and popen (subprocess), sleep, and where files go (work_dir, apps_dir, cloud_config). on_start() is called
    just before the setup opens Ollama, so the app's own start (server.start_ollama) waits instead of starting it
    a second time."""

    def __init__(self, *, find, answering, start_existing, pull, work_dir, platform=None, home=None, env=None,
                 apps_dir=None, cloud_config=None, fetch=None, fetch_bytes=None, run=subprocess.run,
                 popen=subprocess.Popen, sleep=time.sleep, wait_seconds=WAIT_SECONDS, on_start=None):
        self.find, self.answering, self.start_existing, self.pull = find, answering, start_existing, pull
        self.work_dir, self.platform, self.env = work_dir, platform_key(platform), env
        home = home or os.path.expanduser("~")
        self.apps_dir = apps_dir or user_apps(home)
        self.cloud_config = cloud_config or os.path.join(home, ".ollama", "server.json")
        self.fetch, self.fetch_bytes = fetch, fetch_bytes
        self.run, self.popen, self.sleep, self.wait_seconds = run, popen, sleep, wait_seconds
        self.on_start = on_start  # told when the setup opens Ollama (the app's start throttle, so it starts once)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.thread = None
        self.job = {"state": "idle", "step": None, "completed": 0, "total": 0, "message": "", "failed": None,
                    "skipped_install": False, "model": None}
        self.release = None  # the latest release's asset for the offer, from look_up (or the last setup)
        self.looked = (0.0, False)  # (when the facts were last asked for, whether they came)
        self.lookup_thread = None

    def known_release(self):
        """The release asset the offer shows ({name, tag, url, size, sha256}), or None until look_up has found it."""
        with self._lock:
            return self.release

    def look_up(self, wait=False):
        """Read the latest release's facts in the background (at most every LOOKUP_EVERY seconds, LOOKUP_RETRY after a
        failure), for the size the offer shows. Never raises."""
        if not asset_name(self.platform):
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
            asset = published_asset(self.platform, self.fetch_bytes)
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
            binary = self.find()
            if binary:
                self._set(skipped_install=True, step="start")
                if not self.answering():
                    self.start_existing()
            else:
                if self.platform == "win32" and not windows_dir(self.env):
                    raise SetupError("find", "platform")
                asset = published_asset(self.platform, self.fetch_bytes)  # read again: the latest, now
                with self._lock:
                    self.release, self.looked = asset, (time.time(), True)
                self._check_stop(stop)
                archive = os.path.join(self.work_dir, asset["name"])
                self._download(asset, archive, stop)
                self._check_stop(stop)
                self._set(step="install", completed=0, total=0)
                if self.platform == "darwin":
                    binary = install_mac(archive, self.apps_dir, run=self.run)
                else:
                    binary = install_windows(archive, env=self.env, run=self.run)
                self._check_stop(stop)
                answering = self.answering()
                if not answering and self.on_start:
                    self.on_start()  # the app's start throttle is set before the page can see step "start"
                self._set(step="start")
                turn_cloud_off(self.cloud_config)
                if not answering:
                    if not start_app(binary, self.platform, popen=self.popen):
                        raise SetupError("start")
            self._wait(stop)
            self._set(step="model")
            if model and not self.pull(model):
                raise SetupError("model")
            with self._lock:
                if self.job["state"] == "running":
                    self.job.update(state="done", message="")
        except Cancelled:
            pass
        except SetupError as exc:
            with self._lock:
                if self.job["state"] == "running":
                    self.job.update(state="error", failed=exc.step, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - an unexpected failure is reported on the page, never raised
            with self._lock:
                if self.job["state"] == "running":
                    step = self.job["step"] or "find"
                    self.job.update(state="error", failed=step, message=f"{FAILED[step]} ({exc})")
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

    def _wait(self, stop):
        """Until Ollama's API answers (WAIT_SECONDS at most)."""
        deadline = time.monotonic() + self.wait_seconds
        while not self.answering():
            self._check_stop(stop)
            if time.monotonic() >= deadline:
                raise SetupError("start")
            self.sleep(1)


def offer(rows, gb, platform=None, release=None):
    """What the page says before anything downloads: whether automatic setup is available here, the engine's size as
    GitHub's API reports it (release: Setup.known_release(); None until it is known), and the model the setup will
    download for this computer ({available, engine_bytes, model, model_name, model_gb, not_verified}; model is None
    when no row fits)."""
    from model_setup import recommended  # app/model_setup.py
    usable = [r for r in rows if r["available"]]
    model = recommended(usable, gb)
    row = next((r for r in usable if r["model"] == model), None)
    return {"available": bool(asset_name(platform)), "engine_bytes": release["size"] if release else None, "model": model,
            "model_name": row["name"] if row else None, "model_gb": row["download_gb"] if row else None,
            "not_verified": platform_key(platform) == "win32" and WINDOWS_NOT_VERIFIED}
