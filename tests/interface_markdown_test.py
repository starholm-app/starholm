"""The Markdown renderer in the interface (app/static/render.js): heading ids and the outline, [text](#anchor) jumps,
web links kept as plain text, table alignment, task lists, callouts, footnotes, guide figures, and hostile input that
must never become markup or a request; also the two-finger swipe-back decision and the missing-document check. The
checks are JavaScript (tests/interface_markdown_check.js) run with node over a small stand-in DOM; without node on the
PATH this test reports a skip. Then the page's safety rules: render.js never writes HTML as text, and the Content
Security Policy is the one the app has always sent. It reads no app folder, so it needs no sandbox.

Usage: .venv/bin/python tests/interface_markdown_test.py"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


with open(os.path.join(ROOT, "app", "static", "render.js"), encoding="utf-8") as f:
    render_js = f.read()
code = "\n".join(line for line in render_js.splitlines() if not line.lstrip().startswith("//"))  # the header names them
found = re.findall(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", code)
check("render.js never writes HTML as text (comments aside)", not found, found)
with open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8") as f:
    server_py = f.read()
CSP = ('CSP = ("default-src \'self\'; script-src \'self\'; style-src \'self\'; img-src \'self\' data:; font-src \'self\'; "\n'
       '       "connect-src \'self\'; media-src \'self\'; object-src \'none\'; frame-src \'none\'; worker-src \'self\'; "\n'
       '       "base-uri \'none\'; form-action \'self\'; frame-ancestors \'none\'")')
# worker-src 'self' (was 'none'): the map viewer's workers, started from /static/maplibre-gl-csp-worker.js only.
check("the page's Content Security Policy is unchanged", CSP in server_py)

node = shutil.which("node")
if not node:
    print("SKIP the Markdown render checks: node isn't on the PATH")
else:
    result = subprocess.run([node, os.path.join(HERE, "interface_markdown_check.js"),
                             os.path.join(ROOT, "app", "static", "render.js")], capture_output=True, text=True, timeout=120)
    print((result.stdout + result.stderr).strip())
    check("the Markdown render checks", result.returncode == 0)
if FAILS:
    print(f"FAIL {len(FAILS)} checks: {', '.join(FAILS)}")
    sys.exit(1)
print("all Markdown and page safety checks passed")
