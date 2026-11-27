"""Starholm's own updates (upgrade/appupdate.py and the updates part of app/server.py), against a stand-in for
download.starholm.app on a local socket.

Checked:
- the signed manifest: a good one is read; a bad signature, a changed manifest, a signature made without the domain
  prefix (as a pack.sig is), another platform, an address off download.starholm.app or not https, a size or SHA-256
  that isn't valid, are all refused; an older or equal version is never "newer"; version order (1.0.10 after 1.0.9);
- domain separation both ways: an app manifest's signature never passes as a pack's, and a pack's never as an app
  manifest's;
- the download (Mac): a newer version is downloaded, its SHA-256 checked, unpacked, its bundle id, version and code
  signature checked, and offered as ready; a wrong SHA-256, an app with another version inside, and an older version
  are refused and nothing is kept;
- the helper also deletes the update's folder (no zip, no unpacked copy) after a good restart, and after a new
  version that doesn't start, and never a folder that doesn't hold the new app;
- the restart: install() starts the helper with a fixed argument list and no shell, and refuses a folder it can't
  write (the manual way); the helper itself, run here with its process calls stood in for, swaps the two bundles,
  opens the new one and deletes the old one, and puts the old one back when the swap fails;
- the automatic schedule: with the switch on it checks at launch and again on the timer, reading only latest.json;
  off, it checks nothing; a newer version it finds downloads in the background and the status carries the notice; a
  version whose install didn't start isn't downloaded again by the schedule, but Check for updates offers it;
- Windows: the installer downloads from win/, checked by SHA-256 and its MZ header, and Restart now starts Windows
  PowerShell with the helper encoded; on Windows itself the helper runs with a stand-in installer: it installs into
  the same folder and leaves no old folder, installer or update folder; when the new version doesn't start or the
  installer fails, the old folder and its Apps entry (a test registry key) are put back and the download deleted;
- the server: Check for updates goes online only when pressed; with "Download updates automatically" off (the
  default) a start contacts nothing and starts no schedule, and on, a start checks by itself; the switch is saved;
  Restart now with nothing ready is refused; without a writable app folder it shows the manual way.

A key made here signs everything; no real key is involved. No test can reach a real host: fake_hosts.guard().

Usage: .venv/bin/python tests/app_update_test.py"""
import base64
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="app-update-")
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"), SURVIVAL_LLM_DATA=os.path.join(TMP, "data"))
os.environ.pop("STARHOLM_DATA_DIR", None)
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
sandbox.require()
import fake_hosts  # noqa: E402
sys.path.insert(0, ROOT)
fake_hosts.guard()  # before anything can open a connection
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from library import downloader  # noqa: E402
from upgrade import appupdate, packs  # noqa: E402

FAILS = []
MAC = sys.platform == "darwin"


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        FAILS.append(name)


def raises(fn, code=None):
    try:
        fn()
    except appupdate.UpdateError as exc:
        return code is None or exc.code == code
    return False


# ---------- a signing key made for this test ----------
KEY = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"app_update_test signing key").digest())
PUB = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw,
                                                      serialization.PublicFormat.Raw)).decode()
OTHER = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"someone else's key").digest())


def manifest(version="1.0.3", platform="mac", body=None, size=2_000_000, sha="a" * 64, url=None, key=KEY,
             prefix=appupdate.DOMAIN, notes="Library downloads are faster."):
    """latest.json's bytes, signed with key over prefix + the manifest text."""
    m = body if body is not None else {
        "format": 1, "app": "starholm", "platform": platform, "version": version,
        "url": url or f"https://download.starholm.app/{platform}/Starholm-{version}.zip", "size": size, "sha256": sha,
        "notes": notes, "released": "2026-11-26"}
    text = json.dumps(m, indent=1)
    sig = base64.b64encode(key.sign(prefix + text.encode())).decode()
    return json.dumps({"manifest": text, "signature": sig}).encode()


def verify(raw, current="1.0.2", platform="darwin"):
    return appupdate.verify(raw, current, platform, PUB)


# ---------- 1. the manifest ----------
m = verify(manifest())
check("a signed manifest for a newer version is read", m["version"] == "1.0.3" and m["newer"] and m["size"] == 2_000_000
      and m["notes"] == "Library downloads are faster.", m)
check("a bad signature (another key) is refused", raises(lambda: verify(manifest(key=OTHER)), "bad_signature"))
good = json.loads(manifest())
changed = json.dumps({"manifest": good["manifest"].replace("1.0.3", "1.0.4"), "signature": good["signature"]}).encode()
check("a manifest changed after signing is refused", raises(lambda: verify(changed), "bad_signature"))
check("a signature made without the domain prefix (as a pack.sig is) is refused",
      raises(lambda: verify(manifest(prefix=b"")), "bad_signature"))
check("a manifest for Windows is refused on the Mac", raises(lambda: verify(manifest(platform="win")), "platform"))
check("the Windows app reads the Windows manifest", verify(manifest(platform="win"), platform="win32")["newer"])
check("a download address on another host is refused",
      raises(lambda: verify(manifest(url="https://evil.example/mac/Starholm-1.0.3.zip")), "invalid"))
check("a plain http address is refused",
      raises(lambda: verify(manifest(url="http://download.starholm.app/mac/Starholm-1.0.3.zip")), "invalid"))
