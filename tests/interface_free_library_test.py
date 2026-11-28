"""The free library's cards on the Library page: the checks in tests/interface_free_library_check.js (run with node;
without node on the PATH they report a skip) for app/static/free-library.js in its four states, then how app.js,
api.js, index.html and app.css wire it: the script loads before app.js, a download starts only from the Download
button after the person confirms, the server's answer drives the page, the styles use the colour tokens only, and the
content security policy still lets the page connect to this app's own server only. It reads no app folder, so it
needs no sandbox.

Usage: .venv/bin/python tests/interface_free_library_test.py"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(ROOT, "app", "static")
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def code_of(name):
    return "\n".join(line for line in read("app", "static", name).splitlines() if not line.lstrip().startswith("//"))


app, api, free, index, css, server = (code_of("app.js"), code_of("api.js"), code_of("free-library.js"),
                                      read("app", "static", "index.html"), read("app", "static", "app.css"),
                                      read("app", "server.py"))
check("index.html loads free-library.js after render.js and api.js, before app.js",
      index.find("/static/render.js") < index.find("/static/api.js") < index.find("/static/free-library.js")
      < index.find("/static/app.js"))
check("free-library.js writes no HTML as text and calls no server itself",
      not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|fetch\(|XMLHttpRequest|SLApi", free))
check("api.js asks only this app's own /api/free-library endpoints",
      'call("/api/free-library")' in api and "post(`/api/free-library/${encodeURIComponent(str(action))}`, json)" in api)
start = re.findall(r'freeAct\("start"[^)]*\)', app)
check("a download starts only from Download, after the person confirms it",
      start == ['freeAct("start", { keys })'] and 'if (!ok) return;\n      await freeAct("start", { keys });' in app
      and 'confirmDialog("Download the free library?"' in app)
check("the page draws the server's answer through SLFreeLibrary.view, and polls only while something runs",
      "FL.view(L.free, L.freeUi, { h, icon, on: freeOn, ai: aiUi(\"first\") })" in app and 'L.freeTimer = setTimeout(pollFree, 1500);' in app
      and '["downloading", "building"].includes(job.phase)' in app)
check("the library under the cards is hidden only when the view says so", '$("lib-content").hidden = v.hideLibrary;' in app)
check("the upgrade buttons open Settings (its Upgrade page once it exists)",
      "openSettings: () => openSettings(upgradeTab())" in app)
block = css[css.find("/* the free library"):css.find("@media (max-width: 720px)", css.find("/* the free library"))]
check("the cards' styles are in app.css and name colours by token only",
      ".fl-card" in block and ".fl-pick" in block and ".fl-steps" in block and not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", block))
csp = re.search(r'CSP = \((.*?)\)\n', server, re.S).group(1)
check("the content security policy still connects to this app's own server only",
      "connect-src 'self';" in csp and "kiwix" not in csp.lower() and "https:" not in csp and "http:" not in csp)
price = re.compile(r"\$\s?\d|\bprice\b", re.I)
check("no price in the Library cards or their wiring", not price.search(free)
      and not price.search(app[app.find("the free library (free-library.js)"):app.find("function renderLibMain")]))

node = shutil.which("node")
if not node:
    print("SKIP the free library checks: node isn't on the PATH")
else:
    result = subprocess.run([node, os.path.join(HERE, "interface_free_library_check.js"), os.path.join(STATIC, "free-library.js"),
                             os.path.join(STATIC, "render.js"), os.path.join(ROOT, "library", "free-sources.json")],
                            capture_output=True, text=True, timeout=120)
    print((result.stdout + result.stderr).strip())
    check("the free library checks", result.returncode == 0)
if FAILS:
    print(f"FAIL {len(FAILS)} checks: {', '.join(FAILS)}")
    sys.exit(1)
print("all free library interface checks passed")
