"""Read-only search over the offline library built by build_library.py (stdlib only)."""
import atexit
import json
import os
import re
import sqlite3
import subprocess
import sys


# The checkout this code runs from. Everything the app and the pipeline write stays inside it by default.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    """The app's settings folder (app/server.py keeps its settings, memory, chats and indexes there):
    $SURVIVAL_LLM_APP_DIR, else app-data/ in the checkout."""
    return os.environ.get("SURVIVAL_LLM_APP_DIR") or os.path.join(REPO, "app-data")


def default_files_dir():
    """Where "Your files" lives unless the person picked another folder: your-files/ in the checkout, or
    $SURVIVAL_LLM_YOUR_FILES (the Mac app keeps it in its Application Support folder)."""
    return os.environ.get("SURVIVAL_LLM_YOUR_FILES") or os.path.join(REPO, "your-files")


def chosen_folder(key):
    """A folder the person picked in the app: "library_dir" (the library) or "files_dir" (Your files)."""
    try:
        with open(os.path.join(app_dir(), "settings.json"), encoding="utf-8") as f:
            value = json.load(f).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and os.path.isabs(value) else None


# The data folder library/safe_fetch.py writes to: data.noindex/ in the checkout (".noindex" keeps Spotlight out).
# SURVIVAL_LLM_DATA moves both.
DATA_DIR = os.path.abspath(os.environ.get("SURVIVAL_LLM_DATA") or os.path.join(REPO, "data.noindex"))
DEFAULT_LIBRARY = os.path.join(DATA_DIR, "current")  # a symlink refresh.py swaps to each new build


def default_library():
    """Where the library is read from unless the person picked another folder: the "current" symlink in the data
    folder, resolved to the build it points at. Resolving once means a refresh that swaps the symlink while the app
    runs cannot mix two builds; the app picks up the new build on its next start. The build is marked as in use by
    this process, so a refresh never deletes it while this process runs."""
    lib = os.path.realpath(DEFAULT_LIBRARY)
    _mark_reader(lib)
    return lib


def process_start(pid):
    """When process pid started, as ps prints it in UTC ("Thu Nov 12 06:55:14 2026"), or None when ps gives no
    answer: the process has ended, or ps itself failed. A pid with a different start time is a different process
    that reused the number; with None the caller has to check whether the pid is running some other way."""
    if sys.platform == "win32":  # no ps: GetProcessTimes (library/oscompat.py)
        from library import oscompat
        return oscompat.process_start(pid)
    try:
        r = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True,
                           timeout=10, env={"LC_ALL": "C", "TZ": "UTC", "PATH": "/usr/bin:/bin"})
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    out = " ".join(r.stdout.split())
    return out if r.returncode == 0 and out else None


def _mark_reader(lib):
    """Leave <build>/.readers/<pid> holding this process's start time (removed again at exit).
    build_library.prune_builds() skips a build with a marker of a process that is still running, the same process
    (same start time), not one that reused the number after a crash. Only for builds in the data folder's libs/."""
    if os.path.dirname(lib) != os.path.realpath(os.path.join(DATA_DIR, "libs")) or not os.path.isdir(lib):
        return
    marker = os.path.join(lib, ".readers", str(os.getpid()))
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w") as f:
            json.dump({"pid": os.getpid(), "start": process_start(os.getpid()) or ""}, f)
    except OSError:
        return

    def unmark():
        try:
            os.remove(marker)
        except OSError:
            pass
    atexit.register(unmark)


def _find_library():
    """The "library folder" the person picked in the app; else $SURVIVAL_LLM_LIBRARY; else default_library().
    The same order as app/server.py, so the app and this module always agree."""
    chosen = chosen_folder("library_dir")
    if chosen:
        return chosen
    if os.environ.get("SURVIVAL_LLM_LIBRARY"):
        return os.environ["SURVIVAL_LLM_LIBRARY"]
    return default_library()


