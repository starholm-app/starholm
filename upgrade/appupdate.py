"""Starholm's own updates: "Check for updates" (Settings › Status, and Check for Updates… in the Mac app menu), the
"Download updates automatically" switch, and the restart that puts a new version in place.

WHEN IT CONNECTS. Only when the person presses Check for updates, or, with "Download updates automatically" on
(settings.json "updates": {"auto": true}, off by default), AUTO_FIRST_WAIT seconds after launch and then every
AUTO_EVERY seconds (an hour) while the app stays open. latest.json is a public file of under 1 KB, so an hourly read
costs nothing, and a new version reaches someone who leaves Starholm open within the hour. A newer version then
downloads in the background and the ready notice opens by itself. The only host is UPDATE_HOST. The request carries
nothing about the person, and never the upgrade key: it reads a public file, with the app's usual User-Agent.

THE MANIFEST. <UPDATE_BASE>/<platform>/latest.json, platform "mac" or "win" (the folders the downloads live in:
mac/Starholm-<version>.zip, win/Starholm-Setup-<version>.exe). One JSON object:

    {"manifest": "<the signed text>", "signature": "<base64 of a 64-byte Ed25519 signature>"}

The signed text is JSON itself, kept as a string so its exact bytes are what was signed:

    {"format": 1, "app": "starholm", "platform": "mac", "version": "1.0.3",
     "url": "https://download.starholm.app/mac/Starholm-1.0.3.zip", "size": 123, "sha256": "<64 hex>",
     "notes": "One or two lines on what's new.", "released": "2026-11-26"}

The signature is made with the pack-signing key (upgrade/packs.py PACK_PUBLIC_KEY_BASE64, the key already in the app)
over DOMAIN followed by the signed text's UTF-8 bytes. A pack's pack.sig signs pack.json's bytes alone, so neither
signature can stand in for the other: a pack signature never checks out as an app manifest, and an app manifest's
signed bytes start with DOMAIN, which is not JSON, so they can never pass as a pack.json. packbuilder/app_manifest.py
writes the file at release time (ops/release-mac.sh).

WHAT IS INSTALLED. Only a build whose manifest signature checks out, whose platform is this one, whose version is
newer than the one running, whose download has the manifest's size and SHA-256, and (on the Mac) whose app inside the
zip carries the downloadable app's bundle id, the manifest's version in its Info.plist and a code signature that
codesign verifies. Anything else is refused and deleted.

THE RESTART (Mac). "Restart now" pauses a pack download, starts INSTALL_HELPER detached, and quits. The helper waits
for the app to end, renames the old Starholm.app aside, moves the new one into its place, opens it, and deletes the
old one and the update's folder in <data>/updates/. If the swap fails it puts the old app back and opens that, and
the update stays ready. If the new version doesn't start, it is deleted with its download, the old one is put back
and opened, and that version is not downloaded again by the automatic check (state "failed"; Check for updates
still offers it). User data lives in ~/Library/Application Support/Starholm and is never touched. The next start
also deletes anything an interrupted restart left (tidy_beside(), at_start()). When the app's folder can't be written
(an app opened from the disk image, from Downloads under App Translocation, or a folder owned by another account),
"Restart now" shows the new app in the Finder with what to do instead.

THE RESTART (Windows). The download is the Inno Setup installer (ops/windows-installer.iss), checked by size,
SHA-256 and its MZ header. "Restart now" starts WIN_INSTALL_HELPER in Windows PowerShell, detached, and quits. The
helper waits for the app to end, exports the installer's Apps entry (the HKCU uninstall key of its fixed AppId),
moves the install folder aside to .Starholm-old-<hex>, and runs the installer silently into the same folder. The
same AppId keeps one entry in Apps and one folder. It then opens the new Starholm.exe and waits for its first start
to read RESULT_FILE (at_start() deletes it): that is the proof the new version runs. Then the old folder and the
update's folder (the installer, the exported entry) are deleted. If the installer fails or the new version doesn't
start within WIN_START_WAIT seconds, its processes are stopped, its folder deleted, the old folder and the old Apps
entry put back, the download deleted, and the old version opened. Inno Setup cleans up its own temporary folder
(is-*.tmp in %TEMP%).
When the install folder or the folder around it can't be written, "Restart now" opens the installer's folder with
what to do instead.
"""
import base64
import binascii
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from library import downloader, safe_fetch
from library.downloader import DownloadError, Stopped
from library.safe_fetch import FetchError

# ---- the version of this code, and the one place the app checks for a newer one ----
APP_VERSION = "1.0.6"  # ops/build-mac-app.sh and ops/build-windows-app.ps1 read it (the latter passes it to Inno Setup)
UPDATE_HOST = "download.starholm.app"
UPDATE_BASE = f"https://{UPDATE_HOST}"
# ----