check("an address outside /mac/ is refused",
      raises(lambda: verify(manifest(url="https://download.starholm.app/win/x.zip")), "invalid"))
check("a size out of bounds is refused", raises(lambda: verify(manifest(size=10)), "invalid"))
check("a SHA-256 that isn't 64 hex characters is refused", raises(lambda: verify(manifest(sha="xyz")), "invalid"))
check("a version that isn't numbers is refused", raises(lambda: verify(manifest(version="1.0.3-beta")), "invalid"))
check("an older version is never newer", verify(manifest(version="1.0.1"))["newer"] is False)
check("the same version is not newer", verify(manifest(version="1.0.2"))["newer"] is False)
check("not JSON is refused", raises(lambda: verify(b"<html>"), "invalid"))
check("an oversized file is refused", raises(lambda: verify(b" " * (appupdate.MAX_MANIFEST + 1)), "invalid"))
check("version order: 1.0.10 is after 1.0.9, 1.1 after 1.0.9, 1.0 equals 1.0.0",
      appupdate.is_newer("1.0.10", "1.0.9") and appupdate.is_newer("1.1", "1.0.9")
      and not appupdate.is_newer("1.0", "1.0.0") and not appupdate.is_newer("1.0.0", "1.0"))
check("notes are one line, without control characters, and short",
      verify(manifest(notes="a\nb\x07" + "c" * 900))["notes"] == "a b " + "c" * (appupdate.MAX_NOTES - 4))

# ---------- 2. domain separation ----------
pack_json = json.dumps({"format": 1, "name": "guides", "version": "2026-11-25", "size": 0, "files": []}).encode()
pack_sig = base64.b64encode(KEY.sign(pack_json))
check("a pack's signature (pack.json signed alone) passes as a pack", packs.signature_ok(pack_json, pack_sig, PUB))
as_app = json.dumps({"manifest": pack_json.decode(), "signature": pack_sig.decode()}).encode()
check("a pack's signature never passes as an app manifest", raises(lambda: verify(as_app), "bad_signature"))
outer = json.loads(manifest())
app_bytes = appupdate.sign_message(outer["manifest"].encode())
check("an app manifest's signature never passes as a pack's signature over the manifest text",
      not packs.signature_ok(outer["manifest"].encode(), outer["signature"].encode(), PUB))

def _pack_refused(raw, sig):
    real = packs.PACK_PUBLIC_KEY_BASE64
    packs.PACK_PUBLIC_KEY_BASE64 = PUB
    try:
        packs.check_manifest(raw, sig.encode(), "guides", [])
    except packs.PackError:
        return True
    finally:
        packs.PACK_PUBLIC_KEY_BASE64 = real
    return False


check("an app manifest's signed bytes are refused by the pack check, signature and all",
      _pack_refused(app_bytes, outer["signature"]))


