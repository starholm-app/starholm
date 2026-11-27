"""POST /api/restart ("Restart now" when a newer library is ready), on a real local socket: 501 outside the app
bundle; inside it, a detached helper is started with a fixed argument list and no shell, the request is answered,
and the app quits as /api/quit does. The helper and the bundle are stand-ins: nothing is relaunched for real. Also
how the server tells it runs inside Starholm.app (its executable path and the bundle's Info.plist).

Usage: .venv/bin/python tests/backend_restart_test.py"""
import http.client
import json
import os
import plistlib
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()
sandbox.mac_only("checks the Starholm.app restart helper")
os.environ.pop("OLLAMA_HOST", None)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import server  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


spawned = []
server._spawn = lambda args, **kw: spawned.append((args, kw))  # never start the real helper

# ---- telling whether this runs inside the app ----
bundle = os.path.join(TMP, "Apps", "Starholm.app")
os.makedirs(os.path.join(bundle, "Contents", "MacOS"))
exe = os.path.join(bundle, "Contents", "MacOS", "Starholm")
open(exe, "w").close()


def info(bundle_id):
    with open(os.path.join(bundle, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, f)


real_exe = server.executable_path
check("this test process isn't inside the app", server.app_bundle() is None, server.executable_path())
check("its executable path is found", real_exe() and os.path.isabs(real_exe()), real_exe())
server.executable_path = lambda: exe
check("no Info.plist: not the app", server.app_bundle() is None)
info("com.example.other")
check("another app's bundle id: not the app", server.app_bundle() is None)
info("local.survival-llm.app")
check("Starholm.app's executable: the bundle", server.app_bundle() == bundle, server.app_bundle())
server.executable_path = lambda: os.path.join(ROOT, ".venv", "bin", "python")
check("the venv's Python (terminal or --script launcher): not the app", server.app_bundle() is None)
check("the helper script compiles", compile(server.RELAUNCH, "relaunch", "exec") is not None)
check("the helper opens the bundle with /usr/bin/open and a fixed list", 'subprocess.run(["/usr/bin/open", bundle]'
      in server.RELAUNCH and "shell" not in server.RELAUNCH)


def run_helper(app_shows_after):
    """Run the helper script here with os.kill, time.sleep and subprocess.run stood in for: the old app is gone after
    two checks, and the new app's executable shows in ps after app_shows_after opens (None: never). Returns what it did,
    in order: ("sleep", s), ("open", argv) and ("ps", argv)."""
    import subprocess
    import time
    did, kills, opens = [], [0], [0]
    real = (os.kill, time.sleep, subprocess.run)

    def kill(pid, sig):
        kills[0] += 1
        if kills[0] > 2:
            raise ProcessLookupError(pid)

    def run(argv, **kw):
        if argv[0] == "/usr/bin/open":
            opens[0] += 1
            did.append(("open", argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        did.append(("ps", argv))
        shown = app_shows_after is not None and opens[0] >= app_shows_after
        out = "/sbin/launchd\n" + (f"{bundle}/Contents/MacOS/Starholm\n" if shown else "")
        return subprocess.CompletedProcess(argv, 0, out, "")
    os.kill, time.sleep, subprocess.run = kill, lambda s: did.append(("sleep", s)), run
    saved_argv = sys.argv
    sys.argv = ["relaunch", "4242", bundle]
    try:
        exec(compile(server.RELAUNCH, "relaunch", "exec"), {"__name__": "__main__"})
    except SystemExit:
        pass
    finally:
        os.kill, time.sleep, subprocess.run = real
        sys.argv = saved_argv
    return did


did = run_helper(1)
first_open = next(i for i, d in enumerate(did) if d[0] == "open")
check("the helper waits a second after the app has gone before it opens it (LaunchServices lags; -600)",
      did[first_open - 1] == ("sleep", 1.0), did[:first_open + 1])
check("the helper opens once when the app shows in ps", [d for d in did if d[0] == "open"] ==
      [("open", ["/usr/bin/open", bundle])], did)
check("ps is called with a fixed list", all(d[1] == ["/bin/ps", "-A", "-o", "args="] for d in did if d[0] == "ps"), did)
did = run_helper(2)
check("an open that starts nothing is tried again", sum(d[0] == "open" for d in did) == 2, did)
did = run_helper(None)
check("three opens at most, about 5 seconds apart, then the helper ends",
      sum(d[0] == "open" for d in did) == 3 and sum(d[0] == "ps" for d in did) == 150, len(did))

# ---- the endpoint ----
httpd = server.make_server(0)
server.httpd = httpd
server.PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def post(path, token=True):
    conn = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=30)
    headers = {"X-Token": server.TOKEN, "Content-Type": "application/json"} if token else {}
    conn.request("POST", path, body=b"{}", headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data or b"null")


check("no token: 403", post("/api/restart", token=False)[0] == 403 and not spawned)
code, got = post("/api/restart")
check("outside the app: 501 with a sentence, nothing started, still running",
      code == 501 and "error" in got and not spawned and not server.quit_event.is_set(), (code, got))
closed = threading.Event()
server.quit_hooks.append(closed.set)  # stands in for the window's destroy()
server.executable_path = lambda: exe
code, got = post("/api/restart")
check("inside the app: ok", code == 200 and got == {"ok": True}, (code, got))
args, kw = spawned[-1] if spawned else ([], {})
check("the helper: the venv's Python, isolated, the fixed script, this pid and the bundle",
      args == [sys.executable, "-I", "-c", server.RELAUNCH, str(os.getpid()), bundle], args)
check("no shell, detached, no inherited files", "shell" not in kw and kw.get("start_new_session") is True
      and kw.get("close_fds") is True, kw)
check("a minimal environment: PATH and HOME only", kw.get("env") == {"PATH": "/usr/bin:/bin",
                                                                    "HOME": os.path.expanduser("~")}, kw.get("env"))
check("then the app quits as /api/quit does", closed.wait(5) and server.quit_event.wait(5))

print(f"{'all passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