DOMAIN = b"starholm app-update manifest v1\n"  # signed before the manifest's bytes; never the start of a pack.json
MANIFEST_NAME = "latest.json"
MAX_MANIFEST = 64_000  # bytes in latest.json
MIN_SIZE, MAX_SIZE = 1_000_000, 4_000_000_000  # bytes the app's download may have
MAX_NOTES = 400  # characters
BUNDLE_IDS = ("app.starholm.mac",)  # ops/build-mac-app.sh BUNDLE_ID: the only app an update may install
APP_NAME = "Starholm.app"
VERSION_RE = re.compile(r"\d{1,4}(?:\.\d{1,4}){1,3}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
STATE_FILE = "app-update.json"    # in the app's folder: the last check and the update waiting to be installed
RESULT_FILE = "app-update-result.json"  # written by INSTALL_HELPER: what the last restart did
AUTO_FIRST_WAIT = 20  # seconds after launch before the first automatic check
AUTO_EVERY = 3600     # seconds between automatic checks while the app stays open
AUTO_TICK = 300       # seconds between looks at the clock: a Mac waking from sleep checks within 5 minutes
CODESIGN = "/usr/bin/codesign"
DITTO = "/usr/bin/ditto"
WIN_EXE = "Starholm.exe"
WIN_APP_ID = "{63B4D449-2116-4BDC-A784-18C33E143B59}"  # ops/windows-installer.iss AppId
WIN_UNINSTALL_KEY = rf"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{WIN_APP_ID}_is1"
WIN_START_WAIT = 120  # seconds the Windows helper waits for the new version's first start
# How the Windows helper is started: in a console of its own that is never shown (CREATE_NO_WINDOW), in its own
# process group, so it outlives the app. Not DETACHED_PROCESS: PowerShell started with no console at all never ran.
WIN_SPAWN_FLAGS = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
OLD_NEW = re.compile(r"\.Starholm-(old|new)-[0-9a-f]{8}(\.app)?")  # what a restart leaves beside the app for a moment


def powershell():
    """Windows PowerShell 5.1, which every Windows 10 and 11 has, by its full path (never one found on PATH)."""
    import ntpath
    return ntpath.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "WindowsPowerShell", "v1.0",
                       "powershell.exe")


class UpdateError(Exception):
    """A manifest, a download or an app that can't be used. code: "bad_signature", "invalid", "platform",
    "not_newer", "download", "bad_app"; the message is a sentence for the page."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def platform_dir(platform=None):
    return "win" if (platform or sys.platform) == "win32" else "mac"


def manifest_url(platform=None, base=None):
    return f"{(base or UPDATE_BASE).rstrip('/')}/{platform_dir(platform)}/{MANIFEST_NAME}"


def parse_version(text):
    """"1.0.3" -> (1, 0, 3); None for anything that isn't 2 to 4 dot-separated numbers."""
    if not isinstance(text, str) or not VERSION_RE.fullmatch(text):
        return None
    return tuple(int(p) for p in text.split("."))


def is_newer(candidate, current):
    """Whether version text candidate is later than current. 1.0.10 is later than 1.0.9; 1.0 equals 1.0.0."""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def bundle_version(bundle):
    """CFBundleShortVersionString from an app bundle's Info.plist, or None."""
    try:
        with open(os.path.join(bundle, "Contents", "Info.plist"), "rb") as f:
            info = plistlib.loads(f.read(1_000_000))
    except Exception:  # noqa: BLE001 - missing or not a plist
        return None
    value = info.get("CFBundleShortVersionString") if isinstance(info, dict) else None
    return value if parse_version(value) else None


def current_version(bundle=None):
    """The version running: the Mac app's Info.plist when the server runs inside the downloadable app (the build
    writes STARHOLM_VERSION there), else APP_VERSION."""
    if bundle and bundle.endswith(".app"):
        return bundle_version(bundle) or APP_VERSION
    return APP_VERSION


# ---------- the signed manifest ----------

def sign_message(manifest_text_bytes):
    """What the signature covers: DOMAIN, then the signed text's bytes."""
    return DOMAIN + manifest_text_bytes