# ---------- 3. the download, unpacking and checks (Mac) ----------
def make_app(folder, version, bundle_id="app.starholm.mac"):
    """A tiny Starholm.app: Info.plist, a shell-script executable, 1.2 MB of filler, signed ad hoc."""
    app = os.path.join(folder, "Starholm.app")
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    os.makedirs(os.path.join(app, "Contents", "Resources"))
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleExecutable": "Starholm", "CFBundleName": "Starholm",
                       "CFBundleShortVersionString": version, "CFBundleVersion": version,
                       "CFBundlePackageType": "APPL"}, f)
    exe = os.path.join(app, "Contents", "MacOS", "Starholm")
    with open(exe, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(exe, 0o755)
    with open(os.path.join(app, "Contents", "Resources", "filler.bin"), "wb") as f:
        f.write(os.urandom(1_200_000))
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", app], check=True, capture_output=True)
    return app


def zip_of(app, out):
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", app, out], check=True, capture_output=True)
    with open(out, "rb") as f:
        return f.read()


hosts = fake_hosts.FakeHosts()
downloader.CONNECT = hosts.connect
BASE = "https://download.starholm.app"


def serve(version, zip_bytes, sha=None, size=None):
    hosts.add(f"{BASE}/mac/latest.json", manifest(version=version, size=size or len(zip_bytes),
                                                  sha=sha or hashlib.sha256(zip_bytes).hexdigest()), step=65536)
    hosts.add(f"{BASE}/mac/Starholm-{version}.zip", zip_bytes, step=65536)


def wait_phase(u, phases, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        if u.phase in phases and not u.running():
            return True
        time.sleep(0.05)
    return False


if MAC:
    build = os.path.join(TMP, "build")
    os.makedirs(build)
    z103 = zip_of(make_app(os.path.join(build, "v103"), "1.0.3"), os.path.join(build, "103.zip"))
    APP_DIR, DATA = os.path.join(TMP, "u-app"), os.path.join(TMP, "u-data")
    installed_at = os.path.join(TMP, "Applications")
    current = make_app(installed_at, "1.0.2")

    # a wrong SHA-256: the download is refused and deleted
    serve("1.0.3", z103, sha="0" * 64)
    u = appupdate.Updater(APP_DIR, DATA, bundle=current, platform="darwin", public_key_base64=PUB)
    check("the running version comes from the app's Info.plist", u.version == "1.0.2", u.version)
    u.check()
    check("a wrong SHA-256: refused, with a sentence", wait_phase(u, ("error",)) and "checksum" in (u.error or ""), u.error)
    check("a wrong SHA-256: nothing is kept", not os.path.exists(os.path.join(DATA, "updates", "1.0.3")))
    check("a wrong SHA-256: nothing is offered", u.snapshot()["ready"] is None)

    # an app with another version inside its zip
    z_lie = zip_of(make_app(os.path.join(build, "lie"), "1.0.9"), os.path.join(build, "lie.zip"))
    serve("1.0.3", z_lie)
    u.check()
    check("an app whose Info.plist says another version: refused",
          wait_phase(u, ("error",)) and "version" in (u.error or ""), u.error)
    check("...and deleted", not os.path.exists(os.path.join(DATA, "updates", "1.0.3")))

    # another app's bundle id
    z_other = zip_of(make_app(os.path.join(build, "other"), "1.0.3", "com.example.other"), os.path.join(build, "o.zip"))
    serve("1.0.3", z_other)
    u.check()
    check("an app with another bundle id: refused", wait_phase(u, ("error",)) and "isn't Starholm" in (u.error or ""),
          u.error)

    # a broken code signature: a file changed after signing
    broken_dir = os.path.join(build, "broken")
    broken = make_app(broken_dir, "1.0.3")
    with open(os.path.join(broken, "Contents", "Resources", "filler.bin"), "ab") as f:
        f.write(b"changed")
    z_broken = zip_of(broken, os.path.join(build, "b.zip"))
    serve("1.0.3", z_broken)
    u.check()
    check("an app whose code signature doesn't check out: refused",
          wait_phase(u, ("error",)) and "signature" in (u.error or ""), u.error)

    # an older version: nothing is downloaded
    hosts.log.clear()
    serve("1.0.1", z103)
    m = u.check()
    check("an older version: the check says not newer and downloads nothing",
          not m["newer"] and not u.running() and not any(p.endswith(".zip") for _, p, _ in hosts.log), hosts.log)
    check("an older version is never offered", u.snapshot()["ready"] is None and u.snapshot()["latest"] is None)

    # a bad signature: refused before anything is downloaded
    hosts.log.clear()
    hosts.add(f"{BASE}/mac/latest.json", manifest(key=OTHER, size=len(z103), sha=hashlib.sha256(z103).hexdigest()))
    try:
        u.check()
        bad = False
    except appupdate.UpdateError as exc:
        bad = exc.code == "bad_signature"
    check("a bad signature: refused, and the zip is never asked for",
          bad and not any(p.endswith(".zip") for _, p, _ in hosts.log), hosts.log)

    # the good one
    serve("1.0.3", z103)
    u.check()
    check("a newer, signed version downloads, checks out and is ready", wait_phase(u, ("ready",)), (u.phase, u.error))
    snap = u.snapshot()
    check("the snapshot offers 1.0.3 with its notes and the replace-in-place restart",
          snap["ready"] and snap["ready"]["version"] == "1.0.3" and snap["ready"]["install"] == "replace"
          and snap["ready"]["notes"] == "Library downloads are faster.", snap)
    ready_app = u.state["ready"]["app"]
    check("the unpacked app is kept, the zip deleted", os.path.isdir(ready_app)
          and not os.path.exists(os.path.join(DATA, "updates", "1.0.3", "Starholm-1.0.3.zip")))
    check("the ready update is saved for the next start",
          json.load(open(os.path.join(APP_DIR, appupdate.STATE_FILE)))["ready"]["version"] == "1.0.3")
    check("its downloaded app carries no quarantine attribute",
          b"com.apple.quarantine" not in subprocess.run(["/usr/bin/xattr", ready_app], capture_output=True).stdout)
    check("the status notice names it", u.notice() == {"version": "1.0.3", "notes": "Library downloads are faster.",
                                                         "dismissed": False}, u.notice())
    u.later()
    check("Later puts the notice away until the next start", u.notice()["dismissed"] is True)
    u2 = appupdate.Updater(APP_DIR, DATA, bundle=current, platform="darwin", public_key_base64=PUB)
    u2.at_start()
    check("after a quit, the ready update is offered again", u2.notice() and not u2.notice()["dismissed"])

    # install(): the helper's arguments
    spawned = []
    u2.install(spawn=lambda args, **kw: spawned.append((args, kw)), executable="/x/python")
    args, kw = spawned[0]
    check("install() starts the helper with a fixed argument list, isolated Python, no shell",
          args[:3] == ["/x/python", "-I", "-c"] and args[3] == appupdate.INSTALL_HELPER
          and args[5] == os.path.realpath(current) and args[6] == ready_app and "shell" not in kw
          and args[9] == os.path.join(DATA, "updates", "1.0.3") and len(args) == 10
          and kw.get("start_new_session") is True and set(kw["env"]) == {"PATH", "HOME"}, args)
    check("install() remembers what it is installing, for a restore",
          u2.state.get("installing", {}).get("version") == "1.0.3")

    # a folder that can't be written: the manual way
    os.chmod(installed_at, 0o555)
    try:
        ok, why = appupdate.install_target(current)
        check("a read-only app folder can't be replaced in place", not ok and why == "read_only", why)
        check("install() refuses it with the manual way",
              raises(lambda: u2.install(spawn=lambda *a, **k: None), "read_only"))
        check("the snapshot says so", u2.snapshot()["ready"]["install"] == "manual")
    finally:
        os.chmod(installed_at, 0o755)
    check("an app under App Translocation can't be replaced",
          appupdate.install_target("/private/var/folders/x/AppTranslocation/y/d/Starholm.app") == (False, "source")
          or not appupdate.install_target("/private/var/folders/x/AppTranslocation/y/d/Starholm.app")[0])

    # ---------- 4. the helper, run here with its process calls stood in for ----------
    def run_helper(bundle, new_app, fail_second_rename=False, data_dir="", old_running=False, never_starts=False,
                   update_dir=""):
        import subprocess as sp
        did = []
        real = (os.kill, time.sleep, sp.run, os.rename)
        result_file = os.path.join(TMP, "result.json")

        def kill(pid, sig):
            if not old_running:
                raise OSError("gone")

        def run(argv, **kw):
            did.append(list(argv))
            if argv[0] == "/bin/ps":
                exe = os.path.join(bundle, "Contents", "MacOS", "Starholm")
                return sp.CompletedProcess(argv, 0, stdout="" if never_starts else f"{exe}\n", stderr="")
            if argv[0] == "/usr/bin/ditto":
                return real[2](argv, **kw)
            return sp.CompletedProcess(argv, 0)
        renames = [0]

        def rename(a, b):
            renames[0] += 1
            if fail_second_rename and renames[0] == 2:
                raise PermissionError(1, "Operation not permitted")
            return real[3](a, b)
        os.kill, time.sleep, sp.run, os.rename = kill, (lambda s: None), run, rename
        argv = sys.argv
        sys.argv = ["helper", "999999", bundle, new_app, result_file, data_dir, update_dir]
        try:
            exec(compile(appupdate.INSTALL_HELPER, "helper", "exec"), {"__name__": "__main__"})
        except SystemExit:
            pass
        finally:
            os.kill, time.sleep, sp.run, os.rename = real
            sys.argv = argv
        with open(result_file) as f:
            return did, json.load(f)

    update_dir = os.path.join(DATA, "updates", "1.0.3")
    did, result = run_helper(os.path.realpath(current), ready_app, data_dir="/tmp/test-data", update_dir=update_dir)
    opens = [a for a in did if a[0] == "/usr/bin/open"]
    check("the helper put the new version in the old one's place", appupdate.bundle_version(current) == "1.0.3",
          appupdate.bundle_version(current))
    check("the helper opened the app with -n and kept the test's data folder",
          opens and opens[0] == ["/usr/bin/open", "-n", "--env", "STARHOLM_DATA_DIR=/tmp/test-data",
                                 os.path.realpath(current)], opens)
    check("the helper deleted the old app and left nothing beside it",
          sorted(os.listdir(installed_at)) == ["Starholm.app"], os.listdir(installed_at))
    check("the helper wrote its result", result.get("ok") is True and result.get("started") is True, result)
    check("the helper deleted the update's folder: no download, no unpacked copy is left",
          not os.path.exists(update_dir) and os.listdir(os.path.join(DATA, "updates")) == [],
          os.listdir(os.path.join(DATA, "updates")))
    check("the new app's code signature still checks out after the move",
          subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", current]).returncode == 0)

    # the swap fails: the old app goes back and is opened
    newer = make_app(os.path.join(build, "v104"), "1.0.4")
    did, result = run_helper(os.path.realpath(current), newer, fail_second_rename=True)
    check("a failed swap puts the old app back", appupdate.bundle_version(current) == "1.0.3"
          and sorted(os.listdir(installed_at)) == ["Starholm.app"], os.listdir(installed_at))
    check("...says why in its result, and opens the old app",
          result.get("ok") is False and "not permitted" in result.get("error", "")
          and any(a[0] == "/usr/bin/open" for a in did), result)

    # the new version doesn't start: it is removed, the old one put back and opened
    v105 = os.path.join(DATA, "updates", "1.0.5")
    newer = make_app(os.path.join(v105, "app"), "1.0.5")
    with open(os.path.join(v105, "Starholm-1.0.5.zip"), "wb") as f:
        f.write(b"left over")
    did, result = run_helper(os.path.realpath(current), newer, never_starts=True, update_dir=v105)
    check("a new version that doesn't start: the old one is put back", appupdate.bundle_version(current) == "1.0.3"
          and sorted(os.listdir(installed_at)) == ["Starholm.app"], os.listdir(installed_at))
    check("...the result says so, and the old one is opened again",
          result.get("ok") is False and "didn't start" in result.get("error", "")
          and result.get("restored") is True
          and [a[-1] for a in did if a[0] == "/usr/bin/open"][-1] == os.path.realpath(current), (result, did))
    check("...the failed download is deleted and nothing is left beside the app",
          not os.path.exists(v105) and sorted(os.listdir(installed_at)) == ["Starholm.app"], os.listdir(installed_at))
    # an update_dir that doesn't hold new_app is never deleted
    stray = os.path.join(TMP, "not-an-update")
    os.makedirs(stray)
    newer = make_app(os.path.join(build, "v105b"), "1.0.5")
    run_helper(os.path.realpath(current), newer, never_starts=True, update_dir=stray)
    check("...and a folder that doesn't hold the new app is never deleted", os.path.isdir(stray))
    check("...its code signature still checks out", subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", current]).returncode == 0)

    # the old app still running after a minute: nothing is touched, the update stays ready
    newer = make_app(os.path.join(build, "v106"), "1.0.6")
    did, result = run_helper(os.path.realpath(current), newer, old_running=True)
    check("the old app still running: nothing is swapped or opened", appupdate.bundle_version(current) == "1.0.3"
          and os.path.isdir(newer) and not any(a[0] == "/usr/bin/open" for a in did), did)
    check("...and the result says the update is still ready",
          result.get("ok") is False and "still running" in result.get("error", ""), result)

    # the next start after the update: the files are tidied, the result shown once
    with open(os.path.join(APP_DIR, appupdate.RESULT_FILE), "w") as f:
        json.dump({"ok": True, "started": True}, f)
    u3 = appupdate.Updater(APP_DIR, DATA, bundle=current, platform="darwin", public_key_base64=PUB)
    last = u3.at_start()
    check("on the new version, the last restart's result is read once", last == {"ok": True, "error": None,
                                                                                 "version": "1.0.3"}, last)
    check("on the new version, the update's files are deleted and nothing is offered",
          not os.listdir(os.path.join(DATA, "updates")) and u3.notice() is None, os.listdir(os.path.join(DATA, "updates")))
else:
    print("PASS (skipped: the download, unpacking and restart checks run on a Mac)")


# ---------- 4b. the automatic schedule: at launch, then on the timer; nothing when off ----------
def manifest_reads(platform="mac"):
    return sum(1 for _, p, _ in hosts.log if p == f"/{platform}/latest.json")


AUTO_APP, AUTO_DATA = os.path.join(TMP, "a-app"), os.path.join(TMP, "a-data")
saved = (appupdate.AUTO_FIRST_WAIT, appupdate.AUTO_TICK, appupdate.AUTO_EVERY)
appupdate.AUTO_FIRST_WAIT, appupdate.AUTO_TICK, appupdate.AUTO_EVERY = 0.2, 0.1, 0.5
hosts.add(f"{BASE}/mac/latest.json", manifest(version="1.0.1", size=2_000_000))  # older: nothing to download
a = appupdate.Updater(AUTO_APP, AUTO_DATA, bundle=None, platform="darwin", public_key_base64=PUB)
switch = {"on": False}
hosts.log.clear()
a.start_auto(lambda: switch["on"])
time.sleep(0.6)
check("with the switch off, the schedule checks nothing and ends", manifest_reads() == 0
      and not a.auto_thread.is_alive(), hosts.log)
switch["on"] = True
a.start_auto(lambda: switch["on"])
time.sleep(0.45)
first = manifest_reads()
check("with the switch on, it checks at launch (after AUTO_FIRST_WAIT)", first == 1, hosts.log)
time.sleep(1.3)
check("...and again on the timer while the app stays open", manifest_reads() >= 2, hosts.log)
check("...each check reads only the public latest.json, with no key or anything about the person",
      all(p == "/mac/latest.json" for _, p, _ in hosts.log)
      and all("key" not in json.dumps(h or "").lower() for _, _, h in hosts.log), hosts.log)
switch["on"] = False
time.sleep(0.4)
n = manifest_reads()
time.sleep(0.8)
check("turned off, the timer stops checking", manifest_reads() == n and not a.auto_thread.is_alive(), hosts.log)
check("the hourly interval and its 5-minute clock look are what ships",
      saved[2] == 3600 and saved[1] == 300 and saved[0] == 20, saved)

if MAC:  # the background download, then the notice, with no click
    hosts.log.clear()
    serve("1.0.3", z103)
    b = appupdate.Updater(os.path.join(TMP, "b-app"), os.path.join(TMP, "b-data"), bundle=None, platform="darwin",
                          public_key_base64=PUB)
    b.version = "1.0.2"
    switch["on"] = True
    b.start_auto(lambda: switch["on"])
    end_at = time.time() + 60
    while time.time() < end_at and not (b.notice() and not b.running()):
        time.sleep(0.05)
    check("on: a newer version found by the schedule downloads in the background, checked, with no click",
          b.phase == "ready" and any(p.endswith(".zip") for _, p, _ in hosts.log), (b.phase, b.error))
    check("...and the status the page polls carries the ready notice",
          b.notice() == {"version": "1.0.3", "notes": "Library downloads are faster.", "dismissed": False}, b.notice())
    switch["on"] = False
    b.stop_auto()

    # a version whose install didn't start: the schedule doesn't download it again; Check for updates still does
    c_app, c_data = os.path.join(TMP, "c-app"), os.path.join(TMP, "c-data")
    os.makedirs(c_app)
    sha103 = hashlib.sha256(z103).hexdigest()
    with open(os.path.join(c_app, appupdate.STATE_FILE), "w") as f:
        json.dump({"installing": {"version": "1.0.3", "sha256": sha103}}, f)
    with open(os.path.join(c_app, appupdate.RESULT_FILE), "w") as f:
        json.dump({"ok": False, "restored": True, "error": "The new version didn't start, so the old one was put back."}, f)
    c = appupdate.Updater(c_app, c_data, bundle=None, platform="darwin", public_key_base64=PUB)
    c.version = "1.0.2"
    last = c.at_start()
    check("after a restore, the start says so once and remembers the version that failed",
          last["ok"] is False and c.state.get("failed") == {"version": "1.0.3", "sha256": sha103}
          and "installing" not in c.state and not os.path.exists(os.path.join(c_app, appupdate.RESULT_FILE)), c.state)
    hosts.log.clear()
    c.check(auto=True)
    check("...the automatic check doesn't download that version again", not c.running()
          and not any(p.endswith(".zip") for _, p, _ in hosts.log) and c.notice() is None, hosts.log)
    c.check()
    check("...Check for updates, pressed, offers it again", wait_phase(c, ("ready",)) and c.notice(), (c.phase, c.error))
appupdate.AUTO_FIRST_WAIT, appupdate.AUTO_TICK, appupdate.AUTO_EVERY = saved


# ---------- 4c. Windows: the installer downloads, checked; Restart now runs the helper ----------
WIN_APP, WIN_DATA = os.path.join(TMP, "w-app"), os.path.join(TMP, "w-data")
setup_bytes = b"MZ" + os.urandom(1_100_000)
setup_url = f"{BASE}/win/Starholm-Setup-1.0.3.exe"


def serve_win(body, sha=None):
    hosts.add(f"{BASE}/win/latest.json", manifest(platform="win", url=setup_url, size=len(body),
                                                  sha=sha or hashlib.sha256(body).hexdigest()))
    hosts.add(setup_url, body, step=65536)


programs = os.path.join(TMP, "Programs")
win_folder = os.path.join(programs, "Starholm")
os.makedirs(win_folder)
win_exe = os.path.join(win_folder, "Starholm.exe")
with open(win_exe, "wb") as f:
    f.write(b"MZ stand-in")
serve_win(b"PK" + setup_bytes[2:])
w = appupdate.Updater(WIN_APP, WIN_DATA, bundle=win_exe, platform="win32", public_key_base64=PUB)
w.version = "1.0.2"
w.check()
check("Windows: a download that isn't a Windows program is refused and deleted",
      wait_phase(w, ("error",)) and "Windows program" in (w.error or "")
      and not os.path.exists(os.path.join(WIN_DATA, "updates", "1.0.3")), w.error)
serve_win(setup_bytes, sha="0" * 64)
w.check()
check("Windows: a wrong SHA-256 is refused and deleted", wait_phase(w, ("error",)) and "checksum" in (w.error or "")
      and not os.path.exists(os.path.join(WIN_DATA, "updates", "1.0.3")), w.error)
hosts.log.clear()
serve_win(setup_bytes)
w.check()
setup_path = os.path.join(WIN_DATA, "updates", "1.0.3", "Starholm-Setup-1.0.3.exe")
check("Windows: the installer downloads in the background from win/, checked, and is ready",
      wait_phase(w, ("ready",)) and [p for _, p, _ in hosts.log] == ["/win/latest.json", "/win/Starholm-Setup-1.0.3.exe"]
      and w.state["ready"]["installer"] == setup_path and open(setup_path, "rb").read() == setup_bytes,
      (w.phase, w.error, hosts.log))
check("Windows: the notice offers Restart now, the same as on the Mac", w.snapshot()["ready"] == {
    "version": "1.0.3", "notes": "Library downloads are faster.", "dismissed": False, "install": "replace", "why": None},
      w.snapshot()["ready"])
check("Windows: the notice the page polls names it", w.notice() and w.notice()["version"] == "1.0.3")
spawned = []
os.environ["STARHOLM_UPD_EXE"] = "should-not-pass.cmd"
try:
    w.install(spawn=lambda args, **kw: spawned.append((args, kw)))
finally:
    os.environ.pop("STARHOLM_UPD_EXE")
args, kw = spawned[0]
script = base64.b64decode(args[-1]).decode("utf-16-le")
env = kw["env"]
check("Windows: install() starts Windows PowerShell by its full path with the helper encoded, no file, no shell",
      args[0].lower().endswith(r"system32\windowspowershell\v1.0\powershell.exe") and args[-2] == "-EncodedCommand"
      and script == appupdate.WIN_INSTALL_HELPER and "shell" not in kw and len(" ".join(args)) < 30000, args[:-1])
check("Windows: the helper is told the app's process, its folder, the installer, the update's folder and the result",
      env["STARHOLM_UPD_PID"] == str(os.getpid()) and env["STARHOLM_UPD_APP"] == os.path.realpath(win_folder)
      and env["STARHOLM_UPD_SETUP"] == setup_path and env["STARHOLM_UPD_DIR"] == os.path.dirname(setup_path)
      and env["STARHOLM_UPD_RESULT"] == os.path.join(WIN_APP, appupdate.RESULT_FILE)
      and env["STARHOLM_UPD_VERSION"] == "1.0.3" and "STARHOLM_UPD_EXE" not in env and "STARHOLM_UPD_KEY" not in env
      and env["PATH"].split(";")[0].lower().endswith("system32")
      and kw["cwd"] == os.path.expanduser("~") and kw["creationflags"] == appupdate.WIN_SPAWN_FLAGS
      and not kw["creationflags"] & 0x8, env)
check("Windows: the helper's own Apps entry is Starholm's AppId, and it waits 2 minutes for the first start",
      appupdate.WIN_UNINSTALL_KEY in script and "{63B4D449-2116-4BDC-A784-18C33E143B59}_is1" in script
      and "$wait = 120" in script and "/VERYSILENT" in script and "/SUPPRESSMSGBOXES" in script)
iss = open(os.path.join(ROOT, "ops", "windows-installer.iss"), encoding="utf-8").read()
check("Windows: the AppId is the installer's, so an update keeps one entry in Apps",
      "AppId={" + appupdate.WIN_APP_ID + "\n" in iss.replace("\r", ""))
os.makedirs(os.path.join(programs, ".Starholm-old-0123abcd"))
os.makedirs(os.path.join(programs, ".Starholm-old-0123abcd.app"))
os.makedirs(os.path.join(programs, "Other"))
appupdate.tidy_beside(win_exe, "win32")
check("Windows: the next start deletes an old folder an interrupted update left beside the install folder, only that",
      sorted(os.listdir(programs)) == [".Starholm-old-0123abcd.app", "Other", "Starholm"], os.listdir(programs))
os.chmod(programs, 0o555)
try:
    check("Windows: when the folder around the install folder can't be written, the manual way",
          appupdate.install_target(win_exe, "win32") == (False, "read_only") or sys.platform == "win32")
finally:
    os.chmod(programs, 0o755)
check("Windows: anything but Starholm.exe can't be replaced",
      appupdate.install_target(os.path.join(win_folder, "python.exe"), "win32") == (False, "source"))
os.remove(setup_path)
w2 = appupdate.Updater(WIN_APP, WIN_DATA, bundle=win_exe, platform="win32", public_key_base64=PUB)
w2.at_start()
check("Windows: a ready update whose installer is gone is forgotten at the next start", w2.notice() is None)
check("the Windows manifest's address must be in win/", raises(lambda: appupdate.verify(
    manifest(platform="win", url=f"{BASE}/mac/Starholm-Setup-1.0.3.exe"), "1.0.2", "win32", PUB), "invalid"))


# ---------- 4d. Windows: the helper itself, run here with a stand-in installer (on Windows only) ----------
def win_helper_run(mode):
    """Run WIN_INSTALL_HELPER for real, with a stand-in installer (setup.cmd, which runs fake_setup.py) and a
    stand-in app (start.cmd), and a registry key of its own in place of the Apps entry. mode: "ok" (installs, and the
    new version's start deletes the result file), "nostart" (installs, never starts) or "fail" (exits 3 halfway).
    Returns (programs folder, install folder, update folder, result or None, the key's DisplayVersion)."""
    import uuid
    import winreg
    root = os.path.join(TMP, f"wh-{mode}")
    progs, app, upd = os.path.join(root, "Programs"), os.path.join(root, "Programs", "Starholm"), \
        os.path.join(root, "updates", "9.9.9")
    for d in (app, upd, os.path.join(root, "app-data")):
        os.makedirs(d)
    with open(os.path.join(app, "version.txt"), "w") as f:
        f.write("old")
    with open(os.path.join(app, "start.cmd"), "w") as f:
        f.write("@exit /b 0\r\n")
    fake = os.path.join(root, "fake_setup.py")
    with open(fake, "w") as f:
        f.write(r"""import os, subprocess, sys
target = next(a[5:] for a in sys.argv if a.upper().startswith("/DIR="))
os.makedirs(target, exist_ok=True)
open(os.path.join(target, "version.txt"), "w").write("new")
subprocess.run(["reg", "add", os.environ["STARHOLM_UPD_KEY"], "/v", "DisplayVersion", "/d", "new", "/f"],
               capture_output=True)
if os.environ["FAKE_MODE"] == "fail":
    sys.exit(3)
with open(os.path.join(target, "start.cmd"), "w") as f:
    if os.environ["FAKE_MODE"] == "ok":
        f.write('@"%s" -c "import os; os.remove(os.environ[\'STARHOLM_UPD_RESULT\'])"\r\n' % sys.executable)
    else:
        f.write("@exit /b 0\r\n")
""")
    setup = os.path.join(upd, "setup.cmd")
    with open(setup, "w") as f:
        f.write(f'@"{sys.executable}" "{fake}" %*\r\n@exit /b %ERRORLEVEL%\r\n')
    key = rf"HKCU\Software\StarholmUpdateTest-{uuid.uuid4().hex[:8]}"
    subprocess.run(["reg", "add", key, "/v", "DisplayVersion", "/d", "old", "/f"], capture_output=True, check=True)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])  # a process that has ended: the app, already quit
    gone.wait()
    result_file = os.path.join(root, "app-data", appupdate.RESULT_FILE)
    env = {**os.environ, "STARHOLM_UPD_PID": str(gone.pid),
           "STARHOLM_UPD_APP": app, "STARHOLM_UPD_SETUP": setup, "STARHOLM_UPD_DIR": upd,
           "STARHOLM_UPD_RESULT": result_file, "STARHOLM_UPD_VERSION": "9.9.9", "STARHOLM_UPD_KEY": key,
           "STARHOLM_UPD_EXE": "start.cmd", "FAKE_MODE": mode}
    # started the way Updater.install() starts it: its flags, no standard streams
    helper = subprocess.Popen(appupdate.win_helper_command(), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=os.path.expanduser("~"),
                              creationflags=appupdate.WIN_SPAWN_FLAGS, close_fds=True)
    code = helper.wait(240)
    try:
        with open(result_file, encoding="utf-8-sig") as f:
            result = json.load(f)
    except OSError:
        result = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key.split("\\", 1)[1]) as k:
        version = winreg.QueryValueEx(k, "DisplayVersion")[0]
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key.split("\\", 1)[1])
    if code not in (0, 1):
        print(f"the helper exited with {code}")
    return progs, app, upd, result, version


