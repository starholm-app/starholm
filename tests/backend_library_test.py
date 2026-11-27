"""The Library page's endpoints over library/browse.py, on a real local socket: sources, info, browse with keyset
paging, letters, source= and official=1 on /api/search (passages and titles), opening a title at its first passage
through /api/article?doc=, and random articles by category; the request checks (token, bad and capped parameters);
calm answers on a library built before the reading table and the passages(doc_id) index; and a 404 with a plain
message for a document whose text file is gone. The library is a small index built here in a temporary folder. No
network, no Ollama, no real data.

Usage: .venv/bin/python tests/backend_library_test.py"""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading

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
import server  # noqa: E402

server.REFRESH_PLIST = os.path.join(TMP, "no.plist")
server.REFRESH_STATUS = os.path.join(TMP, "no-status.json")
server.installed_models = lambda: []
server.loaded_models = lambda: []
server.start_ollama = lambda: None
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)


# ---- a small library: three sources, a text shard, passages, full-text index, reading categories ----
SOURCES = [("wikipedia", "Wikipedia", "encyclopedia", "CC BY-SA 4.0"), ("ready-gov", "Ready.gov", "emergency", "Public domain"),
           ("medlineplus", "MedlinePlus (NIH)", "medical", "Public domain")]
DOCS = [("wikipedia", "Ada Lovelace", "people"), ("wikipedia", "Akron, Indiana", "places"),
        ("wikipedia", "Battle of Hastings", "history"), ("wikipedia", "Borane", "science"),
        ("wikipedia", "Black currawong", "nature"), ("wikipedia", "Mango", None),
        ("wikipedia", "1066 in England", None), ("ready-gov", "Water storage", None),
        ("ready-gov", "Generator safety", None), ("medlineplus", "Malaria", None)]
DOCS += [("wikipedia", f"Zeta topic {n:02d}", None) for n in range(60)]


def build(folder, upgraded=True):
    os.makedirs(os.path.join(folder, "text"))
    db = sqlite3.connect(os.path.join(folder, "index.sqlite"))
    db.executescript("""
        CREATE TABLE sources(id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT, topic TEXT, origin TEXT,
                             docs INT DEFAULT 0, done INT DEFAULT 0, license TEXT);
        CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT UNIQUE);
        CREATE TABLE docs(id INTEGER PRIMARY KEY, source_id INT, title TEXT, url TEXT, file_id INT, offset INT,
                          length INT);
        CREATE INDEX docs_title ON docs(title COLLATE NOCASE);
        CREATE TABLE passages(id INTEGER PRIMARY KEY, doc_id INT, offset INT, length INT);
        CREATE VIRTUAL TABLE fts USING fts5(title, body, content='', contentless_delete=1,
                                            tokenize='porter unicode61 remove_diacritics 2');
    """)
    if upgraded:
        db.executescript("""
            CREATE INDEX docs_source_title ON docs(source_id, title COLLATE NOCASE);
            CREATE INDEX passages_doc ON passages(doc_id);
            CREATE TABLE reading(category TEXT, doc_id INT, PRIMARY KEY(category, doc_id)) WITHOUT ROWID;
            CREATE INDEX reading_doc ON reading(doc_id);
        """)
    ids = {}
    for i, (key, label, topic, licence) in enumerate(SOURCES, 1):
        db.execute("INSERT INTO sources VALUES (?, ?, ?, ?, '', ?, 1, ?)",
                   (i, key, label, topic, sum(d[0] == key for d in DOCS), licence))
        ids[key] = i
    db.execute("INSERT INTO files VALUES (1, 'shard-0.txt')")
    shard, pid = b"", 1000
    for doc_id, (key, title, category) in enumerate(DOCS, 1):
        head = f"# {title}\nSource: {key}\n\n".encode()
        parts = [f"{title} first passage about {title.lower()} and boiling water.".encode(),
                 f"Second passage of {title}.".encode()]
        start = len(shard)
        shard += head
        for part in parts:
            db.execute("INSERT INTO passages VALUES (?, ?, ?, ?)", (pid, doc_id, len(shard), len(part)))
            db.execute("INSERT INTO fts(rowid, title, body) VALUES (?, ?, ?)", (pid, title, part.decode()))
            shard += part + b"\n\n"
            pid += 1
        db.execute("INSERT INTO docs VALUES (?, ?, ?, '', 1, ?, ?)", (doc_id, ids[key], title, start,
                                                                       len(shard) - start))
        if upgraded and key == "wikipedia":
            db.execute("INSERT INTO reading VALUES (?, ?)", (category or "other", doc_id))
    db.commit()
    db.close()
    with open(os.path.join(folder, "text", "shard-0.txt"), "wb") as f:
        f.write(shard)
    with open(os.path.join(folder, "build.json"), "w") as f:
        json.dump({"built": "2026-11-12T08:00:00Z", "changed": ["medlineplus"], "sources": {}}, f)