def verify(raw, current, platform=None, public_key_base64=None, base=None):
    """latest.json's bytes, checked. Returns the signed manifest as a dict, with "newer": whether its version is later
    than current. Raises UpdateError when the signature doesn't check out or anything in it isn't valid. A manifest
    that isn't newer is returned with newer False: the check says "up to date", and nothing installs it."""
    if public_key_base64 is None:
        from upgrade import packs
        public_key_base64 = packs.PACK_PUBLIC_KEY_BASE64
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_MANIFEST:
        raise UpdateError("invalid", "The update information isn't valid.")
    try:
        outer = json.loads(bytes(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        outer = None
    if not isinstance(outer, dict) or not isinstance(outer.get("manifest"), str) or \
            not isinstance(outer.get("signature"), str):
        raise UpdateError("invalid", "The update information isn't valid.")
    text = outer["manifest"].encode("utf-8")
    try:
        sig = base64.b64decode(outer["signature"].strip(), validate=True)
        if len(sig) != 64:
            raise ValueError("not 64 bytes")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_base64)).verify(sig, sign_message(text))
    except (InvalidSignature, ValueError, TypeError, binascii.Error):
        raise UpdateError("bad_signature", "The update information isn't signed by Starholm, so it was ignored.") \
            from None
    try:
        m = json.loads(text.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        m = None
    if not isinstance(m, dict) or m.get("format") != 1 or m.get("app") != "starholm":
        raise UpdateError("invalid", "The update information isn't valid.")
    if m.get("platform") != platform_dir(platform):
        raise UpdateError("platform", "The update information is for another kind of computer.")
    if parse_version(m.get("version")) is None:
        raise UpdateError("invalid", "The update information has no valid version.")
    url = m.get("url")
    host = (urllib.parse.urlsplit(base or UPDATE_BASE).hostname or "").lower()
    try:
        downloader.check_redirect(url, {host})  # https, this host only, no user name, no other port
    except FetchError:
        raise UpdateError("invalid", "The update information points somewhere it shouldn't.") from None
    if not urllib.parse.urlsplit(url).path.startswith(f"/{platform_dir(platform)}/"):
        raise UpdateError("invalid", "The update information points somewhere it shouldn't.")
    size, sha = m.get("size"), str(m.get("sha256") or "")
    if not isinstance(size, int) or isinstance(size, bool) or not MIN_SIZE <= size <= MAX_SIZE \
            or not SHA256_RE.fullmatch(sha):
        raise UpdateError("invalid", "The update information isn't valid.")
    notes = m.get("notes") if isinstance(m.get("notes"), str) else ""
    notes = re.sub(r"[\x00-\x1f\x7f]+", " ", notes).strip()[:MAX_NOTES]
    released = m.get("released") if isinstance(m.get("released"), str) and DATE_RE.fullmatch(m["released"]) else None
    return {"version": m["version"], "url": url, "size": size, "sha256": sha, "notes": notes, "released": released,
            "platform": m["platform"], "newer": is_newer(m["version"], current)}


def fetch_manifest(platform=None, base=None):
    """latest.json's bytes from UPDATE_HOST (no redirects, at most MAX_MANIFEST bytes). Raises DownloadError or
    FetchError."""
    url = manifest_url(platform, base)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return downloader.fetch_bytes(url, frozenset({host}), max_bytes=MAX_MANIFEST)


# ---------- the downloaded app ----------

def _run(args, timeout=120):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)


def check_app(app, version):
    """Why the app at path app can't be installed as `version`, or None when it can: the downloadable app's bundle
    id, the version in its Info.plist, its executable, no link leading out of it, and a code signature that codesign
    verifies (--deep --strict)."""
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            info = plistlib.loads(f.read(1_000_000))
    except Exception:  # noqa: BLE001
        return "it has no readable Info.plist"
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") not in BUNDLE_IDS:
        return "it isn't Starholm"
    if info.get("CFBundleShortVersionString") != version:
        return f"it says it is version {str(info.get('CFBundleShortVersionString'))[:20]}, not {version}"
    exe = os.path.join(app, "Contents", "MacOS", str(info.get("CFBundleExecutable") or ""))
    if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        return "it has no program to run"
    top = os.path.realpath(app)
    for folder, dirs, files in os.walk(app):
        for name in dirs + files:
            path = os.path.join(folder, name)
            if os.path.islink(path) and not os.path.realpath(path).startswith(top + os.sep):
                return "it has a link that leads outside it"
    try:
        result = _run([CODESIGN, "--verify", "--deep", "--strict", app], timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"its code signature couldn't be checked ({exc.__class__.__name__})"
    if result.returncode != 0:
        return "its code signature doesn't check out"
    return None


def unpack(zip_path, dest):
    """Unpack the downloaded zip (made by ditto -c -k --keepParent) into the empty folder dest with ditto. Returns
    the path of the Starholm.app inside, or raises UpdateError."""
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, mode=0o700)
    try:
        result = _run([DITTO, "-x", "-k", zip_path, dest], timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError("bad_app", f"The update couldn't be unpacked ({exc.__class__.__name__}).") from None
    if result.returncode != 0:
        raise UpdateError("bad_app", "The update couldn't be unpacked.")
    names = [n for n in os.listdir(dest) if n != "__MACOSX"]
    if names != [APP_NAME] or os.path.islink(os.path.join(dest, APP_NAME)):
        raise UpdateError("bad_app", "The update doesn't hold the Starholm app.")
    return os.path.join(dest, APP_NAME)


def check_installer(path, size):
    """Why the downloaded Windows installer at path can't be run, or None when it can: a plain file (no link) of
    the manifest's size that starts as a Windows program does (MZ). Its SHA-256 was checked as it downloaded."""
    try:
        if not path or os.path.islink(path) or not os.path.isfile(path):
            return "it is no longer on this computer"
        if os.path.getsize(path) != size:
            return "it isn't the size the update information gives"
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return "it isn't a Windows program"
    except OSError as exc:
        return f"it couldn't be read ({exc.__class__.__name__})"
    return None


def _writable(folder):
    probe = os.path.join(folder, f".starholm-write-test-{secrets.token_hex(4)}")
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.remove(probe)
    except OSError:
        return False
    return True


def install_target(bundle, platform=None):
    """Whether "Restart now" can replace the app at bundle in place: (True, None), or (False, why).
    Mac: a Starholm.app the server runs inside, outside a disk image and App Translocation, whose folder accepts a new
    file (made and deleted here). Windows: bundle is Starholm.exe, and both its install folder and the folder around it
    (where the old one is moved aside) accept a new file."""
    if (platform or sys.platform) == "win32":
        if not bundle or os.path.basename(bundle).lower() != WIN_EXE.lower() or not os.path.isfile(bundle):
            return False, "source"
        folder = os.path.dirname(os.path.realpath(bundle))
        if not _writable(folder) or not _writable(os.path.dirname(folder)):
            return False, "read_only"
        return True, None
    if not bundle or not bundle.endswith(".app") or not os.path.isdir(bundle):
        return False, "source"
    real = os.path.realpath(bundle)
    if "/AppTranslocation/" in real:
        return False, "translocated"
    if not _writable(os.path.dirname(real)) or not os.access(real, os.W_OK):
        return False, "read_only"
    return True, None


def tidy_beside(bundle, platform=None):
    """Delete what an interrupted restart may have left beside the app: .Starholm-old-* and .Starholm-new-* folders.
    Mac: beside Starholm.app (names ending .app). Windows: beside the install folder that holds Starholm.exe (no
    suffix). Only those names, only folders, never a link."""
    win = (platform or sys.platform) == "win32"
    if not bundle or (win and os.path.basename(bundle).lower() != WIN_EXE.lower()) \
            or (not win and not bundle.endswith(".app")):
        return
    real = os.path.realpath(bundle)
    parent = os.path.dirname(os.path.dirname(real) if win else real)
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        path = os.path.join(parent, name)
        found = OLD_NEW.fullmatch(name)
        if found and bool(found.group(2)) != win and os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)


