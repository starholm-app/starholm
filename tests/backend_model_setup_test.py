"""The free extras: the recommended-model table (rows from recommended-models.json, the row for this Mac's memory
marked), a model download through Ollama's pull API with progress and a cancel that stops it, Ollama found on PATH,
the app starting and answering in library-only mode with no Ollama, the Library only switch with a model present, and
the person's own documents added, listed, found and removed through the API.

No real model is pulled, loaded or called: core.HOST points at a fake Ollama inside this test, every request the app
makes to "Ollama" is checked to go there, starting Ollama is replaced by a recorder, and the chat stream is faked.

Usage: .venv/bin/python tests/backend_model_setup_test.py"""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()
os.environ.pop("OLLAMA_HOST", None)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import emergency  # noqa: E402

emergency.LOCALTIME = os.path.join(TMP, "no-localtime")
emergency.GLOBAL_PREFS = os.path.join(TMP, "no-prefs.plist")
if sandbox.WINDOWS:  # the country is read from macOS files; on Windows nothing is detected
    emergency.detect = lambda: (None, None)
import core  # noqa: E402
import model_setup  # noqa: E402
import server  # noqa: E402

server.ANSWER_DEFAULTS["mode"] = "standard"  # one model request per answer; the default mode has its own tests
server.REFRESH_PLIST = os.path.join(TMP, "no.plist")
server.REFRESH_STATUS = os.path.join(TMP, "no-status.json")
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