if sys.platform == "win32":
    def text_of(path):
        try:
            return open(path).read()
        except OSError:
            return None
    progs, app, upd, result, version = win_helper_run("ok")
    check("Windows helper: the installer ran into the same folder and the new version started",
          text_of(os.path.join(app, "version.txt")) == "new" and result is None, (result, os.listdir(app)))
    check("Windows helper: one install folder, no old folder, no installer or update folder left",
          os.listdir(progs) == ["Starholm"] and not os.path.exists(upd), (os.listdir(progs), os.path.exists(upd)))
    check("Windows helper: the Apps entry is the new one's", version == "new", version)
    progs, app, upd, result, version = win_helper_run("nostart")
    check("Windows helper: a new version that doesn't start: the old folder is put back",
          text_of(os.path.join(app, "version.txt")) == "old" and os.listdir(progs) == ["Starholm"], os.listdir(progs))
    check("...the old Apps entry is put back, the download deleted, and the result says so",
          version == "old" and not os.path.exists(upd) and result and result.get("restored") is True
          and "didn't start" in result.get("error", ""), (version, result))
    progs, app, upd, result, version = win_helper_run("fail")
    check("Windows helper: an installer that fails: the old version and its Apps entry are back, nothing left",
          text_of(os.path.join(app, "version.txt")) == "old" and os.listdir(progs) == ["Starholm"] and version == "old"
          and not os.path.exists(upd) and result and result.get("restored") is True
          and "installer stopped" in result.get("error", ""), (os.listdir(progs), version, result))
