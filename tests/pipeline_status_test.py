"""refresh.py's status file (refresh-status.json beside the log: written atomically, progress with byte counts
during a download, the result and the sources changed at the end) and its call to emergency.save_detected_country()
at the end of every run, guarded so a missing module or an error only logs a warning. Every network step is
replaced by a stub; nothing is downloaded.

Usage: run through tests/run_all.py"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

TMP = tempfile.mkdtemp(prefix="pipeline-status-")
os.environ["SURVIVAL_LLM_DATA"] = os.path.join(TMP, "data")
os.environ["SURVIVAL_LLM_APP_DIR"] = os.path.join(TMP, "app")
os.environ["SURVIVAL_LLM_LIBRARY"] = os.path.join(TMP, "no-library")
os.environ["SURVIVAL_LLM_REFRESH_LOG"] = os.path.join(TMP, "logs", "refresh.log")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import build_library, fetch_manuals, fetch_risk, fetch_wikihow, fetch_zims, refresh  # noqa: E402

FAILS = []
COUNT = [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        FAILS.append(name)


STATUS = os.path.join(TMP, "logs", "refresh-status.json")
check("the status file sits beside the log", refresh.STATUS_PATH == STATUS, refresh.STATUS_PATH)


def read_status():
    with open(STATUS, encoding="utf-8") as f:
        return json.load(f)


# ---- stubs for every step that would use the network ----
ZIM = os.path.join(TMP, "data", "zim")
os.makedirs(ZIM)
SIZE = 3_400_000
item = {"name": "medlineplus.gov_en_all", "file": "medlineplus.gov_en_all_2026-11.zim", "status": "download",
        "size": SIZE, "sha256": "0" * 64, "path": "x"}
seen_progress = []


def fake_download(it):
    path = os.path.join(ZIM, it["file"])
    with open(path, "wb") as f:
        for _ in range(6):
            f.write(b"\0" * (SIZE // 6))
            f.flush()
            time.sleep(0.12)
            seen_progress.append(read_status()["progress"])
    return True


build_result = {"status": "swapped", "changed": ["medlineplus"], "build": "/x/lib-2026-11-15", "problems": [],
                "removed": []}
calls = []
fetch_zims.plan = lambda force=(): [dict(item)]
fetch_zims.download_zim = fake_download
fetch_wikihow.plan = lambda force=(): {"status": "current"}
fetch_wikihow.describe = lambda it: "current"
fetch_wikihow.fetch = lambda it, lock: {"pending": [], "failed": []}
fetch_manuals.main = lambda: {"pending": [], "failed": []}
fetch_risk.main = lambda: {"pending": [], "failed": []}
build_library.build_and_swap = lambda force=(): dict(build_result)
refresh.retire = lambda result: None
refresh.prune_zims = lambda items: None
refresh.disk_report = lambda items=None: None
refresh.notify = lambda message: calls.append(("notify", message))
refresh.ollama_cloud_check = lambda dry=False: None
refresh.STATUS_EVERY = 0.05


def stub_emergency(behaviour):
    """Install an emergency module: "ok" records the call, "raise" raises, None makes the import fail."""
    if behaviour is None:
        sys.modules["emergency"] = None  # import emergency -> ImportError
        return
    module = types.ModuleType("emergency")

    def save_detected_country(*args, **kw):
        calls.append(("emergency", args, kw))
        if behaviour == "raise":
            raise RuntimeError("time zone unreadable")
    module.save_detected_country = save_detected_country
    sys.modules["emergency"] = module


# ---- a run that downloads one ZIM and swaps in a build ----
stub_emergency("ok")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = refresh.run(())
status = read_status()
check("run: exit 0", code == 0, out.getvalue()[-500:])
check("status at the end: finished, updated, with the changed source and no progress",
      status["state"] == "finished" and status["result"] == "updated" and status["changed"] == ["medlineplus"]
      and status["progress"] is None and status["problems"] == [] and status["version"] == 1
      and status["pid"] == os.getpid() and status["started"] <= status["finished"], status)
downloads = [p for p in seen_progress if p and p["step"] == "download"]
check("status while downloading: the source's name and byte counts that grow",
      downloads and all(p["source"] == "medlineplus" and p["total"] == SIZE for p in downloads)
      and downloads[-1]["done"] > downloads[0]["done"]
      and downloads[-1]["text"].startswith("Downloading MedlinePlus (NIH), ")
      and downloads[-1]["text"].endswith(f" of {SIZE / 1e6:.0f} MB"), downloads)
check("the emergency numbers are saved at the end of the run, with no arguments",
      ("emergency", (), {}) in calls, calls)
check("no temporary status file is left", os.listdir(os.path.dirname(STATUS)) == ["refresh-status.json"],
      os.listdir(os.path.dirname(STATUS)))

# ---- written atomically: a reader never sees a partial file ----
s = refresh.Status()
s.data["problems"] = ["x" * 200] * 2000  # a large file, so a non-atomic write would be caught half-written
bad = []
stop = threading.Event()


def reader():
    while not stop.is_set():
        try:
            read_status()
        except ValueError:
            bad.append(1)
        except OSError:
            pass


t = threading.Thread(target=reader)
t.start()
for _ in range(60):
    s.write()
stop.set()
t.join()
check("status file: replaced atomically (no reader ever saw a partial file)", not bad, len(bad))

# ---- nothing changed ----
build_result.update(status="noop", changed=[])
calls.clear()
with contextlib.redirect_stdout(io.StringIO()):
    code = refresh.run(())
check("no change: result no-change, nothing changed", code == 0 and read_status()["result"] == "no-change"
      and read_status()["changed"] == [] and ("emergency", (), {}) in calls, read_status())

# ---- a failed build ----
build_result.update(status="failed", changed=[], problems=["wikipedia: 0 documents, expected at least 1"])
calls.clear()
stub_emergency("raise")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = refresh.run(())
status = read_status()
check("failed build: exit 1, result failed with the problem", code == 1 and status["result"] == "failed"
      and status["problems"] == ["wikipedia: 0 documents, expected at least 1"], status)
check("an emergency module that raises: only a warning, the run still finishes",
      ("emergency", (), {}) in calls and "WARNING: emergency numbers not saved: RuntimeError" in out.getvalue()
      and status["state"] == "finished", out.getvalue()[-600:])

# ---- the emergency module is missing ----
build_result.update(status="noop", changed=[], problems=[])
stub_emergency(None)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = refresh.run(())
check("a missing emergency module: only a warning, exit 0",
      code == 0 and "WARNING: emergency numbers not saved: the emergency module did not load" in out.getvalue()
      and read_status()["result"] == "no-change", out.getvalue()[-600:])

# ---- a run that crashes still finishes its status file and saves the numbers ----
stub_emergency("ok")
calls.clear()


def broken_plan(force=()):
    raise OSError("catalog unreadable")


fetch_zims.plan = broken_plan
try:
    with contextlib.redirect_stdout(io.StringIO()):
        refresh.run(())
    raised = False
except OSError:
    raised = True
status = read_status()
check("a crash: the status file says failed, with the error, and the numbers are still saved",
      raised and status["state"] == "finished" and status["result"] == "failed"
      and "catalog unreadable" in status["problems"][0] and ("emergency", (), {}) in calls, status)

# ---- the call is guarded in the code itself ----
source = open(refresh.__file__, encoding="utf-8").read()
check("refresh.py imports emergency only inside the guarded function",
      source.count("import emergency") == 1 and "\nimport emergency" not in source)

sys.modules.pop("emergency", None)
shutil.rmtree(TMP, ignore_errors=True)
print(f"all {COUNT[0]} checks passed" if not FAILS else f"{COUNT[0] - len(FAILS)} of {COUNT[0]} checks passed")
sys.exit(1 if FAILS else 0)
