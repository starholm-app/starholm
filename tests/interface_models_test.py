"""Settings › Model and library-only mode in the interface: the checks in tests/interface_models_check.js (run with
node; without node on the PATH they report a skip) for app/static/models.js, then how index.html and app.js wire it:
the Model page with its table, the AI engine's row and the Library only switch, the table asked again while a model downloads,
the start screen's no-model note with its link to the Model page, and the line under a question with no model in place
of Summarise, and "and the AI too": the Upgrade page's Download now and the free library's download also set up the AI,
with the AI's own line on the Upgrade page. It reads no app folder, so it needs no sandbox.

Usage: .venv/bin/python tests/interface_models_test.py"""
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


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


app, models, index, api = read("app.js"), read("models.js"), read("index.html"), read("api.js")
code = "\n".join(line for line in models.splitlines() if not line.lstrip().startswith("//"))
check("models.js uses no DOM and no network", not re.search(r"\bdocument\b|\bfetch\(|XMLHttpRequest|innerHTML", code))
check("index.html loads models.js before app.js", 0 < index.find("/static/models.js") < index.find("/static/app.js"))
pane = index[index.find('id="pane-model"'):index.find("</section>", index.find('id="pane-model"'))]
check("Settings has a Model page after Status", 0 < index.find('id="tab-status"') < index.find('id="tab-model"')
      < index.find('id="tab-appearance"') and pane)
check("the Model page holds the table, the AI engine's row and the Library only switch",
      'id="model-table"' in pane and 'id="ollama-text"' in pane and 'id="ollama-action"' in pane
      and 'id="docs-only"' in pane and ">Library only<" in pane)
check("the Library only switch is on the Model page only", index.count('id="docs-only"') == 1)
check("the table is drawn by models.js with the page's own h()", "M.buildTable(data, h, modelAction)" in app)
check("the Model page loads when its tab opens, with the Library only setting",
      "model: loadModelPage" in app and "loadAnswers();\n    loadModels();" in app)
check("it is asked again while a model downloads, only while the page is open, and stops when Settings closes",
      "const wait = M.pollDelay(data);" in app and '!$("pane-model").hidden) modelsTimer = setTimeout(loadModels, wait)' in app
      and "clearTimeout(modelsTimer)" in app)
check("the buttons download, cancel and choose through the API",
      "API.models.pull(model)" in app and "API.models.cancel(model)" in app and "API.setModel(model)" in app
      and '"/api/models/pull"' in api and '"/api/models/cancel"' in api and 'call("/api/models")' in api)
check("Start the AI and Check again look for the engine and start it", '$("ollama-action").addEventListener("click", async () => '
      "{ await startEngine(); loadModels(); });" in app)
check("with no model, questions are still asked (library-only), not refused",
      "const libraryOnly = () => documentsOnly() || M.noModel(S.status);" in app
      and "function notReady(needModel = !libraryOnly(), summarise = false)" in app
      and "s.ollama_starting ? (s.library && !summarise ? null" in app)
check("the start screen says library-only mode with a link to the Model page",
      'id="start-offline-link"' in index and "M.welcomeNote(S.status)" in app
      and '$("start-offline-link").addEventListener("click", () => openSettings("model"));' in app)
check("a question with no model shows the no-model line and Set up a model instead of Summarise",
      "this.noModel ? h(\"p\", { class: \"no-model-line\" }, M.NO_MODEL_LINE) : null" in app
      and "this.noModel ? setUp : summarise" in app and "ans.docOnly && !ans.noModel && ans.turn" in app
      and 'this.noModel = typeof done.no_model === "string"' in app and "no_model: m.no_model" in app)
check("Set up the AI sits on the Model page, between the Ollama row and the table",
      'id="ai-setup"' in pane and pane.find('id="ollama-text"') < pane.find('id="ai-setup"') < pane.find('id="model-table"'))
check("Set up the AI is drawn by models.js and runs through the API, asked again while it runs",
      "M.buildSetup(data, h, setupAction," in app and "await API.setup.start()" in app and "await API.setup.cancel()" in app
      and 'post("/api/ollama/setup")' in api and 'post("/api/ollama/setup/cancel")' in api)
check("Remove asks for confirmation before it deletes, through the API",
      "M.removeConfirm(row, lastModels && lastModels.rows)" in app
      and "if (!(await confirmDialog(c.title, c.lines, c.label))) return;" in app
      and app.find("confirmDialog(c.title") < app.find("API.models.remove(model)")
      and 'post("/api/models/remove", { model })' in api)
check("the engine's row is called the AI engine, and the app's own words don't name Ollama",
      "<span>AI engine</span>" in pane and "Ollama" not in index.replace("ollama-", "")
      and "Ollama isn't installed" not in app)
check("Download now and Resume on the Upgrade page also set up the AI, through the same setup call",
      "download: () => { if (!justSaved()) { upAct(\"download\"); aiWant(); } }" in app
      and "resume: () => { if (!justSaved()) { upAct(\"resume\"); aiWant(); } }" in app
      and "setupAi: () => { AI.started = true; aiWant(); }" in app
      and "const d = M.aiDecision(S.status, AI.data);" in app and "await API.setup.start();" in app)
check("the free library's download also sets up the AI, and its card says so",
      "aiWant();  // and the AI" in app and "ai: aiUi(\"first\")" in app)
check("the Upgrade page shows the AI's line and asks /api/models again only while something runs and the page is open",
      "ai: aiUi(UPG.state(UP.st, UP.ui))" in app and "!tick || aiActive() ? aiLoad() : null" in app
      and '(v.state === "downloading" || aiActive()) && settings.open && !pane.hidden' in app)
check("no price on the Model page", not re.search(r"[$€£]\s?\d|\bprice\b", pane + models, re.I))

node = shutil.which("node")
if not node:
    print("SKIP the model checks: node isn't on the PATH")
else:
    result = subprocess.run([node, os.path.join(HERE, "interface_models_check.js"), os.path.join(STATIC, "models.js")],
                            capture_output=True, text=True, timeout=120)
    print((result.stdout + result.stderr).strip())
    check("the model checks", result.returncode == 0)
if FAILS:
    print(f"FAIL {len(FAILS)} checks: {', '.join(FAILS)}")
    sys.exit(1)
print("all interface model checks passed")
