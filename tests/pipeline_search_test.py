"""library/search.py: a crafted index cannot make the reader open a file outside the library's text folder
(absolute paths, "..", symlinks), and the library is found in the same order as app/server.py (the "library folder"
setting, then $SURVIVAL_LLM_LIBRARY, then the data folder's "current" build). Temporary folders only.

Usage: .venv/bin/python tests/pipeline_search_test.py"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="pipeline-search-")
os.environ["SURVIVAL_LLM_DATA"] = os.path.join(TMP, "data")
os.environ["SURVIVAL_LLM_APP_DIR"] = os.path.join(TMP, "app")
os.environ["SURVIVAL_LLM_LIBRARY"] = os.path.join(TMP, "no-library")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from library import search  # noqa: E402

FAILS = []
COUNT = [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


# ---- a tiny library whose index names shard paths we control ----
LIB = os.path.join(TMP, "lib")
TEXT = os.path.join(LIB, "text")
os.makedirs(os.path.join(TEXT, "src"))
with open(os.path.join(TEXT, "src", "0000.md"), "wb") as f:
    f.write(b"inside the library")
SECRET = os.path.join(TMP, "secret.txt")
with open(SECRET, "wb") as f:
    f.write(b"private notes")
os.symlink(SECRET, os.path.join(TEXT, "src", "link.md"))
os.symlink(TMP, os.path.join(TEXT, "escape"))  # a folder link out of the text folder
db = sqlite3.connect(os.path.join(LIB, "index.sqlite"))
db.executescript("CREATE TABLE sources(id INTEGER PRIMARY KEY, key TEXT, label TEXT, topic TEXT, docs INT, done INT);"
                 "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT);")
paths = {1: "src/0000.md", 2: SECRET, 3: "../secret.txt", 4: "src/../../secret.txt", 5: "src/link.md",
         6: "escape/secret.txt", 7: "", 8: "src"}
db.executemany("INSERT INTO files(id, path) VALUES(?, ?)", paths.items())
db.execute("INSERT INTO files(id, path) VALUES(9, NULL)")
db.commit()
db.close()

lib = search.Library(os.path.join(LIB, "index.sqlite"))
check("a shard inside the text folder is read", lib._read_bytes(1, 0, 6) == b"inside")
for file_id, label in [(2, "an absolute path"), (3, "a .. path"), (4, "a .. path inside a subfolder"),
                       (5, "a symlinked file pointing outside"), (6, "a path through a symlinked folder"),
                       (7, "an empty path"), (8, "a folder"), (9, "a NULL path"), (99, "a missing row")]:
    check(f"refused: {label}", lib._read_bytes(file_id, 0, 100) == b"", lib._read_bytes(file_id, 0, 100))
check("refused: a negative offset", lib._read_bytes(1, -5, 3) == b"")
check("safe_text_path keeps a text folder reached through a symlink",
      search.safe_text_path(os.path.join(TEXT, "..", "text"), "src/0000.md")
      == os.path.realpath(os.path.join(TEXT, "src", "0000.md")))
lib.db.close()

# ---- the order the library is found in ----
APP = os.path.join(TMP, "order-app")
os.makedirs(APP)
CHOSEN, FROM_ENV = os.path.join(TMP, "chosen"), os.path.join(TMP, "from-env")
DATA = os.path.join(TMP, "order-data")
os.makedirs(os.path.join(DATA, "libs", "lib-2026-11-15"))
os.symlink(os.path.join("libs", "lib-2026-11-15"), os.path.join(DATA, "current"))


def found(setting=None, env=None):
    """search.LIB as a fresh process sees it (it is worked out once, at import)."""
    with open(os.path.join(APP, "settings.json"), "w") as f:
        json.dump({"library_dir": setting} if setting else {}, f)
    child_env = {k: v for k, v in os.environ.items() if k != "SURVIVAL_LLM_LIBRARY"}
    child_env.update(SURVIVAL_LLM_APP_DIR=APP, SURVIVAL_LLM_DATA=DATA)
    if env:
        child_env["SURVIVAL_LLM_LIBRARY"] = env
    out = subprocess.run([sys.executable, "-c", "from library import search; print(search.LIB)"], cwd=ROOT,
                         env=child_env, capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


check("the setting wins over the variable", found(CHOSEN, FROM_ENV) == CHOSEN, found(CHOSEN, FROM_ENV))
check("the variable when there is no setting", found(None, FROM_ENV) == FROM_ENV, found(None, FROM_ENV))
check("else the build that current points at, resolved",
      found() == os.path.realpath(os.path.join(DATA, "libs", "lib-2026-11-15")), found())

shutil.rmtree(TMP, ignore_errors=True)
print(f"{COUNT[0] - len(FAILS)} of {COUNT[0]} checks passed" if FAILS else f"all {COUNT[0]} checks passed")
sys.exit(1 if FAILS else 0)
