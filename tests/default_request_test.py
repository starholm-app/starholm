"""With every answer setting at its default (Gemma 4 26B, fetch-quoted, chosen 14 November 2026), the model gets
exactly the requests pinned in tests/fixtures/default-requests.json: the request bodies (messages, tools, think,
options, so the context of 32768 too) for two questions in one chat (each a search-words step, then the answer), on
a one-article library, with the date and the country fixed. This test runs the same two questions through this
checkout's server with a faked Ollama and compares what it would send, byte for byte. A change to the default
request fails it.

Usage: .venv/bin/python tests/default_request_test.py
       SURVIVAL_LLM_CHECKOUT=<another checkout> .venv/bin/python tests/default_request_test.py --write
       (--write stores what that checkout sends as the fixture: only when the default request is meant to change.)"""
import datetime
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHECKOUT = os.environ.get("SURVIVAL_LLM_CHECKOUT") or ROOT
FIXTURE = os.path.join(HERE, "fixtures", "default-requests.json")
TMP = tempfile.mkdtemp()
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "lib"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"))
os.environ.setdefault("SURVIVAL_LLM_DATA", os.path.join(TMP, "data"))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402  (tests/sandbox.py)
sandbox.require()
os.environ.pop("OLLAMA_HOST", None)
sys.path.insert(0, CHECKOUT)
sys.path.insert(0, os.path.join(CHECKOUT, "app"))

# ---- a one-article library ----
LIB = os.path.join(TMP, "lib")
os.makedirs(os.path.join(LIB, "text"))
PASSAGE = "Bring water to a rolling boil for at least one minute."
TEXT = ("# Water\n\n" + PASSAGE + "\n\n").encode()
db = sqlite3.connect(os.path.join(LIB, "index.sqlite"))
db.executescript("""
    CREATE TABLE sources(id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT, topic TEXT, origin TEXT, docs INT, done INT,
                         license TEXT);
    CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT UNIQUE);
    CREATE TABLE docs(id INTEGER PRIMARY KEY, source_id INT, title TEXT, url TEXT, file_id INT, offset INT, length INT);
    CREATE TABLE passages(id INTEGER PRIMARY KEY, doc_id INT, offset INT, length INT);
    CREATE VIRTUAL TABLE fts USING fts5(title, body, content='', contentless_delete=1,
                                        tokenize='porter unicode61 remove_diacritics 2');""")
db.execute("INSERT INTO sources VALUES (1, 'ready-gov', 'Ready.gov', 'emergency', '', 1, 1, '')")
db.execute("INSERT INTO files VALUES (1, 's.txt')")
db.execute("INSERT INTO passages VALUES (1000, 1, 9, ?)", (len(PASSAGE),))
db.execute("INSERT INTO fts(rowid, title, body) VALUES (1000, 'Water', ?)", (PASSAGE,))
db.execute("INSERT INTO docs VALUES (1, 1, 'Water', 'https://www.ready.gov/water', 1, 0, ?)", (len(TEXT),))
db.commit()
db.close()
with open(os.path.join(LIB, "text", "s.txt"), "wb") as f:
    f.write(TEXT)

import emergency  # noqa: E402

emergency.LOCALTIME = os.path.join(TMP, "no-localtime")  # no country detected: the same call line everywhere
emergency.GLOBAL_PREFS = os.path.join(TMP, "no-prefs.plist")
if sandbox.WINDOWS:  # the country is read from macOS files; on Windows nothing is detected
    emergency.detect = lambda: (None, None)
import core  # noqa: E402
import server  # noqa: E402


class FixedDate(datetime.date):
    @classmethod
    def today(cls):
        return datetime.date(2026, 11, 13)


# The instructions carry today's date: the server sees a fixed one.
server.datetime = types.SimpleNamespace(**{k: getattr(datetime, k) for k in dir(datetime) if not k.startswith("_")})
server.datetime.date = FixedDate
SENT = []


def fake_stream(model, messages, tools, think, options):
    SENT.append({"messages": [dict(m) for m in messages], "tools": tools, "think": think, "options": options})
    if options.get("num_predict") == core.SEARCH_WORDS_TOKENS:  # the step that writes search words
        yield {"message": {"content": "boil water"}}
    else:
        yield {"message": {"content": "- Boil water for one minute. [[P1: " + PASSAGE + "]]\n\nSources: Water"}}
    yield {"done": True, "eval_count": 3, "eval_duration": 1_000_000}


def no_ollama(*args, **kwargs):
    raise ConnectionRefusedError("no Ollama in this test")


core._stream = fake_stream
core.ollama_request = server.ollama_request = no_ollama
server.installed_models = lambda: ["gemma4:26b-a4b-it-qat"]
server.loaded_models = lambda: []
server.start_ollama = lambda: None
server.capabilities = lambda model: {"completion", "tools", "thinking"}
httpd = server.make_server(0)
server.PORT = httpd.server_address[1]
server.private_dir(server.APP_DIR)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def ask(question):
    conn = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=30)
    conn.request("POST", "/api/chat", json.dumps({"conversation": "a" * 32, "message": question,
                                                  "model": "gemma4:26b-a4b-it-qat"}), {"X-Token": server.TOKEN})
    conn.getresponse().read()
    conn.close()


ask("How long should I boil water?")
ask("And at altitude?")
got = json.dumps(SENT, sort_keys=True, indent=1, ensure_ascii=False)
if "--write" in sys.argv:
    with open(FIXTURE, "w", encoding="utf-8") as f:
        f.write(got + "\n")
    print(f"wrote {len(SENT)} requests from {CHECKOUT} to {FIXTURE}")
    sys.exit(0)
with open(FIXTURE, encoding="utf-8") as f:
    want = f.read().rstrip("\n")
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


check("the default settings send the four requests pinned in the fixture, byte for byte", got == want,
      next((f"first difference at character {i}: {got[i:i + 80]!r} against {want[i:i + 80]!r}"
            for i in range(min(len(got), len(want))) if got[i] != want[i]), f"lengths {len(got)} and {len(want)}"))
check("the fixture holds the two questions' four requests", len(json.loads(want)) == 4)
if FAILS:
    print(f"FAIL {len(FAILS)} checks: {', '.join(FAILS)}")
    sys.exit(1)
print("the default request is unchanged")
