"""Run every test in this folder, each in its own process (they stand in for the model in different ways).

Usage: .venv/bin/python tests/run_all.py
No Ollama or library needed: these test memory.py and core.py's handling of what a model writes.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Every test gets temporary settings, data, library and files folders unless it sets its own, so no test reads or
# writes the app's real folders (importing library/search.py reads the settings and marks the live library build
# as in use). tests/sandbox.py makes each test refuse to run without them.
SANDBOX = tempfile.mkdtemp(prefix="survival-llm-tests-")
ENV = {**os.environ, "SURVIVAL_LLM_APP_DIR": os.path.join(SANDBOX, "app"),
       "SURVIVAL_LLM_DATA": os.path.join(SANDBOX, "data"), "SURVIVAL_LLM_LIBRARY": os.path.join(SANDBOX, "library"),
       "SURVIVAL_LLM_FILES": os.path.join(SANDBOX, "files")}
# The app carries the production keys. tests/testkeys/sitecustomize.py swaps in the TEST keys in every test process
# and every server a test spawns; only this runner puts it on the path.
TESTKEYS = os.path.join(HERE, "testkeys")
ENV["PYTHONPATH"] = os.pathsep.join(p for p in (TESTKEYS, os.environ.get("PYTHONPATH")) if p)
TIMEOUT = 300  # seconds for any one test file, so a test that hangs fails and the rest still run
failed = []
for path in sorted(glob.glob(os.path.join(HERE, "*_test.py"))):
    try:
        result = subprocess.run([sys.executable, path], capture_output=True, text=True, env=ENV, timeout=TIMEOUT)
        code, out = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:  # a test that hangs fails with what it printed, and the rest still run
        def text(b):
            return b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")
        code, out = 1, text(exc.stdout) + text(exc.stderr) + f"\ntimed out after {TIMEOUT} seconds"
    lines = out.strip().splitlines()
    print(f"{'ok  ' if code == 0 else 'FAIL'} {os.path.basename(path)}: {lines[-1] if lines else ''}", flush=True)
    if code:
        failed.append(path)
        print("\n".join(line for line in lines if not line.startswith("PASS")), flush=True)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if failed else 0)