# ---- a fake Ollama on a free port: its model list, and a pull that streams progress ----
FAKE = {"models": [], "seen": [], "pull_lines": 6, "pull_delay": 0.02, "closed": [], "digests": {}}


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FAKE["seen"].append(("GET", self.path, None))
        if self.path == "/api/tags":
            return self._json(200, {"models": [{"name": m, "digest": FAKE["digests"].get(m, "")} for m in FAKE["models"]]})
        if self.path == "/api/ps":
            return self._json(200, {"models": []})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FAKE["seen"].append(("POST", self.path, body))
        if self.path != "/api/pull":
            return self._json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        total = 1000
        try:
            self.wfile.write(b'{"status":"pulling manifest"}\n')
            for i in range(1, FAKE["pull_lines"] + 1):
                time.sleep(FAKE["pull_delay"])
                line = {"status": "pulling abc", "digest": "sha256:abc", "total": total,
                        "completed": total * i // FAKE["pull_lines"]}
                self.wfile.write(json.dumps(line).encode() + b"\n")
                self.wfile.flush()
            self.wfile.write(b'{"status":"verifying sha256 digest"}\n{"status":"success"}\n')
            self.wfile.flush()
            FAKE["models"].append(body["model"])
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            FAKE["closed"].append(body["model"])  # the app closed the request: Ollama stops downloading


fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
fake.daemon_threads = True
threading.Thread(target=fake.serve_forever, daemon=True).start()
FAKE_HOST = f"http://127.0.0.1:{fake.server_address[1]}"
core.HOST = FAKE_HOST  # every Ollama request of the app goes through core.ollama_request, which reads HOST
real_request = core.ollama_request
OUTSIDE = []  # any Ollama request that would have gone somewhere other than the fake


def guarded_request(path, body=None, timeout=900):
    if core.HOST != FAKE_HOST:
        OUTSIDE.append(path)
        raise ConnectionRefusedError("refused: not the fake Ollama")
    return real_request(path, body, timeout)


core.ollama_request = server.ollama_request = guarded_request
REGISTRY_ASKED = []  # the update check never reaches the real Ollama registry in this test


def registry_blocked(host, port):
    REGISTRY_ASKED.append(host)
    raise ConnectionRefusedError("refused: not the fake registry")


server.UPDATES.connect = registry_blocked
STARTED = []
REAL_START = server.start_ollama
server.start_ollama = lambda: STARTED.append(1)  # never runs a real Ollama
server.capabilities = lambda model: {"completion"}
STREAMS = []


def fake_stream(model, messages, tools, think, options):  # the model's answer; nothing reaches Ollama's /api/chat
    STREAMS.append(model)
    yield {"message": {"content": "Boil it for one minute."}}
    yield {"done": True, "eval_count": 5, "eval_duration": 1_000_000}


core._stream = fake_stream
server.memory_bytes = lambda: 16 * 2 ** 30  # this "Mac" has 16 GB

# ---- a small library ----
LIB = os.path.join(TMP, "lib")
os.makedirs(os.path.join(LIB, "text"))
BOIL = "Bring water to a rolling boil for at least one minute. Let it cool before you drink it."
db = sqlite3.connect(os.path.join(LIB, "index.sqlite"))
db.executescript("""
    CREATE TABLE sources(id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT, topic TEXT, origin TEXT,
                         docs INT DEFAULT 0, done INT DEFAULT 0, license TEXT);
    CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT UNIQUE);
    CREATE TABLE docs(id INTEGER PRIMARY KEY, source_id INT, title TEXT, url TEXT, file_id INT, offset INT, length INT);
    CREATE INDEX docs_source_title ON docs(source_id, title COLLATE NOCASE);
    CREATE TABLE passages(id INTEGER PRIMARY KEY, doc_id INT, offset INT, length INT);
    CREATE VIRTUAL TABLE fts USING fts5(title, body, content='', contentless_delete=1,
                                        tokenize='porter unicode61 remove_diacritics 2');
""")
db.execute("INSERT INTO sources VALUES (1, 'ready-gov', 'Ready.gov', 'emergency', '', 1, 1, '')")
db.execute("INSERT INTO files VALUES (1, 'shard-0.txt')")
shard = b"# Water treatment\n\n"
db.execute("INSERT INTO passages VALUES (1000, 1, ?, ?)", (len(shard), len(BOIL.encode())))
db.execute("INSERT INTO fts(rowid, title, body) VALUES (1000, 'Water treatment', ?)", (BOIL,))
shard += BOIL.encode() + b"\n\n"
db.execute("INSERT INTO docs VALUES (1, 1, 'Water treatment', 'https://www.ready.gov/water', 1, 0, ?)", (len(shard),))
db.commit()
db.close()
with open(os.path.join(LIB, "text", "shard-0.txt"), "wb") as f:
    f.write(shard)
server.LIB, server.DB_PATH = LIB, os.path.join(LIB, "index.sqlite")
server.lib_version[0] += 1

# ---- the app on a free port ----
httpd = server.make_server(0)
server.httpd = httpd
server.PORT = httpd.server_address[1]
server.private_dir(server.APP_DIR)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def request(method, path, body=None, raw=None, token=True, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=30)
    headers = {**({"X-Token": server.TOKEN} if token else {}), **(headers or {})}
    if body is not None:
        raw = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=raw, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def jget(path):
    code, data = request("GET", path)
    return code, json.loads(data or b"null")


def jpost(path, body=None):
    code, data = request("POST", path, body if body is not None else {})
    return code, json.loads(data or b"null")


def ask(message, chat_id=None):
    body = {"conversation": chat_id or os.urandom(16).hex(), "message": message}
    code, data = request("POST", "/api/chat", body)
    return code, [json.loads(line[6:]) for line in data.decode().split("\n") if line.startswith("data: ")]


def wait_for(fn, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


ROWS = model_setup.load_rows()
SMALL, MEDIUM, LARGE = (r["model"] for r in ROWS)

# ---- 1. the table: the rows of the data file, the row for 16 GB marked ----
with open(os.path.join(ROOT, "recommended-models.json"), encoding="utf-8") as f:
    data_rows = json.load(f)["rows"]
code, table = jget("/api/models")
check("the model table answers", code == 200, table)
check("its rows are the data file's rows, in memory order",
      [(r["memory_gb"], r["model"], r["download_gb"], r["tested"], r["available"]) for r in table["rows"]]
      == [(r["memory_gb"], r["model"], r["download_gb"], r.get("tested") is True, r.get("available") is not False)
          for r in sorted(data_rows, key=lambda r: r["memory_gb"])], table["rows"])
check("the machine's memory is the injected 16 GB", table["memory_gb"] == 16, table["memory_gb"])
check("the 16 GB row is the one marked as recommended",
      [r["model"] for r in table["rows"] if r["recommended"]] == [MEDIUM], table["rows"])
check("a row needing more memory than the Mac has is too large; every other row can be downloaded",
      {r["model"]: r["state"] for r in table["rows"]} == {SMALL: "not_installed", MEDIUM: "not_installed",
                                                          LARGE: "too_large"}, table["rows"])
check("every row in the data file is listed and downloadable: no placeholder rows",
      [r["available"] for r in ROWS] == [True, True, True], ROWS)
check("Ollama is running (the fake answers)", table["ollama"] == "running", table["ollama"])
server.memory_bytes = lambda: 64 * 2 ** 30
check("with 64 GB the largest row is recommended and nothing is too large",
      [(r["recommended"], r["state"]) for r in jget("/api/models")[1]["rows"]]
      == [(False, "not_installed"), (False, "not_installed"), (True, "not_installed")])
server.memory_bytes = lambda: 16 * 2 ** 30
check("rows come only from the data file: a damaged file gives none",
      model_setup.load_rows(os.path.join(TMP, "none.json")) == [])

# A row a data file still marks "available": false stays a placeholder with no download.
PLACEHOLDER_ROWS = os.path.join(TMP, "placeholder-rows.json")
with open(PLACEHOLDER_ROWS, "w", encoding="utf-8") as f:
    json.dump({"rows": [{**r, "available": r["model"] != SMALL} for r in data_rows]}, f)
model_setup.ROWS_FILE = PLACEHOLDER_ROWS
code, out = jpost("/api/models/pull", {"model": SMALL})
check("a row marked not available can't be downloaded, with a plain reason",
      code == 400 and "still being tested" in out.get("error", "") and not [s for s in FAKE["seen"] if s[1] == "/api/pull"],
      (code, out))

# From here, a data file of the test's own with every row available, so the downloads below can run.
TEST_ROWS = os.path.join(TMP, "rows.json")
with open(TEST_ROWS, "w", encoding="utf-8") as f:
    json.dump({"rows": [{**r, "available": True} for r in data_rows]}, f)
model_setup.ROWS_FILE = TEST_ROWS
check("the test's own rows are all available", all(r["available"] for r in model_setup.load_rows()))

# ---- 2. a download through the fake Ollama's pull API, with progress ----
check("a model that isn't in the table can't be downloaded", jpost("/api/models/pull", {"model": "other:1b"})[0] == 400)
check("a row too large for this Mac can't be downloaded", jpost("/api/models/pull", {"model": LARGE})[0] == 400)
FAKE["pull_delay"] = 0.15
code, out = jpost("/api/models/pull", {"model": MEDIUM})
check("a download starts", code == 200 and out["ok"], out)
check("the pull went to Ollama's /api/pull with the model name, streamed",
      wait_for(lambda: ("POST", "/api/pull", {"model": MEDIUM, "stream": True}) in FAKE["seen"]), FAKE["seen"])
check("a second download waits for the first", jpost("/api/models/pull", {"model": SMALL})[0] == 409)
seen_progress = wait_for(lambda: (lambda r: r["state"] == "downloading" and r["download"]["total"] == 1000
                                  and 0 < r["download"]["completed"] < 1000)(jget("/api/models")[1]["rows"][1]))
check("while it downloads, its row shows the state and progress", seen_progress, jget("/api/models")[1]["rows"][1])
check("it finishes and the row is the model in use (the chosen one wasn't installed)",
      wait_for(lambda: jget("/api/models")[1]["rows"][1]["state"] == "in_use"), jget("/api/models")[1]["rows"][1])
check("the download is recorded as done with full progress",
      (lambda d: d["state"] == "done" and d["completed"] == d["total"] == 1000)(jget("/api/models")[1]["rows"][1]["download"]))
check("answers now come from the downloaded model", core.chosen_model() == MEDIUM, core.chosen_model())

# ---- 3. a cancel that stops a download ----
FAKE["pull_lines"], FAKE["pull_delay"] = 200, 0.05
code, out = jpost("/api/models/pull", {"model": SMALL})
check("a second model starts downloading", code == 200, out)
wait_for(lambda: (jget("/api/models")[1]["rows"][0]["download"] or {}).get("completed", 0) > 0)
code, out = jpost("/api/models/cancel", {"model": SMALL})
check("Cancel answers with the row cancelled", code == 200 and out["models"]["rows"][0]["download"]["state"] == "cancelled"
      and out["models"]["rows"][0]["state"] == "not_installed", out)
check("Cancel closed the request, so the fake Ollama stopped sending", wait_for(lambda: SMALL in FAKE["closed"]),
      FAKE["closed"])
check("the cancelled model was never installed", SMALL not in FAKE["models"])
check("a cancel with nothing downloading: 409", jpost("/api/models/cancel", {"model": SMALL})[0] == 409)
FAKE["pull_lines"], FAKE["pull_delay"] = 6, 0.02

# ---- 4. with a model, a question gets a written answer; with Library only on, none ----
code, events = ask("How long should I boil water?")
check("with the model installed, the question is answered by it",
      code == 200 and STREAMS == [MEDIUM] and any(e["type"] == "token" for e in events), [e["type"] for e in events])
STREAMS.clear()
before = len(FAKE["seen"])
check("the Library only switch turns on", jpost("/api/answers", {"documents_only": True})[1]["documents_only"] is True)
code, events = ask("How long should I boil water?")
done = next((e for e in events if e["type"] == "done"), {})
check("Library only with a model present: the library's passages and no model",
      code == 200 and STREAMS == [] and done.get("documents_only") is True and done.get("answer") == ""
      and any(e["type"] == "documents" and e["documents"] and e["documents"][0]["passage"] == BOIL for e in events),
      [e["type"] for e in events])
check("Library only: Ollama isn't asked anything for the question", FAKE["seen"][before:] == [], FAKE["seen"][before:])
check("Library only on purpose isn't the no-model case", "no_model" not in done, done)
code, status = jget("/api/status")
check("the status says library-only mode is on by setting", status["library_only"] == "setting", status["library_only"])
check("the model table shows the switch", jget("/api/models")[1]["library_only"] is True)
jpost("/api/answers", {"documents_only": False})

# ---- 5. the chosen model not downloaded: library-only with the line ----
FAKE["models"].clear()
code, events = ask("How long should I boil water?")
done = next((e for e in events if e["type"] == "done"), {})
check("with the model not downloaded, a question gives passages, sources and the no-model line",
      code == 200 and STREAMS == [] and done.get("no_model") == "not_installed" and done.get("note") == server.NO_MODEL_LINE
      and done["documents"] and done["documents"][0]["source"] and done["documents"][0]["url"], done)
check("the line says written answers need a model and where to set one up",
      server.NO_MODEL_LINE == "Written answers need a model. Set one up in Settings › Model.")
check("the status names why", jget("/api/status")[1]["library_only"] == "not_installed")

# ---- 6. Ollama found through PATH; absent, the app starts and works in library-only mode ----
missing = os.path.join(TMP, "nowhere", "ollama")
if not sandbox.WINDOWS:  # PATH is not searched on Windows, and it has no group or other write bits
    tools = os.path.join(TMP, "bin")
    os.makedirs(tools)
    exe = os.path.join(tools, "ollama")
    with open(exe, "w") as f:
        f.write("#!/bin/sh\nexit 1\n")  # never run: only found
    os.chmod(exe, 0o755)
    check("Ollama is found on PATH when it isn't in the usual places",
          server.find_ollama((missing,), search_path=os.pathsep.join(["relative/bin", tools])) == exe)
    check("a relative PATH folder is never used",
          server.find_ollama((missing,), search_path="bin") == missing)
    inside = os.path.join(ROOT, "app")
    check("a PATH folder inside the app's own folder is never used",
          not any(c.startswith(ROOT + os.sep) for c in server.path_candidates(inside)))
    check("the usual install places come before PATH", server.find_ollama((exe,), search_path=tools) == exe)
    shared = os.path.join(TMP, "shared-bin")
    os.makedirs(shared)
    os.chmod(shared, 0o775)  # writable by the group
    with open(os.path.join(shared, "ollama"), "w") as f:
        f.write("#!/bin/sh\nexit 1\n")
    os.chmod(os.path.join(shared, "ollama"), 0o755)
    check("a PATH folder writable by group or others is skipped",
          server.path_candidates(shared) == () and server.find_ollama((missing,), search_path=shared) == missing)
    loose = os.path.join(TMP, "loose-bin")
    os.makedirs(loose)
    os.chmod(loose, 0o755)
    with open(os.path.join(loose, "ollama"), "w") as f:
        f.write("#!/bin/sh\nexit 1\n")
    os.chmod(os.path.join(loose, "ollama"), 0o757)  # the program itself writable by others
    check("an ollama writable by group or others is skipped",
          server.path_candidates(loose) == () and server.find_ollama((missing,), search_path=loose) == missing)
    check("a folder and program writable only by their owner are used", server.path_candidates(tools) == (exe,))

# ---- the folder picker off macOS: a clear message (the platform injected) ----
real_platform = server.PLATFORM
server.PLATFORM = "linux"
code, out = jpost("/api/pick-folder", {})
check("off macOS the folder picker answers 501 with a clear message",
      code == 501 and out.get("error") == "The folder picker isn't available here. Type the folder's full path instead.",
      (code, out))
server.PLATFORM = real_platform

real_find = server.find_ollama
server.find_ollama = lambda *a, **k: missing
server.OLLAMA_BIN = missing
core.HOST = "http://127.0.0.1:9"  # nothing answers: no Ollama at all (the guard refuses it before any connection)
popen = []
real_popen = server.subprocess.Popen
server.subprocess.Popen = lambda *a, **k: popen.append(a)
server.start_ollama, server.ollama_started_at[0] = REAL_START, 0  # the real one, with Popen replaced
server.start_ollama_if_needed()
check("with no Ollama, starting the app runs nothing", popen == [], popen)
server.start_ollama = lambda: STARTED.append(1)
code, page = request("GET", "/", token=False,
                     headers={"Cookie": f"{server.session_cookie()}={server.new_session()}"})  # as after /launch
check("the page loads", code == 200 and b"<html" in page.lower(), code)
code, status = jget("/api/status")
check("the status works and says library-only: no Ollama",
      code == 200 and status["library_only"] == "no_ollama" and status["ollama"] is False, status.get("library_only"))
code, table = jget("/api/models")
check("the model table says Ollama is missing", code == 200 and table["ollama"] == "missing", table.get("ollama"))
code, found = jget("/api/search?q=" + quote("boil water"))
check("search works", code == 200 and found["results"] and found["results"][0]["title"] == "Water treatment", found)
code, guides = jget("/api/guides")
check("the guides list works", code == 200 and isinstance(guides["guides"], list), guides)
free = [g for g in guides["guides"] if not g.get("locked")]  # without an upgrade key, the free guides
if free:
    slug = free[0]["slug"]
    check("a guide opens", jget(f"/api/guides/{slug}")[0] == 200)
code, events = ask("How long should I boil water?")
done = next((e for e in events if e["type"] == "done"), {})
check("a question returns passages with the no-model line and no error",
      code == 200 and not any(e["type"] == "error" for e in events) and done.get("no_model") == "no_ollama"
      and done.get("note") == server.NO_MODEL_LINE and BOIL in [d["passage"] for e in events if e["type"] == "documents"
                                                                  for d in e["documents"]], events)
check("downloading needs Ollama", jpost("/api/models/pull", {"model": SMALL})[0] == 503)
server.find_ollama = real_find
server.subprocess.Popen = real_popen
core.HOST = FAKE_HOST

# ---- 7. your own documents: added, listed, found in search and answers, removed ----
server.to_trash = os.remove  # the test's own file, never the real Trash
NOTE = b"Our family meeting point is the blue water tower on Cedar Hill."
code, data = request("POST", "/api/myfiles/add?name=meeting-point.txt", raw=NOTE)
check("a document is added", code == 200, data)
listed = lambda: [i for i in jget("/api/myfiles")[1]["items"] if i["name"] == "meeting-point.txt"]  # noqa: E731
check("it is listed and read", wait_for(lambda: listed() and listed()[0]["status"] == "ready"), listed())
code, found = jget("/api/search?q=" + quote("blue water tower"))
check("search finds the added document", code == 200 and any("meeting" in json.dumps(r).lower()
                                                               for r in found["results"]), found)
FAKE["models"].append(MEDIUM)
code, events = ask("Where is our family meeting point at the water tower?")
early = next((e for e in events if e["type"] == "documents"), {"documents": []})
check("an answer is handed the added document", any("Cedar Hill" in d["passage"] for d in early["documents"]),
      early["documents"])
code, out = jpost("/api/myfiles/remove", {"name": "meeting-point.txt"})
check("it is removed", code == 200 and wait_for(lambda: not listed()), out)
check("search no longer finds it", wait_for(lambda: not any(
    "meeting" in json.dumps(r).lower() for r in jget("/api/search?q=" + quote("blue water tower"))[1]["results"])))

# ---- Update available: the Ollama registry is a fake on a local socket ----
REG = {"digest": None, "seen": []}


class FakeRegistry(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        REG["seen"].append((self.headers.get("Host"), self.path, self.headers.get("Accept")))
        if not REG["digest"]:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"schemaVersion": 2}'
        self.send_response(200)
        self.send_header("Docker-Content-Digest", "sha256:" + REG["digest"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


registry = ThreadingHTTPServer(("127.0.0.1", 0), FakeRegistry)
registry.daemon_threads = True
threading.Thread(target=registry.serve_forever, daemon=True).start()


def registry_connect(host, port):
    if host != model_setup.REGISTRY:
        raise ConnectionRefusedError(f"the update check asked {host}")
    return http.client.HTTPConnection("127.0.0.1", registry.server_address[1], timeout=5)


def medium_row(updates):
    """/api/models with a fresh update check (its thread waited for), and MEDIUM's row."""
    server.UPDATES = updates
    jget("/api/models")
    if updates.thread:
        updates.thread.join(10)
    code, data = jget("/api/models")
    return code, data, next(r for r in data["rows"] if r["model"] == MEDIUM)


LOCAL = "a" * 64
FAKE["digests"][MEDIUM] = LOCAL
REG["digest"] = "b" * 64
code, data, row = medium_row(model_setup.Updates(connect=registry_connect))
check("a newer version in the registry: the installed row says update", code == 200 and row["update"] is True, row)
check("the registry was asked for that model's manifest, by name and tag",
      REG["seen"] and REG["seen"][-1][0] == model_setup.REGISTRY
      and REG["seen"][-1][1] == model_setup.manifest_path(MEDIUM) and "manifest.v2" in REG["seen"][-1][2], REG["seen"])
check("only installed models in the table were asked about",
      {p for _, p, _ in REG["seen"]} == {model_setup.manifest_path(MEDIUM)}, REG["seen"])
REG["digest"] = LOCAL
code, data, row = medium_row(model_setup.Updates(connect=registry_connect))
check("the same version: no update shown", code == 200 and row["update"] is False, row)


def offline(host, port):
    raise OSError("nodename nor servname provided, or not known")


code, data, row = medium_row(model_setup.Updates(connect=offline))
check("offline: no update shown and no error reaches the page",
      code == 200 and row["update"] is False and "error" not in data, (code, data))
REG["digest"] = None
code, data, row = medium_row(model_setup.Updates(connect=registry_connect))
check("a registry that doesn't know the model: no update, no error", code == 200 and row["update"] is False, row)
check("a model name on another host is never checked",
      model_setup.manifest_path("hf.co/someone/model:q4") is None and model_setup.manifest_path("gemma4") == "/v2/library/gemma4/manifests/latest")
server.UPDATES = model_setup.Updates(connect=registry_blocked)
registry.shutdown()

# ---- nothing went anywhere but the fake ----
check("with no Ollama, only its model list was asked for, and refused before any connection",
      set(OUTSIDE) <= {"/api/tags", "/api/ps"}, OUTSIDE)
check("the update check never reached the real registry (blocked before any connection)",
      set(REGISTRY_ASKED) <= {model_setup.REGISTRY}, REGISTRY_ASKED)
check("no request reached Ollama's chat or generate (the model was never called or loaded)",
      not [s for s in FAKE["seen"] if s[1] in ("/api/chat", "/api/generate")], FAKE["seen"])

httpd.shutdown()
fake.shutdown()
print(f"{len(FAILS)} failed" if FAILS else "all model setup checks passed")
sys.exit(1 if FAILS else 0)
