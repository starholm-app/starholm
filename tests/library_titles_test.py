"""library/titles.py: the app and the build run with or without the cleaning rules (library/cleanup.py).

With cleanup.py here (the private repository), build_library, browse, refresh and the server use it, as before.
Without it (the public repository, simulated here by blocking the import), they import, use the plain stand-ins,
the server starts, and only a cleaning rule itself raises CleaningRulesMissing. An import error inside cleanup.py
is never mistaken for a missing file.

Usage: run through tests/run_all.py
"""
import importlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import sandbox  # noqa: E402

sandbox.require()

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        FAILS.append(name)


# ---- without cleanup.py: a fresh process where importing library.cleanup fails as if the file were absent ----
WITHOUT = r"""
import importlib.abc, json, os, sys, threading, urllib.request
root = sys.argv[1]
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "library.cleanup":
            raise ModuleNotFoundError("No module named 'library.cleanup'", name=name)
        return None
sys.meta_path.insert(0, Absent())
sys.path[:0] = [root, os.path.join(root, "app")]
from library import browse, build_library, refresh, search, titles
import server
out = {"stand_in": [m.cleanup is titles for m in (browse, build_library, refresh)],
       "loaded": "library.cleanup" in sys.modules,
       "sort": [titles.sort_key(None, t) for t in ("Éclair", "'Quoted", "10 knots", "2 knots", "Ωmega")],
       "letter": [titles.sort_letter(titles.sort_key(None, t)) for t in ("apple", "9 lives")],
       "display": titles.display_title("wikipedia", "Α-Pinene"),
       "off": sorted(titles.sources_off(os.path.join(root, "no-such-switch.json"))),
       "trim": sorted(titles.trim_tiers())}
try:
    build_library.cleanup.clean_document("ready-gov", "t", "u", "x")
    out["rule"] = "no error"
except titles.CleaningRulesMissing as exc:
    out["rule"] = "missing" if "only the free (raw) library" in str(exc) else str(exc)
httpd = server.make_server(0)
server.PORT = port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
req = urllib.request.Request(f"http://127.0.0.1:{port}/ping", headers={"Host": f"127.0.0.1:{port}"})
with urllib.request.urlopen(req, timeout=10) as r:
    out["ping"] = json.load(r)
httpd.shutdown()
print(json.dumps(out))
"""
r = subprocess.run([sys.executable, "-B", "-c", WITHOUT, ROOT], capture_output=True, text=True,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120)
got = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else None
check("without cleanup.py the build, browse, refresh and the server import", got is not None,
      (r.stdout + r.stderr)[-1500:])
if got:
    check("they use the plain stand-ins, and cleanup.py is never loaded", got["stand_in"] == [True] * 3
          and not got["loaded"], got)
    check("the stand-in sort key drops accents and leading punctuation, pads numbers, marks others with #",
          got["sort"] == ["eclair", "quoted", "#0000000010 knots", "#0000000002 knots", "#ωmega"], got["sort"])
    check("the stand-in letters are A to Z or #", got["letter"] == ["A", "#"], got["letter"])
    check("titles are shown as stored", got["display"] == "Α-Pinene", got["display"])
    check("a missing switch file switches nothing off, and no trim tier is on", got["off"] == [] and got["trim"] == [])
    check("a cleaning rule raises CleaningRulesMissing, naming the raw build", got["rule"] == "missing", got["rule"])
    check("the server starts and answers /ping", got["ping"].get("app") == "survival-llm", got["ping"])

# ---- with cleanup.py (the private repository): everything uses it, exactly as before ----
from library import titles  # noqa: E402

if os.path.isfile(os.path.join(ROOT, "library", "cleanup.py")):
    from library import browse, build_library, cleanup, refresh  # noqa: E402
    check("with cleanup.py, build_library, browse and refresh use it",
          all(m.cleanup is cleanup for m in (browse, build_library, refresh)) and titles.cleanup() is cleanup)
    check("its own rules are the ones that run (wikiHow's sort key drops 'How to')",
          build_library.cleanup.sort_key("wikihow", "How to Tie a Knot") == "tie a knot")
else:
    print("PASS (skipped: no library/cleanup.py in this copy, as in the public repository)")

# An import error inside cleanup.py (here: a module it needs) is raised, never taken for a missing cleanup.py.
real = importlib.import_module


def broken(name, *args, **kwargs):
    if name == "library.cleanup":
        raise ModuleNotFoundError("No module named 'json'", name="json")
    return real(name, *args, **kwargs)


importlib.import_module = broken
try:
    titles.cleanup()
    raised = False
except ModuleNotFoundError as exc:
    raised = exc.name == "json"
finally:
    importlib.import_module = real
check("an import error inside cleanup.py is raised, not hidden", raised)

print(f"{'FAILED' if FAILS else 'passed'}: library titles")
sys.exit(1 if FAILS else 0)