# Run by the bundled Python, detached, after "Restart now", with -I and a fixed argument list:
#   pid  bundle  new_app  result_file  data_dir (or "")  update_dir
# Wait (at most a minute) for the app to end; if it is still running then, touch nothing (the update stays ready for
# next time). Otherwise swap the bundles with two renames in the same folder: the old app to
# .Starholm-old-<hex>.app, the new one into its place (copied into the folder first with ditto when it is on another
# disk). If the second rename fails, the old app goes back. Then open the app at bundle (-n: a new copy even while
# LaunchServices still remembers the old one; --env keeps a test's data folder) and check with ps that it runs. Only
# then are the old one and update_dir (<data>/updates/<version>, what is left of the download) deleted; when the new
# one doesn't start, it is moved aside and deleted with update_dir, the old one is put back and opened, and the result
# says so (restored). update_dir is used only when new_app is inside it. Everything is imported first: the old bundle,
# which holds this Python, is renamed while it runs. The result goes to result_file as JSON for the next start to show.
INSTALL_HELPER = r"""import json, os, secrets, shutil, subprocess, sys, time
pid, bundle, new_app, result_file, data_dir = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
update_dir = sys.argv[6] if len(sys.argv) > 6 else ""
if not update_dir or not new_app.startswith(update_dir.rstrip("/") + "/"):
    update_dir = ""
quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
def note(**r):
    try:
        tmp = result_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({**r, "at": time.time()}, f)
        os.replace(tmp, result_file)
    except OSError:
        pass
def runs(path):
    exe = os.path.join(path, "Contents", "MacOS") + "/"
    for _ in range(80):
        time.sleep(0.1)
        try:
            ps = subprocess.run(["/bin/ps", "-A", "-o", "args="], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            ps = ""
        if any(line.startswith(exe) for line in ps.splitlines()):
            return True
    return False
def launch(path):
    args = ["/usr/bin/open", "-n"] + (["--env", "STARHOLM_DATA_DIR=" + data_dir] if data_dir else []) + [path]
    for _ in range(3):
        try:
            subprocess.run(args, timeout=30, **quiet)
        except (OSError, subprocess.SubprocessError):
            pass
        if runs(path):
            return True
    return False
for _ in range(600):
    try:
        os.kill(pid, 0)
    except OSError:
        break
    time.sleep(0.1)
else:
    note(ok=False, error="Starholm was still running after a minute, so the update wasn't installed. It is still ready.")
    sys.exit(1)
time.sleep(1.0)
parent = os.path.dirname(bundle)
old = os.path.join(parent, ".Starholm-old-" + secrets.token_hex(4) + ".app")
staged = new_app
try:
    if os.stat(new_app).st_dev != os.stat(parent).st_dev:
        staged = os.path.join(parent, ".Starholm-new-" + secrets.token_hex(4) + ".app")
        if subprocess.run(["/usr/bin/ditto", new_app, staged], timeout=900, **quiet).returncode != 0:
            raise OSError("ditto couldn't copy the new app beside the old one")
    os.rename(bundle, old)
    try:
        os.rename(staged, bundle)
    except OSError:
        os.rename(old, bundle)
        raise
except (OSError, subprocess.SubprocessError) as exc:
    if staged != new_app:
        shutil.rmtree(staged, ignore_errors=True)
    note(ok=False, error=f"{exc.__class__.__name__}: {exc}"[:300])
    launch(bundle)
    sys.exit(1)
if not launch(bundle):
    failed = os.path.join(parent, ".Starholm-new-" + secrets.token_hex(4) + ".app")
    try:
        os.rename(bundle, failed)
        os.rename(old, bundle)
    except OSError as exc:
        note(ok=False, error=f"The new version didn't start, and the old one couldn't be put back: {exc}"[:300])
        sys.exit(1)
    shutil.rmtree(failed, ignore_errors=True)
    if update_dir:
        shutil.rmtree(update_dir, ignore_errors=True)
    note(ok=False, restored=True, error="The new version didn't start, so the old one was put back.")
    launch(bundle)
    sys.exit(1)
note(ok=True, started=True)
shutil.rmtree(old, ignore_errors=True)
if new_app != staged:
    shutil.rmtree(os.path.dirname(new_app), ignore_errors=True)
if update_dir:
    shutil.rmtree(update_dir, ignore_errors=True)
"""