else:
    print("PASS (skipped: the Windows helper itself runs on Windows, in .github/workflows/windows.yml)")

# ---------- 5. the server ----------
connections = []
real_connect = hosts.connect
downloader.CONNECT = lambda host, port: (connections.append(host), real_connect(host, port))[1]
sys.path.insert(0, os.path.join(ROOT, "app"))
import server  # noqa: E402

server.UPDATER.public_key = PUB
server.UPDATER.bundle = None
server.updates_at_start()
time.sleep(0.3)
check("a start with automatic updates off (the default) contacts nothing and starts no schedule",
      not connections and server.updates_auto() is False
      and not (server.UPDATER.auto_thread and server.UPDATER.auto_thread.is_alive()), connections)
st = server.updates_status()
check("GET /api/updates contacts nothing and names this version", not connections
      and st["app"]["version"] == appupdate.APP_VERSION and st["app"]["auto"] is False, st)
check("the status check carries the version and no notice", server.UPDATER.notice() is None)
try:
    server.updates_restart()
    refused = False
except server.HTTPProblem as exc:
    refused = exc.code == 409
check("Restart now with nothing ready is refused", refused)
appupdate.AUTO_FIRST_WAIT = 3600  # the schedule starts, but this test never waits for its first check
out = server.updates_action("auto", {"on": True})
check("the switch is saved and starts the schedule", out["app"]["auto"] is True and server.updates_auto()
      and server.UPDATER.auto_thread.is_alive())