NEW, OLD = os.path.join(TMP, "lib-new"), os.path.join(TMP, "lib-old")
build(NEW)
build(OLD, upgraded=False)


def use(folder):
    server.LIB, server.DB_PATH = folder, os.path.join(folder, "index.sqlite")
    server.lib_version[0] += 1  # every server thread reopens it


use(NEW)
httpd = server.make_server(0)
server.PORT = httpd.server_address[1]
server.private_dir(server.APP_DIR)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def jget(path, token=True):
    conn = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=30)
    conn.request("GET", path, headers={"X-Token": server.TOKEN} if token else {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data or b"null")


# ---- request checks ----
for path in ("/api/library/sources", "/api/library/info", "/api/library/browse?source=wikipedia",
             "/api/library/letters?source=wikipedia", "/api/library/random", "/api/article?doc=1",
             "/api/search?q=water&titles=1"):
    check(f"no token: 403 on {path}", jget(path, token=False)[0] == 403)

# ---- sources and info ----
code, got = jget("/api/library/sources")
keys = [s["key"] for s in got["sources"]]
check("sources: every source, largest first", code == 200 and keys == ["wikipedia", "ready-gov", "medlineplus"], got)
wiki = got["sources"][0]
check("sources: the facts the page shows", wiki["name"] == "Wikipedia" and wiki["topic"] == "Encyclopedia and travel"
      and wiki["articles"] == 67 and wiki["licence"] == "CC BY-SA 4.0" and wiki["official"] is False
      and got["sources"][1]["official"] is True, wiki)
check("sources: updated by the last refresh", [s["updated"] for s in got["sources"]] == [False, False, True])
code, got = jget("/api/library/info")
check("info: articles, sources, size, build time, changed", code == 200 and got["articles"] == len(DOCS)
      and got["sources"] == 3 and got["size_bytes"] > 0 and got["built"] == "2026-11-12T08:00:00Z"
      and got["changed"] == ["medlineplus"], got)

status = jget("/api/status")[1]
check("status and info give the same library size (one function, bytes on disk)",
      status["library"]["size_bytes"] == got["size_bytes"], (status["library"], got["size_bytes"]))

# ---- browse with keyset paging ----
code, page = jget("/api/library/browse?source=wikipedia&limit=5")
check("browse: the first page, A to Z", code == 200 and [t["title"] for t in page["titles"]] ==
      ["1066 in England", "Ada Lovelace", "Akron, Indiana", "Battle of Hastings", "Black currawong"], page)
nxt = page["next"]
code, page2 = jget(f"/api/library/browse?source=wikipedia&limit=5&after={nxt['after'].replace(' ', '%20')}"
                   f"&after_id={nxt['after_id']}")
check("browse: the next page carries on after the last title", code == 200 and
      [t["title"] for t in page2["titles"]][:2] == ["Borane", "Mango"], page2)
code, page = jget("/api/library/browse?source=wikipedia&after=Z&limit=200")
check("browse: after=Z jumps to the letter, and the last page has no next", code == 200
      and len(page["titles"]) == 60 and page["next"] is None, (len(page["titles"]), page["next"]))
code, page = jget("/api/library/browse?source=wikipedia&limit=100000")
check("browse: limit is capped at 200", code == 200 and len(page["titles"]) == len(DOCS) - 3, len(page["titles"]))
code, page = jget("/api/library/browse?source=wikipedia&limit=0")
check("browse: limit is at least 1", code == 200 and len(page["titles"]) == 1)
for bad, want in (("", 400), ("?source=Wiki pedia", 400), ("?source=nope", 404), ("?source=wikipedia&limit=x", 400),
                  ("?source=wikipedia&after_id=5", 400), ("?source=wikipedia&after=A&after_id=-1", 400),
                  ("?source=wikipedia,ready-gov", 400), ("?source=wikipedia&after=" + "a" * 301, 400)):
    check(f"browse{bad[:40]}: {want}", jget("/api/library/browse" + bad.replace(" ", "%20"))[0] == want)

# ---- letters ----
code, got = jget("/api/library/letters?source=wikipedia")
check("letters: # first (a title starts with a digit), then the letters with titles", code == 200
      and got["source"] == "wikipedia" and got["letters"] == ["#", "A", "B", "M", "Z"], got)
code, page = jget("/api/library/browse?source=wikipedia&after=%23&limit=2")
check("browse: after=# starts at the titles under #, and each title says its letter", code == 200
      and [(t["title"], t["letter"]) for t in page["titles"]] == [("1066 in England", "#"), ("Ada Lovelace", "A")],
      page)
check("letters: unknown source 404, none 400", jget("/api/library/letters?source=nope")[0] == 404
      and jget("/api/library/letters")[0] == 400)

# ---- search with source= and official=1 ----
code, got = jget("/api/search?q=boiling%20water")
check("search: passages from every source by default", code == 200 and {r["source"] for r in got["results"]} >=
      {"Wikipedia", "Ready.gov", "MedlinePlus (NIH)"}, got)
code, got = jget("/api/search?q=boiling%20water&source=ready-gov")
check("search: source= keeps one source", code == 200 and got["results"]
      and {r["source"] for r in got["results"]} == {"Ready.gov"}, got)
code, got = jget("/api/search?q=boiling%20water&official=1")
check("search: official=1 keeps official sources only", code == 200 and got["results"]
      and all(r["official"] for r in got["results"]) and "Wikipedia" not in {r["source"] for r in got["results"]}, got)
code, got = jget("/api/search?q=ma&titles=1")
check("title search: titles starting with the text", code == 200 and [r["title"] for r in got["results"]] ==
      ["Malaria", "Mango"], got)
code, got = jget("/api/search?q=ma&titles=1&source=wikipedia")
check("title search: source=", [r["title"] for r in got["results"]] == ["Mango"], got)
code, got = jget("/api/search?q=ma&titles=1&official=1")
check("title search: official=1", [r["title"] for r in got["results"]] == ["Malaria"], got)
code, got = jget("/api/search?q=zeta&titles=1&limit=7")
check("title search: limit", len(got["results"]) == 7, len(got["results"]))
check("search: an unknown or malformed source is refused", jget("/api/search?q=x&source=nope")[0] == 400
      and jget("/api/search?q=x&source=a;b")[0] == 400 and jget("/api/search?q=x&titles=1&limit=x")[0] == 400)
check("search: an empty query gives nothing", jget("/api/search?q=&titles=1")[1] == {"results": []})

# ---- opening a title at its first passage ----
doc_id = next(t["doc_id"] for t in jget("/api/library/browse?source=medlineplus")[1]["titles"])
code, got = jget(f"/api/article?doc={doc_id}")
check("article?doc=: the whole article, focused on its first passage", code == 200 and got["title"] == "Malaria"
      and got["focus"] == got["parts"][0]["id"] and len(got["parts"]) == 2, got)
check("article?doc=: unknown 404, bad 400", jget("/api/article?doc=99999")[0] == 404
      and jget("/api/article?doc=0")[0] == 400 and jget("/api/article?doc=abc")[0] == 400)
check("article?id= still opens a passage", jget(f"/api/article?id={got['parts'][1]['id']}")[1]["focus"]
      == got["parts"][1]["id"])

# ---- random articles ----
code, got = jget("/api/library/random")
check("random: one article from the reading sources", code == 200 and len(got["articles"]) == 1
      and got["articles"][0]["source"] == "wikipedia" and got["articles"][0]["passage_id"] >= 1000, got)
code, got = jget("/api/library/random?category=history")
check("random: by category", code == 200 and [a["title"] for a in got["articles"]] == ["Battle of Hastings"], got)
code, got = jget("/api/library/random?count=500")
check("random: count is capped at 50 (and at what there is)", code == 200 and 1 <= len(got["articles"]) <= 50
      and len({a["doc_id"] for a in got["articles"]}) == len(got["articles"]), len(got["articles"]))
check("random: an unknown category or bad count is refused", jget("/api/library/random?category=sport")[0] == 400
      and jget("/api/library/random?count=two")[0] == 400)

# ---- an open article's place: breadcrumb (source, category) and "More in this category" ----
db = sqlite3.connect(os.path.join(NEW, "index.sqlite"))
by_title = {t: i for i, t in db.execute("SELECT id, title FROM docs")}
first_pid = {d: p for d, p in db.execute("SELECT doc_id, MIN(id) FROM passages GROUP BY doc_id")}
history = [by_title[f"Zeta topic {n:02d}"] for n in range(10)]  # nine more history articles, after Hastings by id
db.executemany("INSERT INTO reading VALUES ('history', ?)", [(i,) for i in history])
db.commit()
server.lib_version[0] += 1
hastings = by_title["Battle of Hastings"]
code, got = jget(f"/api/library/place?id={first_pid[hastings]}")
place = got.get("place") or {}
check("place: a Wikipedia article's source and category for the breadcrumb", code == 200 and place.get("doc_id") == hastings
      and place.get("source") == "wikipedia" and place.get("source_name") == "Wikipedia"
      and place.get("category") == "history" and place.get("category_name") == "History", got)
check(f"place: More in this category is the next {server.RELATED_COUNT} history articles, never the article itself",
      [r["doc_id"] for r in place.get("related", [])] == history[:server.RELATED_COUNT]
      and [r["title"] for r in place["related"]][:2] == ["Zeta topic 00", "Zeta topic 01"], place.get("related"))
last = history[-1]
code, got = jget(f"/api/library/place?id={first_pid[last] + 1}")  # any passage of the article, not only the first
check("place: from the last article of a category it wraps round to the first, still at most the limit",
      [r["doc_id"] for r in got["place"]["related"]] == [hastings] + history[:server.RELATED_COUNT - 1], got)
code, got = jget(f"/api/library/place?id={first_pid[by_title['Water storage']]}")
place = got["place"]
check("place: a source with no categories gives the source alone and its next titles A to Z",
      code == 200 and place["source"] == "ready-gov" and place["category"] is None and place["category_name"] is None
      and [r["title"] for r in place["related"]] == ["Generator safety"], place)
code, got = jget(f"/api/library/place?id={first_pid[by_title['Mango']]}")
check("place: a Wikipedia article with no category ('other') falls back to the next titles of Wikipedia",
      got["place"]["category"] is None and [r["title"] for r in got["place"]["related"]][:2] == ["Zeta topic 00", "Zeta topic 01"]
      and len(got["place"]["related"]) == server.RELATED_COUNT, got)
code, got = jget(f"/api/library/place?id={first_pid[by_title['Zeta topic 59']]}")
check("place: at the end of a source the list carries on from its first title",
      got["place"]["category"] is None and [r["title"] for r in got["place"]["related"]][:2] == ["1066 in England", "Ada Lovelace"]
      and len(got["place"]["related"]) == server.RELATED_COUNT, got)
check("place: an unknown passage gives no place; a passage of Your files gives none either",
      jget("/api/library/place?id=99999")[1] == {"place": None}
      and jget(f"/api/library/place?id={server.ID_OFFSET + 1000}")[1] == {"place": None})
check("place: a bad id is 400, no token 403", jget("/api/library/place?id=0")[0] == 400
      and jget("/api/library/place?id=x")[0] == 400 and jget("/api/library/place")[0] == 400
      and jget(f"/api/library/place?id={first_pid[hastings]}", token=False)[0] == 403)
# every query it makes uses an index: nothing scans a whole table (the real library has a million articles)
plans = []
real_db = server.main_library().db


class Recorder:
    def execute(self, sql, args=()):
        plans.extend(r[3] for r in real_db.execute("EXPLAIN QUERY PLAN " + sql, args))
        return real_db.execute(sql, args)


class Lib:
    db = Recorder()
    _sort_keys = False


server.article_place(Lib(), first_pid[hastings])
server.article_place(Lib(), first_pid[by_title["Water storage"]])
check("place: every query is an index lookup or a bounded index range, no table scan",
      plans and not [p for p in plans if p.startswith("SCAN") and "USING" not in p], plans)
db.executemany("DELETE FROM reading WHERE category='history' AND doc_id=?", [(i,) for i in history])
db.commit()
db.close()
server.lib_version[0] += 1

# ---- a library built before the reading table and the passages(doc_id) index: calm answers ----
use(OLD)
code, got = jget("/api/library/random?category=history")
check("old library: random gives an empty list, not an error", code == 200 and got == {"articles": []}, (code, got))
old_doc = next(t["doc_id"] for t in jget("/api/library/browse?source=wikipedia&limit=3")[1]["titles"])
code, got = jget(f"/api/article?doc={old_doc}")
check("old library: a title still opens (more slowly)", code == 200 and got["parts"], code)
check("old library: browse and letters still answer", jget("/api/library/letters?source=wikipedia")[0] == 200)
code, got = jget("/api/library/place?id=1000")
check("old library: a place has the source and no category", code == 200 and got["place"]["source"] == "wikipedia"
      and got["place"]["category"] is None and got["place"]["related"], (code, got))

# ---- a document whose text file is gone: a plain message, not a blank page ----
GONE = os.path.join(TMP, "lib-gone")
build(GONE)
os.remove(os.path.join(GONE, "text", "shard-0.txt"))
use(GONE)
code, got = jget("/api/article?doc=1")
check("missing document: ?doc= is 404 with a plain message", code == 404 and got.get("missing") is True
      and got["error"] == "That document isn't in the library any more.", (code, got))
code, got = jget("/api/article?id=1000")
check("missing document: ?id= is 404 with the same message", code == 404 and got.get("missing") is True, (code, got))

# ---- no library at all ----
use(os.path.join(TMP, "none"))
check("no library: sources empty, info zeros, random empty", jget("/api/library/sources")[1] == {"sources": []}
      and jget("/api/library/info")[1]["articles"] == 0 and jget("/api/library/random")[1] == {"articles": []})
check("no library: browse 404, title search empty", jget("/api/library/browse?source=wikipedia")[0] == 404
      and jget("/api/search?q=ma&titles=1")[1] == {"results": []})

print(f"{'all passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