# Run by Windows PowerShell 5.1 after "Restart now", detached, as -EncodedCommand (no script file to leave behind).
# Its inputs are environment variables, set by Updater.install():
#   STARHOLM_UPD_PID      the app's process, to wait for
#   STARHOLM_UPD_APP      the install folder (the one holding Starholm.exe)
#   STARHOLM_UPD_SETUP    the downloaded, checked installer
#   STARHOLM_UPD_DIR      <data>\updates\<version>: deleted at the end, either way (it also holds helper.log, the
#                         helper's steps, for a test to read while it runs)
#   STARHOLM_UPD_RESULT   RESULT_FILE: the next start reads it and deletes it
#   STARHOLM_UPD_VERSION  the version being installed
#   STARHOLM_UPD_KEY, STARHOLM_UPD_EXE  only for tests: another registry key and program name (defaults below)
# The new version's first start proves it runs by deleting RESULT_FILE, which this helper writes as {"ok": true}
# just before opening it. Moves and deletes are retried for 10 seconds: Windows keeps a file busy for a moment after
# the program using it ends, and antivirus software opens new files.
WIN_INSTALL_HELPER = r"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$oldPid = [int]$env:STARHOLM_UPD_PID
$app = $env:STARHOLM_UPD_APP.TrimEnd('\')
$setup = $env:STARHOLM_UPD_SETUP
$upd = $env:STARHOLM_UPD_DIR
$result = $env:STARHOLM_UPD_RESULT
$version = $env:STARHOLM_UPD_VERSION
$key = if ($env:STARHOLM_UPD_KEY) { $env:STARHOLM_UPD_KEY } else { '__KEY__' }
$exe = if ($env:STARHOLM_UPD_EXE) { $env:STARHOLM_UPD_EXE } else { 'Starholm.exe' }
$wait = __WAIT__
$utf8 = New-Object Text.UTF8Encoding $false
function Say($t) { try { [IO.File]::AppendAllText((Join-Path $upd 'helper.log'), ((Get-Date -Format o) + ' ' + $t + "`r`n"), $utf8) } catch { } }
Say "start: pid $oldPid, app $app, setup $setup"
function Note($r) {
  $r['at'] = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
  try { [IO.File]::WriteAllText($result + '.tmp', (ConvertTo-Json $r -Compress), $utf8)
        Move-Item -LiteralPath ($result + '.tmp') -Destination $result -Force } catch { }
}
function Retry([scriptblock]$b) {
  for ($i = 0; $i -lt 40; $i++) { try { & $b; return $true } catch { Start-Sleep -Milliseconds 250 } }
  return $false
}
function Gone($p) {
  if ($p -and (Test-Path -LiteralPath $p)) {
    Retry { if (Test-Path -LiteralPath $p -PathType Container) { [IO.Directory]::Delete($p, $true) } else { [IO.File]::Delete($p) } } | Out-Null
  }
}
function Inside {
  @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $path = $null; try { $path = $_.Path } catch { }
    $path -and $path.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase) })
}
function Reg($argList) {
  try { (Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\reg.exe') -ArgumentList $argList -Wait -PassThru -WindowStyle Hidden).ExitCode } catch { -1 }
}
function Launch { try { Start-Process -FilePath (Join-Path $app $exe) -WorkingDirectory $app -PassThru } catch { $null } }
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Process -Id $oldPid -ErrorAction SilentlyContinue) -or (Inside).Count) {
  if ((Get-Date) -gt $deadline) {
    Note @{ ok = $false; error = "Starholm was still running after a minute, so the update wasn't installed. It is still ready." }
    exit 1
  }
  Start-Sleep -Milliseconds 200
}
Say 'the app has ended'
Start-Sleep -Milliseconds 500
$old = Join-Path (Split-Path -Parent $app) ('.Starholm-old-' + (-join (1..8 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })))
$regFile = Join-Path $upd 'apps-entry.reg'
$hadKey = (Reg @('export', $key, "`"$regFile`"", '/y')) -eq 0
Say "Apps entry exported: $hadKey"
if (-not (Retry { [IO.Directory]::Move($app, $old) })) {
  Gone $regFile
  Note @{ ok = $false; error = "The old version couldn't be moved aside, so the update wasn't installed. It is still ready." }
  Launch | Out-Null
  exit 1
}
Say "moved aside to $old"
function Restore($why) {
  Say "restore: $why"
  foreach ($p in (Inside)) { try { Stop-Process -Id $p.Id -Force } catch { } }
  Start-Sleep -Milliseconds 500
  Gone $app
  $back = Retry { [IO.Directory]::Move($old, $app) }
  if ($hadKey) { Reg @('import', "`"$regFile`"") | Out-Null } else { Reg @('delete', $key, '/f') | Out-Null }
  Gone $upd
  if ($back) { Note @{ ok = $false; restored = $true; error = $why }; Launch | Out-Null }
  else { Note @{ ok = $false; restored = $false; error = "$why The old version couldn't be put back: install Starholm again from starholm.app. Your chats, notes and library are kept." } }
  exit 1
}
$code = -1
try {
  $code = (Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL', '/SP-', "/DIR=`"$app`"")).ExitCode
} catch { $code = -1 }
Say "installer exited with $code"
if ($code -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $app $exe))) {
  Restore "The installer stopped (code $code), so the old version was put back."
}
Note @{ ok = $true; version = $version }
$p = Launch
Say "opened the new version: $(if ($p) { $p.Id } else { 'failed' })"
$started = $false
$deadline = (Get-Date).AddSeconds($wait)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 250
  if (-not (Test-Path -LiteralPath $result)) { $started = $true; break }
  if (-not $p -or $p.HasExited) { Start-Sleep -Milliseconds 500; $started = -not (Test-Path -LiteralPath $result); break }
}
Say "started: $started"
if (-not $started) {
  Gone $result
  Restore "The new version didn't start, so the old one was put back."
}
Gone $old
Gone $upd
exit 0
""".replace("__KEY__", WIN_UNINSTALL_KEY).replace("__WAIT__", str(WIN_START_WAIT))


def win_helper_command(script=None):
    """The Windows helper's command line: PowerShell with the script encoded (UTF-16LE, base64), so no file is
    written for it and nothing on the command line needs quoting."""
    encoded = base64.b64encode((script or WIN_INSTALL_HELPER).encode("utf-16-le")).decode("ascii")
    return [powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


# ---------- the updater ----------

class Updater:
    """The app's update state in one server: the last check, a download running in a thread, and an update that is
    ready. Kept in STATE_FILE so a downloaded, checked update survives a quit; nothing runs on its own unless
    automatic updates are on (start_auto())."""

    def __init__(self, app_dir, data_dir, bundle=None, platform=None, base=None, public_key_base64=None):
        self.app_dir, self.data_dir = app_dir, data_dir
        self.bundle = bundle
        self.platform = platform or sys.platform
        self.base = base
        self.public_key = public_key_base64
        self.version = current_version(bundle)
        self.lock = threading.Lock()
        self.thread = None
        self.stopping = threading.Event()
        self.auto_thread = None
        self.auto_stop = threading.Event()
        self.phase = "idle"  # idle, checking, downloading, unpacking, ready, error
        self.progress = {"done": 0, "bytes": 0}
        self.error = None
        self.dismissed = None  # the version whose notice "Later" put away, until the next start
        self.state = self._load()

    # -- the saved state --
    def _path(self):
        return os.path.join(self.app_dir, STATE_FILE)

    def _load(self):
        try:
            with open(self._path(), "rb") as f:
                data = json.loads(f.read(200_000).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            data = None
        return data if isinstance(data, dict) else {}

    def _save(self):
        os.makedirs(self.app_dir, mode=0o700, exist_ok=True)
        tmp = f"{self._path()}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self._path())

    def updates_dir(self):
        return os.path.join(self.data_dir, "updates")

    # -- at start --
    def at_start(self):
        """Tidy up after an update: an update that is no newer than this version (it is installed now, or an older
        one) is forgotten and its files deleted; a ready update whose app is gone is forgotten. Returns the last
        restart's result ({"ok", "error", "version"}) once, for the page, or None."""
        result = None
        try:
            with open(os.path.join(self.app_dir, RESULT_FILE), encoding="utf-8-sig") as f:
                result = json.load(f)
        except (OSError, ValueError):
            result = None
        try:  # deleting it is what tells the Windows helper this version started
            os.remove(os.path.join(self.app_dir, RESULT_FILE))
        except OSError:
            pass
        installing = self.state.pop("installing", None)
        if isinstance(result, dict) and result.get("restored") and isinstance(installing, dict):
            self.state["failed"] = {"version": installing.get("version"), "sha256": installing.get("sha256")}
        ready = self.state.get("ready")
        if ready and (not is_newer(ready.get("version"), self.version) or not self._ready_files(ready)):
            self.state.pop("ready", None)
        latest = self.state.get("latest")
        if latest and not is_newer(latest.get("version"), self.version):
            self.state.pop("latest", None)
        keep = {str(self.state.get("ready", {}).get("version") or "")}
        try:
            for name in os.listdir(self.updates_dir()):
                if name not in keep:
                    shutil.rmtree(os.path.join(self.updates_dir(), name), ignore_errors=True)
        except OSError:
            pass
        tidy_beside(self.bundle, self.platform)
        try:
            self._save()
        except OSError:
            pass
        if self.state.get("ready"):
            self.phase = "ready"
        if isinstance(result, dict):
            result = {"ok": bool(result.get("ok")), "error": str(result.get("error") or "")[:300] or None,
                      "version": self.version}
            self.state["last_restart"] = result
            return result
        return None

    # -- the page's view --
    def snapshot(self, auto=False):
        with self.lock:
            ready = self.state.get("ready")
            latest = self.state.get("latest")
            out = {"version": self.version, "platform": platform_dir(self.platform), "auto": bool(auto),
                   "phase": self.phase, "checked": self.state.get("checked"),
                   "latest": {k: latest.get(k) for k in ("version", "notes", "size", "released")} if latest else None,
                   "done": self.progress["done"], "bytes": self.progress["bytes"], "error": self.error,
                   "ready": None, "last_restart": self.state.get("last_restart")}
        if ready and is_newer(ready.get("version"), self.version):
            out["ready"] = {"version": ready["version"], "notes": ready.get("notes") or "",
                            "dismissed": self.dismissed == ready["version"]}
            ok, why = install_target(self.bundle, self.platform)
            out["ready"]["install"] = "replace" if ok else "manual"
            out["ready"]["why"] = why
        return out

    def _ready_files(self, ready):
        """Whether a ready update's download is still on disk: the unpacked app (Mac) or the installer (Windows)."""
        if self.platform == "win32":
            return check_installer(ready.get("installer"), ready.get("size")) is None
        return os.path.isdir(str(ready.get("app") or ""))

    def notice(self):
        """The small part /api/status carries, so the page can show the ready notice: None, or {"version", "notes",
        "dismissed"}."""
        with self.lock:
            ready = self.state.get("ready")
        if not ready or not is_newer(ready.get("version"), self.version):
            return None
        return {"version": ready["version"], "notes": ready.get("notes") or "",
                "dismissed": self.dismissed == ready["version"]}

    def later(self):
        ready = self.state.get("ready")
        self.dismissed = ready.get("version") if ready else None

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    # -- check and download --
    def check(self, download=True, auto=False):
        """Read latest.json and check it (verify()). A newer version starts its download in a thread, unless download
        is False. auto: the automatic schedule, which skips a version whose last install didn't start (state
        "failed"); a check the person asked for forgets that and offers it again. Returns the manifest checked, or
        raises UpdateError, DownloadError or FetchError."""
        if self.running():
            return self.state.get("latest")
        if not auto:
            self.state.pop("failed", None)
        with self.lock:
            self.phase, self.error = "checking", None
        try:
            raw = fetch_manifest(self.platform, self.base)
            m = verify(raw, self.version, self.platform, self.public_key, self.base)
        except (UpdateError, DownloadError, FetchError) as exc:
            with self.lock:
                self.phase = "ready" if self.state.get("ready") else "error"
                self.error = self._message(exc)
                self.state["checked"] = time.time()
            self._save_quietly()
            raise
        with self.lock:
            self.state["checked"] = time.time()
            if m["newer"]:
                self.state["latest"] = {k: m[k] for k in ("version", "url", "size", "sha256", "notes", "released")}
            else:
                self.state.pop("latest", None)
            ready = self.state.get("ready")
            have_it = bool(ready and ready.get("version") == m["version"] and ready.get("sha256") == m["sha256"])
            failed = self.state.get("failed") or {}
            skip = auto and failed.get("version") == m["version"] and failed.get("sha256") == m["sha256"]
            self.phase = "ready" if ready and is_newer(ready.get("version"), self.version) else "idle"
        if m["newer"] and not have_it and not skip and download:
            self.start_download(m)
        self._save_quietly()
        return m

    def _save_quietly(self):
        try:
            with self.lock:
                self._save()
        except OSError:
            pass

    @staticmethod
    def _message(exc):
        if isinstance(exc, UpdateError):
            return str(exc)
        return "Starholm couldn't reach the update server. Check the internet connection and try again."

    def start_download(self, m):
        with self.lock:
            if self.running():
                return
            self.phase, self.error = "downloading", None
            self.progress = {"done": 0, "bytes": m["size"]}
            self.stopping.clear()
            self.thread = threading.Thread(target=self._download, args=(m,), daemon=True, name="app-update")
            self.thread.start()

    def _download(self, m):
        win = self.platform == "win32"
        folder = os.path.join(self.updates_dir(), m["version"])
        path = os.path.join(folder, f"Starholm-Setup-{m['version']}.exe" if win else f"Starholm-{m['version']}.zip")
        host = (urllib.parse.urlsplit(m["url"]).hostname or "").lower()

        def progress(done, total):
            self.progress = {"done": done, "bytes": m["size"]}
        try:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            downloader.fetch(m["url"], path, frozenset({host}), size=m["size"], sha256=m["sha256"],
                             progress=progress, stop=self.stopping.is_set, follow=False,
                             same_file=f"app/{m['version']}/{m['sha256']}")
            with self.lock:
                self.phase = "unpacking"
            if safe_fetch.sha256_file(path) != m["sha256"]:  # once more, from the disk
                raise UpdateError("download", "The update didn't match its checksum, so it was deleted.")
            ready = {"version": m["version"], "notes": m["notes"], "sha256": m["sha256"], "size": m["size"],
                     "downloaded": time.time()}
            if win:
                why = check_installer(path, m["size"])
                ready["installer"] = path
            else:
                app = unpack(path, os.path.join(folder, "app"))
                why = check_app(app, m["version"])
                ready["app"] = app
            if why:
                raise UpdateError("bad_app", f"The update was refused: {why}.")
            if not win:
                os.remove(path)
            with self.lock:
                self.state["ready"] = ready
                self.phase = "ready"
                self.dismissed = None
                self._save()
            self._drop_other_downloads(m["version"])
        except Stopped:
            with self.lock:
                self.phase = "idle"
        except (UpdateError, DownloadError, FetchError, OSError) as exc:
            if isinstance(exc, (UpdateError, downloader.ChecksumError)):
                shutil.rmtree(folder, ignore_errors=True)  # a wrong file is never kept
            with self.lock:
                self.phase = "error"
                self.error = str(exc) if isinstance(exc, UpdateError) else \
                    "The update couldn't be downloaded. Check the internet connection and try again."
                if isinstance(exc, downloader.ChecksumError):
                    self.error = "The update didn't match its checksum, so it was deleted."

    def _drop_other_downloads(self, keep):
        """A newer ready version replaces an older one: delete every other folder in <data>/updates/."""
        try:
            names = os.listdir(self.updates_dir())
        except OSError:
            return
        for name in names:
            if name != keep:
                shutil.rmtree(os.path.join(self.updates_dir(), name), ignore_errors=True)

    def stop(self, seconds=10):
        self.stopping.set()
        if self.thread:
            self.thread.join(seconds)

    # -- automatic updates --
    def start_auto(self, enabled):
        """With enabled() True: check at launch (after AUTO_FIRST_WAIT seconds), then whenever AUTO_EVERY seconds have
        passed since the last check, and download a newer version in the background; the page's status poll then
        opens the ready notice. Stops when enabled() turns False."""
        if self.auto_thread and self.auto_thread.is_alive() and not self.auto_stop.is_set():
            return  # already on
        # Each schedule has its own stop: one turned off during a check ends after it, while a new one (the switch
        # turned on again) runs.
        self.auto_stop = stop = threading.Event()

        def loop():
            first = True
            while not stop.wait(AUTO_FIRST_WAIT if first else AUTO_TICK):
                if not enabled():
                    return
                last = self.state.get("checked") or 0
                if first or not 0 <= time.time() - last < AUTO_EVERY:  # a clock set back counts as due
                    try:
                        self.check(auto=True)
                    except (UpdateError, DownloadError, FetchError, OSError):
                        pass  # shown on the Status page; the next hour tries again
                first = False
        self.auto_thread = threading.Thread(target=loop, daemon=True, name="app-update-auto")
        self.auto_thread.start()

    def stop_auto(self):
        self.auto_stop.set()

    # -- the restart --
    def install(self, spawn=subprocess.Popen, executable=None):
        """Start the install helper for the ready update: INSTALL_HELPER (Mac) or WIN_INSTALL_HELPER (Windows). The
        caller quits right after. Raises UpdateError when no verified update is ready or the app's folder can't be
        written (code "read_only", "translocated" or "source": the caller then shows the manual way)."""
        ready = self.state.get("ready")
        if not ready or not is_newer(ready.get("version"), self.version):
            raise UpdateError("not_newer", "No newer version of Starholm is ready to install.")
        win = self.platform == "win32"
        if win:
            why = check_installer(ready.get("installer"), ready.get("size"))
        else:
            app = ready.get("app")
            why = check_app(app, ready["version"]) if app and os.path.isdir(app) else "it is no longer on this computer"
        if why:
            with self.lock:
                self.state.pop("ready", None)
                self.phase = "idle"
            self._save_quietly()
            raise UpdateError("bad_app", f"The update can't be installed: {why}. Check for updates again.")
        ok, reason = install_target(self.bundle, self.platform)
        if not ok:
            raise UpdateError(reason, "manual")
        with self.lock:  # what at_start() remembers as "failed" when the helper puts the old version back
            self.state["installing"] = {"version": ready["version"], "sha256": ready.get("sha256")}
        self._save_quietly()
        result_file = os.path.join(self.app_dir, RESULT_FILE)
        if win:
            env = {**os.environ, "STARHOLM_UPD_PID": str(os.getpid()),
                   "STARHOLM_UPD_APP": os.path.dirname(os.path.realpath(self.bundle)),
                   "STARHOLM_UPD_SETUP": ready["installer"], "STARHOLM_UPD_DIR": os.path.dirname(ready["installer"]),
                   "STARHOLM_UPD_RESULT": result_file, "STARHOLM_UPD_VERSION": ready["version"]}
            for name in ("STARHOLM_UPD_KEY", "STARHOLM_UPD_EXE"):  # test-only inputs never come from the app
                env.pop(name, None)
            import ntpath  # the launcher's PATH holds the app's own folders: put Windows' own first
            system = ntpath.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32")
            env["PATH"] = ";".join([system, ntpath.join(system, "WindowsPowerShell", "v1.0"), env.get("PATH", "")])
            spawn(win_helper_command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, cwd=os.path.expanduser("~"), env=env, creationflags=WIN_SPAWN_FLAGS,
                  close_fds=True)
            return {"version": ready["version"]}
        exe = executable or sys.executable
        data = os.environ.get("STARHOLM_DATA_DIR", "")
        spawn([exe, "-I", "-c", INSTALL_HELPER, str(os.getpid()), os.path.realpath(self.bundle), app, result_file,
               data if data.startswith("/") else "", os.path.join(self.updates_dir(), ready["version"])],
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=os.path.expanduser("~"), env={"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~")},
              start_new_session=True, close_fds=True)
        return {"version": ready["version"]}
