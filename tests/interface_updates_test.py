"""Starholm's own updates in the interface: the checks in tests/interface_updates_check.js (run with node; without node
on the PATH they report a skip) for app/static/updates.js, then how app.js, api.js, index.html, app.css and the
server wire it: the script loads before app.js, the Updates rows sit on Settings › Status, the notice is its own
dialog, the page asks only this app's /api/updates endpoints, the Mac app menu's Check for Updates… runs the page's
check, the styles use the colour tokens only, and the page never names the update host (the server alone reads it).
It reads no app folder, so it needs no sandbox.

Usage: .venv/bin/python tests/interface_updates_test.py"""
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


app, api, upd, index, css, server = (code_of("app.js"), code_of("api.js"), code_of("updates.js"),
                                     read("app", "static", "index.html"), read("app", "static", "app.css"),
                                     read("app", "server.py"))
check("index.html loads updates.js after render.js and upgrade.js, before app.js",
      index.find("/static/render.js") < index.find("/static/upgrade.js") < index.find("/static/updates.js")
      < index.find("/static/app.js"))
status_pane = index[index.find('id="pane-status"'):index.find('id="pane-model"')]
check("the Updates rows sit on Settings › Status, above Quit",
      'id="updates-section"' in status_pane and status_pane.find('id="updates-section"') < status_pane.find("Quit Starholm"))
card = re.search(r'<aside class="upd-card" id="update-card"[^>]*>', index)
check("the ready notice is a card of its own outside Settings, not modal, named and described for screen readers",
      card and 'role="dialog"' in card.group(0) and 'aria-modal="false"' in card.group(0)
      and 'aria-labelledby="update-title"' in card.group(0) and "aria-describedby" not in card.group(0)
      and " hidden" in card.group(0) and index.find('id="update-card"') < index.find('id="settings"')
      and 'id="update-dialog"' not in index and "update-dialog" not in app)
check("...a screen reader hears it appear, without the focus being taken",
      'id="update-live" aria-live="polite"' in index and '$("update-live").textContent = UV.announce(ready);' in app
      and "updCard.hidden = false;" in app and not re.search(r"function showUpdateCard[^}]*?\.focus\(\)", app))
rule = re.search(r"\.upd-card \{([^}]*)\}", css)
check("...it is as wide as the side panel's buttons, sits just above Settings, below the toast, and prints never",
      rule and "position: fixed" in rule.group(1) and "left: 10px" in rule.group(1)
      and "bottom: var(--upd-bottom, 12px)" in rule.group(1) and "calc(var(--sidebar) - 20px)" in rule.group(1)
      and "z-index: 35" in rule.group(1) and ".toast, .upd-card," in css
      and "function placeUpdateCard()" in app and 'document.querySelector(".side-foot")' in app)
check("...it shows for every ready update, whoever started the download (the same render path for both)",
      "if (app.ready) showUpdateCard(app.ready);" in app and app.count("showUpdateCard(") >= 3)
check("updates.js writes no HTML as text and calls no server itself",
      not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|fetch\(|XMLHttpRequest|SLApi", upd))
check("api.js asks only this app's own /api/updates endpoints",
      'call("/api/updates")' in api and "post(`/api/updates/${encodeURIComponent(str(action))}`, json)" in api
      and 'post("/api/updates/restart")' in api)
check("the server answers exactly check, auto and later, plus restart",
      'UPDATES_ACTIONS = ("check", "auto", "later")' in server and '"/api/updates/restart"' in server)
check("the page draws the server's answer through SLUpdateViews and polls only while checking or downloading",
      "UV.rows(UPD.st, UPD.ui," in app
      and 'if (["checking", "downloading", "unpacking"].includes(app.phase)) UPD.timer = setTimeout(loadUpdates, 1000);' in app)
check("the Upgrade page's Check for updates is the same check (the server runs check_for_updates for both)",
      server.count("check_for_updates()") >= 3 and 'upAct("check-updates")' in app)
check("the Mac app menu has Settings… then Check for Updates…, under About, each calling the page",
      '[MenuAction("Settings…", settings_from_menu), MenuAction("Check for Updates…", check_from_menu)]' in server
      and 'menu_to_page("window.SLUpdates && window.SLUpdates.fromMenu()"' in server
      and 'menu_to_page("window.SLSettings && window.SLSettings.open()"' in server
      and "window.SLUpdates = { fromMenu:" in app and "window.SLSettings = { open: () => openSettings() };" in app)
check("the menu calls the page with run_js: evaluate_js wraps the script in eval(), which the page's CSP refuses",
      "window.run_js(script)" in server and "evaluate_js" not in server.replace("evaluate_js wraps", "")
      and "script-src 'self'" in server)
check("Settings… gets Cmd+, once the menu exists, and the window comes forward, out of the Dock if minimised",
      'item.setKeyEquivalent_(",")' in server and "NSEventModifierFlagCommand" in server
      and "window.restore()" in server and "window.show()" in server
      and "BrowserView._add_app_menu = add_app_menu" in server and "            mac_menu_shortcuts()" in server)
check("the menu items are added on the Mac only (a Windows window would get a menu bar)",
      re.search(r'if PLATFORM == "darwin":  # Settings… \(Cmd\+,\) and Check for Updates…', server) is not None)
check("inside the page Cmd+, (Ctrl+, on Windows) opens Settings too",
      'if (mod && e.key === ",") { e.preventDefault(); openSettings(); return; }' in app)
check("Esc inside the card counts as Later (or closes the manual steps)",
      'updCard.onkeydown = (e) => {' in app and 'if (e.key !== "Escape") return;' in app
      and 'if (updCard.querySelector("[data-action=later]")) later(); else hideUpdateCard();' in app)
check("Later hides the card; the server keeps it hidden until the next start or the next version",
      "hideUpdateCard();\n      try { UPD.st = await API.updates.act(\"later\"); }" in app)
block = css[css.find("/* ---------- Starholm's own updates"):]
check("the notice's styles are in app.css and name colours by token only",
      ".upd-card {" in block and not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", block))
csp = re.search(r'CSP = \((.*?)\)\n', server, re.S).group(1)
check("the content security policy still connects to this app's own server only",
      "connect-src 'self';" in csp and "starholm" not in csp and "https:" not in csp)
check("the update host is named in upgrade/appupdate.py, not in the page",
      "download.starholm.app" not in upd + app + api + index
      and 'UPDATE_HOST = "download.starholm.app"' in read("upgrade", "appupdate.py"))

node = shutil.which("node")
if not node:
    print("SKIP the update view checks: node isn't on the PATH")
else:
    result = subprocess.run([node, os.path.join(HERE, "interface_updates_check.js"), os.path.join(STATIC, "updates.js"),
                             os.path.join(STATIC, "render.js"), os.path.join(STATIC, "upgrade.js")],
                            capture_output=True, text=True, timeout=120)
    print((result.stdout + result.stderr).strip())
    check("the update view checks", result.returncode == 0)
if FAILS:
    print(f"FAIL {len(FAILS)} checks: {', '.join(FAILS)}")
    sys.exit(1)
print("all update interface checks passed")