check("turning it on contacts nothing at once", not connections, connections)
server.updates_action("auto", {"on": False})
time.sleep(0.1)
check("turning it off saves it and stops the schedule", server.updates_auto() is False
      and not server.UPDATER.auto_thread.is_alive())
# off and on again while the old schedule is still busy (here: still alive): a new schedule runs
busy = threading.Event()
old = threading.Thread(target=busy.wait, daemon=True)
old.start()
server.UPDATER.auto_thread = old  # stands in for a schedule in the middle of a check
server.updates_action("auto", {"on": True})
server.updates_action("auto", {"on": False})
server.UPDATER.auto_thread = old
server.updates_action("auto", {"on": True})
check("off and on during a check: a new schedule runs", server.UPDATER.auto_thread is not old
      and server.UPDATER.auto_thread.is_alive())
server.updates_action("auto", {"on": False})
busy.set()
# the switch on at the next start: the app checks by itself, at launch, with no click
appupdate.AUTO_FIRST_WAIT = 0.2
server.MEMORY.set_setting("updates", {"auto": True})
connections.clear()
server.updates_at_start()
end_at = time.time() + 10
while time.time() < end_at and not connections:
    time.sleep(0.05)
check("a start with automatic updates on checks download.starholm.app by itself, at launch",
      connections and set(connections) == {"download.starholm.app"}, connections)
server.updates_action("auto", {"on": False})
if server.UPDATER.thread:
    server.UPDATER.thread.join(60)
appupdate.AUTO_FIRST_WAIT = 3600
if MAC:
    serve("1.0.3", z103)
    server.UPDATER.version = "1.0.2"
    out = server.updates_action("check", {})
    check("Check for updates goes online: the manifest is read from download.starholm.app",
          connections and set(connections) == {"download.starholm.app"}, connections)
    check("...and with no key, the packs aren't asked about", out["packs"] is None and out["unlocked"] is False, out)
    server.UPDATER.thread.join(60)
    opened = []
    server._open_url_spawn = lambda args, **kw: opened.append(args)
    out = server.updates_restart()  # this test runs outside an app bundle: the manual way
    check("Restart now outside a writable app: the new app is shown in the Finder, with the steps",
          out["mode"] == "manual" and opened and opened[0][:2] == ["/usr/bin/open", "-R"] and "drag" in out["steps"],
          (out, opened))

hosts.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"{'FAILED' if FAILS else 'passed'}: {len(FAILS)} failed" if FAILS else "all app update checks passed")
sys.exit(1 if FAILS else 0)