LIB = _find_library()
DB_PATH = os.path.join(LIB, "index.sqlite")
TEXT_DIR = os.path.join(LIB, "text")
TOPICS = ("medical", "emergency", "howto", "repair", "encyclopedia")

# Ranking boost for sources with editorial review over wikiHow and Q&A sites.
OFFICIAL = ("ready-gov", "cdc-travel", "medlineplus", "nhs-medicines", "military-medicine", "wikem",
            "medical-library", "water", "post-disaster", "food-prep", "survival-manuals", "first-aid-guides",
            "usda-canning", "risk")
# Tuned on 14 November 2026 with evals/passage_replay.py (it was official 1.3, Wikipedia and WikiMed 1.15, the rest 1.0):
# the official sources are small beside Wikipedia, so BM25 alone buries them; forums and repair guides rank lowest.
AUTHORITY = {**{k: 2.0 for k in OFFICIAL}, "wikimed": 1.5, "wikipedia": 1.0,
             **{k: 0.8 for k in ("se-diy", "se-cooking", "se-outdoors", "se-mechanics", "ifixit")}}
TITLE_WEIGHT = 4.0  # BM25 weight of a title word against a body word (1.0)
POOL = 300          # passages ranked by BM25 before the authority weights reorder them
MIN_HITS = 5        # fewer passages holding every word than this: the fallback (Library._match); it was 3
FALLBACK = "or"     # "or": any one word (of those not in more than OR_MAX_SHARE of passages); "relax": the most
                    # common words made optional one by one (tried 14 November 2026: fast, but found fewer answers)
OR_MAX_SHARE = 0.05  # 1.0 keeps every word, as before 14 November 2026; 0.02 lost the power line answer
# Sources published by a government health/emergency agency, the IFRC or WHO: shown with an "Official" label.
# (Not the UK medicines guide: the NHS terms say an unrefreshed copy must not be presented as the NHS's.)
OFFICIAL_LABEL = {"ready-gov", "cdc-travel", "medlineplus", "military-medicine", "survival-manuals",
                  "first-aid-guides", "usda-canning"}

STOPWORDS = set("""a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her here
hers herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on
once only or other our ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours yourself yourselves""".split())


def use_folder(path):
    """Read the library from path from now on (the app moved it, or the person picked another one)."""
    global LIB, DB_PATH, TEXT_DIR
    LIB, DB_PATH, TEXT_DIR = path, os.path.join(path, "index.sqlite"), os.path.join(path, "text")


class Library:
    def __init__(self, path=None, text_dir=None):
        """path: an index.sqlite (default: the library's). text_dir: its text shards; by default the "text" folder
        beside path when path is given, else the library's (which follows use_folder())."""
        self.text_dir = text_dir or (os.path.join(os.path.dirname(os.path.abspath(path)), "text") if path else None)
        path = path or DB_PATH
        # check_same_thread=False only so a library swap can close it from another thread (see app/server.py)
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        try:
            self.db.execute("SELECT 1 FROM sources LIMIT 1")
        except sqlite3.OperationalError:  # e.g. a WAL-mode file on a read-only disk
            self.db = sqlite3.connect(f"file:{path}?immutable=1", uri=True, check_same_thread=False)

    def stats(self):
        """(articles, sources) that are fully indexed."""
        sources, docs = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(docs), 0) FROM sources WHERE done=1").fetchone()
        return docs, sources

    def labels(self):
        """Names of the indexed sources, largest first, for telling the model what it can search."""
        return [r[0] for r in self.db.execute("SELECT label FROM sources WHERE done=1 ORDER BY docs DESC")]

    def _read(self, file_id, offset, length):
        return self._read_bytes(file_id, offset, length).decode("utf-8", "replace")

    def search(self, query, topic=None, limit=8, scores=False):
        """Best passages for a keyword query, at most one per article. scores=True adds each result's ranking
        score (lower is better) and how many of the query's words its passage contains, for merging result lists."""
        words = query_words(query)
        if not words:
            return []
        hits = self._match(words)
        if not hits:
            return []
        rank = dict(hits)
        marks = ",".join("?" * len(rank))
        info = {r[0]: r for r in self.db.execute(
            "SELECT p.id, d.id, d.title, s.label, s.topic, d.file_id, p.offset, p.length, s.key, d.offset, d.length "
            f"FROM passages p JOIN docs d ON d.id = p.doc_id JOIN sources s ON s.id = d.source_id "
            f"WHERE p.id IN ({marks})", list(rank))}
        # BM25 ranks are negative (lower is better), so a weight above 1 moves a source up.
        order = sorted(info, key=lambda pid: rank[pid] * AUTHORITY.get(info[pid][8], 1.0))
        results, seen_docs, shown = [], set(), set()
        for pid in order:
            r = info[pid]
            if r[1] in seen_docs or (topic and topic != "any" and r[4] != topic):
                continue
            seen_docs.add(r[1])
            body = self._read_bytes(r[5], r[9], r[10]).split(b"\n\n", 1)[-1]  # the article without its title header
            if body in shown:  # the same article under another title (iFixit's guides for each model variant)
                continue
            shown.add(body)
            text = self._read(r[5], r[6], r[7])
            results.append({"id": pid, "title": r[2], "source": r[3], "official": r[8] in OFFICIAL_LABEL,
                            "snippet": _snippet(text, words)})
            if scores:
                low = f"{r[2]} {text}".lower()
                results[-1].update(score=rank[pid] * AUTHORITY.get(r[8], 1.0),
                                   matched=sum(w[:5] in low for w in words))
            if len(results) >= limit:
                break
        return results

    def _ranked(self, expression):
        return self.db.execute(
            f"SELECT rowid, rank FROM fts WHERE fts MATCH ? AND rank MATCH 'bm25({TITLE_WEIGHT}, 1.0)' "
            f"ORDER BY rank LIMIT {POOL}", (expression,)).fetchall()

    def _match(self, words):
        """[(passage id, BM25 rank)] for the words, best first: passages holding all of them; when that finds fewer
        than MIN_HITS, the most common word is made optional (it still adds to the rank where it appears), then the
        next most common, until enough are found or one word is left. With FALLBACK = "or", any one word instead,
        as before 14 November 2026 (slow: ranking a common word means ranking millions of passages)."""
        quoted = lambda w: '"' + w.replace('"', "") + '"'  # noqa: E731  (a word the model repeats counts twice)
        hits = self._ranked(" ".join(map(quoted, words)))
        if len(hits) >= MIN_HITS or len(words) == 1:
            return hits
        if FALLBACK == "or":
            # A word in more than OR_MAX_SHARE of all passages adds almost nothing to the BM25 rank but makes the
            # search rank millions of passages: it is left out, unless every word is that common.
            total = self._passages()
            rare = [w for w in words if self._count(quoted(w)) <= OR_MAX_SHARE * total] or words
            return self._ranked(" OR ".join(map(quoted, rare)))
        common = sorted(dict.fromkeys(words), key=lambda w: -self._count(quoted(w)))
        for k in range(1, len(common)):
            needed = [w for w in words if w not in common[:k]]
            optional = [w for w in words if w in common[:k]]
            hits = self._ranked(" ".join(map(quoted, needed)) + " AND ("
                                + " OR ".join(map(quoted, [needed[0]] + optional)) + ")")
            if len(hits) >= MIN_HITS:
                break
        return hits

    def _passages(self):
        """How many passages the library has (the highest passage id: they are numbered from 1)."""
        if "_total" not in self.__dict__:
            self._total = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM passages").fetchone()[0] or 1
        return self._total

    def _count(self, term):
        """How many passages hold a word (kept for the life of the connection)."""
        counts = self.__dict__.setdefault("_counts", {})
        if term not in counts:
            counts[term] = self.db.execute("SELECT count(*) FROM fts WHERE fts MATCH ?", (term,)).fetchone()[0]
        return counts[term]

    def read(self, passage_id, max_chars=5000):
        """Article text starting at a passage, about max_chars long."""
        row = self.db.execute(
            "SELECT p.doc_id, d.title, s.label, d.url, d.file_id, s.key FROM passages p JOIN docs d ON d.id = p.doc_id "
            "JOIN sources s ON s.id = d.source_id WHERE p.id=?", (passage_id,)).fetchone()
        if not row:
            return None
        doc_id, title, label, url, file_id, key = row
        parts, size, next_id = [], 0, None
        for pid, offset, length in self.db.execute(
                "SELECT id, offset, length FROM passages WHERE doc_id=? AND id>=? ORDER BY id", (doc_id, passage_id)):
            if parts and size + length > max_chars:
                next_id = pid
                break
            parts.append(self._read(file_id, offset, length))
            size += length
        return {"title": title, "source": label, "url": url, "official": key in OFFICIAL_LABEL,
                "text": "\n\n".join(parts), "next_id": next_id}


    def document(self, passage_id):
        """The whole article holding a passage, split into passages so a viewer can mark the cited one."""
        row = self.db.execute(
            "SELECT d.id, d.title, s.label, d.url, d.file_id, d.offset, d.length, s.key FROM passages p "
            "JOIN docs d ON d.id = p.doc_id JOIN sources s ON s.id = d.source_id WHERE p.id=?",
            (passage_id,)).fetchone()
        if not row:
            return None
        doc_id, title, label, url, file_id, start, length, key = row
        raw = self._read_bytes(file_id, start, length)
        parts = [{"id": pid, "text": raw[off - start:off - start + n].decode("utf-8", "replace")}
                 for pid, off, n in self.db.execute(
                     "SELECT id, offset, length FROM passages WHERE doc_id=? ORDER BY id", (doc_id,))]
        from library import titles
        return {"doc_id": doc_id, "title": titles.cleanup().display_title(key, title), "source": label, "url": url,
                "official": key in OFFICIAL_LABEL, "license": self.license(key), "focus": passage_id, "parts": parts}

    def license(self, key):
        """The source's license line, from libraries built since licenses were kept ('' before that)."""
        try:
            row = self.db.execute("SELECT license FROM sources WHERE key=?", (key,)).fetchone()
        except sqlite3.OperationalError:  # no license column yet
            return ""
        return (row and row[0]) or ""

    def _read_bytes(self, file_id, offset, length):
        """Bytes from a text shard. The shard's path comes from the index, which may come from any folder the app
        is pointed at, so it is resolved (symlinks included) and must stay inside the library's text folder;
        anything else reads as empty."""
        row = self.db.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
        full = safe_text_path(getattr(self, "text_dir", None) or TEXT_DIR, row[0] if row else None)
        if full is None or not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length < 0:
            return b""
        with open(full, "rb") as f:
            f.seek(offset)
            return f.read(length)


def safe_text_path(text_dir, rel):
    """text_dir/rel resolved, if it is a regular file inside text_dir after resolving symlinks; else None.
    Refuses absolute paths, "..", and symlinks that lead out of the folder."""
    if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel):
        return None
    root = os.path.realpath(text_dir)
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    return full


def query_words(query):
    """The words of a query worth searching for."""
    return [w for w in re.findall(r"[\w']+", query.lower()) if w not in STOPWORDS and len(w) > 1][:12]


def _snippet(text, words, width=360):
    flat = " ".join(line.lstrip("#").strip() for line in text.splitlines() if line.strip())
    low = flat.lower()
    hits = [low.find(w) for w in words if low.find(w) >= 0]
    start = max(0, min(hits) - 80) if hits else 0
    out = flat[start:start + width]
    return ("..." if start else "") + out + ("..." if start + width < len(flat) else "")
