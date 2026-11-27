"""The person's own files: documents they add (PDF, Word, EPUB, web pages, text), searched along with the library.

The folder is the record. Files put in it, by the app or by hand, are read into a small index of their own (kept
with the app's settings, so a library refresh never touches it), and taken out again when they leave
the folder. Text comes out with the standard library, except PDFs, which need pypdf (in the app's venv).

Files are untrusted input. Each one is parsed in a separate Python process (extract_isolated) with a time limit, a
cap on its size, a cap on how much its zip parts may unpack to, and no symlinks followed, so a damaged or hostile
file can't hang the app, fill its memory, or pull in a file from elsewhere on the Mac.
"""
import html.parser
import io
import json
import os
import posixpath
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import xml.parsers.expat
import zipfile

from library import oscompat
from library.passages import split_passages
from library.search import Library, query_words

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "my-files"
LABEL = "Your files"
ID_OFFSET = 1_000_000_000  # their passages get ids above the library's, so one id names one passage
KINDS = {".pdf": "PDF", ".docx": "Word", ".odt": "OpenDocument", ".epub": "EPUB", ".html": "web page",
         ".htm": "web page", ".txt": "text", ".md": "text", ".markdown": "text", ".csv": "text"}
CANT_READ = "Starholm can't read this kind of file. It reads PDF, Word, EPUB, web pages and text files."
MAX_TEXT = 10_000_000  # characters kept from one file (a 1,000-page book is about 3 million)
MAX_FILE_BYTES = 200 * 1024 ** 2  # biggest file read (and biggest upload the app accepts)
MAX_UNZIPPED = 300 * 1024 ** 2  # most that the parts of one Word, OpenDocument or EPUB file may unpack to
MAX_ZIP_ENTRIES = 10_000  # most parts in one of those files
PARSE_TIMEOUT = 120  # seconds one file may take to read before it's given up on
MAX_PARSE_MEMORY = 2 * 1024 ** 3  # bytes of memory the reading process may use before it is stopped
TOO_BIG = "This file is too big for Starholm to read (over 200 MB)."
TOO_BIG_UNPACKED = "This file unpacks to more than Starholm will read. It may be damaged."
TOO_SLOW = "Reading this file took too long, so Starholm gave up on it. It may be damaged."
TOO_MUCH_MEMORY = "Reading this file needed too much memory, so Starholm gave up on it. It may be damaged."
NOT_A_FILE = "This is a link to another file (or not a plain file), so Starholm skips it. Put the file itself here."
DAMAGED = "Couldn't read this file. It may be damaged, or saved in a way Starholm doesn't understand."
IGNORED = re.compile(r"^(\.|~\$)|^(desktop\.ini|thumbs\.db|icon\r)$|\.(part|crdownload|download|tmp)$", re.I)

SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT, topic TEXT,
                                       origin TEXT, docs INT DEFAULT 0, done INT DEFAULT 0, license TEXT);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, data BLOB);
    CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INT, title TEXT, url TEXT,
                                    file_id INT, offset INT, length INT);
    CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INT, offset INT, length INT);
    CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, body, content='', contentless_delete=1,
                                                      tokenize='porter unicode61 remove_diacritics 2');
    CREATE TABLE IF NOT EXISTS items(name TEXT PRIMARY KEY, size INT, mtime REAL, doc_id INT, status TEXT,
                                     error TEXT, title TEXT, passages INT, added REAL);
"""


class Unreadable(Exception):
    """A file we can't get text out of; the message is shown to the person."""


# ---------- searching ----------

class MyFiles(Library):
    """Their index: the library's tables, with each document's text stored in the database itself."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)

    @classmethod
    def open(cls, path):
        """Their index, or None when no file of theirs is ready to search."""
        if not os.path.exists(path):
            return None
        try:
            mine = cls(path)
            if mine.stats()[0]:
                return mine
            mine.db.close()
        except sqlite3.Error:  # created but not set up yet, or damaged (the next sync rebuilds it)
            pass
        return None

    def license(self, key):
        return ""

    def _read_bytes(self, file_id, offset, length):
        row = self.db.execute("SELECT substr(data, ?, ?) FROM files WHERE id=?", (offset + 1, length, file_id)).fetchone()
        return row[0] if row and row[0] is not None else b""  # empty if the file was re-read meanwhile


class Combined:
    """The library plus their files, searched as one. Either part may be missing."""

    def __init__(self, main, mine):
        self.main, self.mine = main, mine
        self.has_personal = mine is not None

    def _part(self, pid):
        return (self.mine, pid - ID_OFFSET) if pid >= ID_OFFSET else (self.main, pid)

    def stats(self):
        return self.main.stats() if self.main else (0, 0)

    def labels(self):
        return ([LABEL + " (documents the person added)"] if self.mine else []) + (self.main.labels() if self.main else [])

    def search(self, query, topic=None, limit=8):
        """Their files first when a passage has most of the query's words, then the library, then their weaker matches.
        (Scores from a handful of files and from a million articles can't be compared directly.)"""
        mine = self.mine.search(query, limit=limit, scores=True) if self.mine else []  # any topic: they're theirs
        main = self.main.search(query, topic=topic, limit=limit) if self.main else []
        need = max(1, round(len(query_words(query)) * 0.6))
        strong = [r for r in mine if r["matched"] >= need][:3]
        weak = [r for r in mine if r not in strong]
        for r in mine:
            r["id"] += ID_OFFSET
            del r["score"], r["matched"]
        return (strong + main + weak)[:limit]

    def read(self, passage_id, max_chars=5000):
        part, pid = self._part(passage_id)
        doc = part.read(pid, max_chars) if part and pid > 0 else None
        if doc and part is self.mine and doc["next_id"]:
            doc["next_id"] += ID_OFFSET
        return doc

    def document(self, passage_id):
        part, pid = self._part(passage_id)
        doc = part.document(pid) if part and pid > 0 else None
        if doc and part is self.mine:
            doc["focus"] += ID_OFFSET
            for p in doc["parts"]:
                p["id"] += ID_OFFSET
        return doc


# ---------- keeping the index in step with the folder ----------

progress = {"busy": False, "doing": "", "done": 0, "total": 0}
_lock = threading.Lock()
_again = threading.Event()


def open_db(path):
    """The index, made readable by this user only (mode 600; SQLite gives its -wal and -shm files the same mode)."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if not os.path.exists(path):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | oscompat.NOFOLLOW, 0o600))
    os.chmod(path, 0o600)
    db = sqlite3.connect(path, isolation_level=None, timeout=30)
    db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM sources WHERE key=?", (KEY,)).fetchone():
        db.execute("INSERT INTO sources(key, label, topic, origin, done) VALUES(?,?,NULL,'',1)", (KEY, LABEL))
    return db


def items(db_path):
    """What the index knows about each file, for the app's list."""
    if not os.path.exists(db_path):
        return []
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT name, status, error, title, passages, size, mtime FROM items ORDER BY added DESC, name")
        return [dict(zip(("name", "status", "error", "title", "passages", "size", "mtime"), r)) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        db.close()


def sync(folder, db_path):
    """Read new or changed files in folder and drop the ones that left it. Returns True if the index changed.
    If a sync is already running, it looks at the folder again when it finishes, instead of running twice."""
    if not _lock.acquire(blocking=False):
        _again.set()
        return False
    changed = False
    try:
        progress.update(busy=True, doing="", done=0, total=0)
        while True:
            _again.clear()
            try:
                changed = _sync_once(folder, db_path) or changed
            except sqlite3.DatabaseError as exc:
                if isinstance(exc, sqlite3.OperationalError):  # busy or locked: try again next time
                    raise
                for suffix in ("", "-wal", "-shm"):  # damaged: it is only a copy of the folder, so start it over
                    try:
                        os.remove(db_path + suffix)
                    except OSError:
                        pass
                changed = _sync_once(folder, db_path) or True
            if not _again.is_set():
                return changed
    finally:
        progress.update(busy=False, doing="", done=0, total=0)
        _lock.release()


def _on_disk(folder):
    """The plain files in folder and its subfolders. Symlinks (to files or folders) and anything that isn't a plain
    file are skipped: a link could pull a file from anywhere on the Mac into what the AI reads."""
    found = {}
    # The app passes the folder resolved. If it or any folder on its path has since been swapped for a link, the
    # resolved path differs: read nothing.
    if os.path.islink(folder) or os.path.realpath(folder) != os.path.normpath(os.path.abspath(folder)):
        return found
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(root, d))]
        for name in files:
            if IGNORED.search(name):
                continue
            path = os.path.join(root, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            found[os.path.relpath(path, folder).replace(os.sep, "/")] = (st.st_size, st.st_mtime)
    return found


def _sync_once(folder, db_path):
    found = _on_disk(folder) if os.path.isdir(folder) else {}
    if not found and not os.path.exists(db_path):
        return False
    db = open_db(db_path)
    try:
        known = {r[0]: (r[1], r[2]) for r in db.execute("SELECT name, size, mtime FROM items")}
        changed = False
        for name in known.keys() - found.keys():
            _drop(db, name)
            changed = True
        todo = sorted(n for n in found if known.get(n) != found[n])
        progress.update(total=len(todo))
        for i, name in enumerate(todo):
            progress.update(doing=name, done=i)
            if time.time() - found[name][1] < 2:  # maybe still being copied in: wait until it stops changing
                time.sleep(2)
                size_now = _on_disk_one(folder, name)
                if size_now != found[name]:
                    _again.set()
                    continue
            _index(db, folder, name, *found[name])
            changed = True
        if changed:
            db.execute("UPDATE sources SET docs=(SELECT COUNT(*) FROM items WHERE status='ready') WHERE key=?", (KEY,))
        return changed
    finally:
        db.close()


def _on_disk_one(folder, name):
    try:
        st = os.lstat(os.path.join(folder, *name.split("/")))
        return st.st_size, st.st_mtime
    except OSError:
        return None


def _drop(db, name, keep_item=False):
    row = db.execute("SELECT doc_id FROM items WHERE name=?", (name,)).fetchone()
    db.execute("BEGIN")
    if row and row[0]:
        doc = row[0]
        db.execute("DELETE FROM fts WHERE rowid IN (SELECT id FROM passages WHERE doc_id=?)", (doc,))
        db.execute("DELETE FROM passages WHERE doc_id=?", (doc,))
        db.execute("DELETE FROM files WHERE id=(SELECT file_id FROM docs WHERE id=?)", (doc,))
        db.execute("DELETE FROM docs WHERE id=?", (doc,))
    if not keep_item:
        db.execute("DELETE FROM items WHERE name=?", (name,))
    db.execute("COMMIT")


def _index(db, folder, name, size, mtime):
    """Read one file into the index (replacing an older version), or record why it can't be read."""
    _drop(db, name, keep_item=True)
    added = time.time()
    row = db.execute("SELECT added FROM items WHERE name=?", (name,)).fetchone()
    if row and row[0]:
        added = row[0]
    status, error, title, doc_id, count = "ready", None, None, None, 0
    try:
        title, text = extract_isolated(os.path.join(folder, *name.split("/")), folder)
        doc_id, count = _write(db, name, title, text[:MAX_TEXT])
    except Unreadable as exc:
        status, error = "unreadable", str(exc)
    except Exception:  # a damaged or unusual file: say so rather than stop reading the others
        status, error = "unreadable", DAMAGED
    db.execute("INSERT OR REPLACE INTO items(name, size, mtime, doc_id, status, error, title, passages, added) "
               "VALUES(?,?,?,?,?,?,?,?,?)", (name, size, mtime, doc_id, status, error, title, count, added))


def _write(db, name, title, text):
    """Store one document the way build_library.py lays out a library shard, so the reader shows it the same way."""
    source_id = db.execute("SELECT id FROM sources WHERE key=?", (KEY,)).fetchone()[0]
    out, passages = bytearray(), []
    out += f"# {title}\nSource: {LABEL} | {name}\n\n".encode()
    for heading, chunk in split_passages(text):
        b = chunk.encode("utf-8")
        passages.append((len(out), len(b), heading, chunk))
        out += b + b"\n\n"
    out += b"---\n\n"
    db.execute("BEGIN")
    try:
        file_id = db.execute("INSERT INTO files(path, data) VALUES(?,?)", (name, bytes(out))).lastrowid
        doc_id = db.execute("INSERT INTO docs(source_id, title, url, file_id, offset, length) VALUES(?,?,?,?,0,?)",
                            (source_id, title, name, file_id, len(out))).lastrowid
        for offset, length, heading, chunk in passages:
            pid = db.execute("INSERT INTO passages(doc_id, offset, length) VALUES(?,?,?)",
                             (doc_id, offset, length)).lastrowid
            db.execute("INSERT INTO fts(rowid, title, body) VALUES(?,?,?)", (pid, f"{title} {heading}".strip(), chunk))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return doc_id, len(passages)


# ---------- getting the text out ----------

# Run in the child process: python -I -c _CHILD <code root> <file> <limits as JSON>. -I keeps PYTHONPATH, the user's
# site-packages and the current folder out of its imports.
_CHILD = ("import sys; sys.path.insert(0, sys.argv[1]); from library import myfiles; "
          "myfiles._child_main(sys.argv[2], sys.argv[3])")


def _inside(path, folder):
    real, top = os.path.realpath(path), os.path.realpath(folder)
    return real.startswith(top.rstrip(os.sep) + os.sep)


def extract_isolated(path, folder=None):
    """(title, text) of one of their files, read in a separate process that is stopped after PARSE_TIMEOUT seconds;
    Unreadable with a reason the person can act on. Symlinks, other non-plain files, files outside folder and files
    over MAX_FILE_BYTES are refused before anything is read."""
    try:
        st = os.lstat(path)
    except OSError:
        raise Unreadable(DAMAGED) from None
    if not stat.S_ISREG(st.st_mode) or (folder is not None and not _inside(path, folder)):
        raise Unreadable(NOT_A_FILE)
    if os.path.splitext(path)[1].lower() not in KINDS:
        raise Unreadable(CANT_READ)
    if st.st_size > MAX_FILE_BYTES:
        raise Unreadable(TOO_BIG)
    limits = json.dumps({"max_file": MAX_FILE_BYTES, "max_unzipped": MAX_UNZIPPED, "max_entries": MAX_ZIP_ENTRIES,
                         "max_text": MAX_TEXT, "cpu": int(PARSE_TIMEOUT) + 5, "max_memory": MAX_PARSE_MEMORY})
    # The child imports library/search.py, which reads the app's settings and marks the live library build as in use.
    # Point both at an empty folder nobody can write to, so a parse never touches either.
    nowhere = "/var/empty/survival-llm-parser"
    if oscompat.WINDOWS:  # a folder in System32, where an account without admin rights can't make one
        nowhere = os.path.join(os.environ.get("SYSTEMROOT") or r"C:\Windows", "System32", "survival-llm-parser")
    env = {"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"), "LANG": "en_US.UTF-8",
           "SURVIVAL_LLM_APP_DIR": nowhere, "SURVIVAL_LLM_LIBRARY": nowhere, "SURVIVAL_LLM_DATA": nowhere}
    proc = subprocess.Popen([sys.executable, "-I", "-c", _CHILD, ROOT, path, limits], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT, env=env)
    deadline = time.monotonic() + PARSE_TIMEOUT
    try:
        while True:  # wait in short steps, checking its memory in between (macOS doesn't enforce RLIMIT_AS)
            try:
                stdout, _ = proc.communicate(timeout=max(0.0, min(0.5, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise Unreadable(TOO_SLOW) from None
                if _rss_bytes(proc.pid) > MAX_PARSE_MEMORY:
                    raise Unreadable(TOO_MUCH_MEMORY) from None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    try:
        out = json.loads(stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Unreadable(DAMAGED) from None
    if not isinstance(out, dict) or not out.get("ok"):
        message = out.get("unreadable") if isinstance(out, dict) else None
        raise Unreadable(message if isinstance(message, str) and message else DAMAGED)
    title, text = out.get("title"), out.get("text")
    if not isinstance(title, str) or not isinstance(text, str):
        raise Unreadable(DAMAGED)
    return title, text[:MAX_TEXT]


def _rss_bytes(pid):
    """A process's resident memory in bytes, from ps (0 if it can't tell, say because it has just exited)."""
    if oscompat.WINDOWS:
        return oscompat.rss_bytes(pid)
    try:
        out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(int(pid))], capture_output=True, text=True,
                             timeout=5, env={"PATH": "/usr/bin:/bin"}).stdout.strip()
        return int(out) * 1024 if out.isdigit() else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def _peak_memory():
    """This process's peak memory in bytes (ru_maxrss is bytes on macOS, kilobytes elsewhere)."""
    if oscompat.WINDOWS:
        return oscompat.peak_rss_bytes()
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _memory_watchdog(limit):
    """In the child: stop the process as soon as its peak memory passes limit, saying why. A second guard beside the
    parent's polling, in case one of them misses a quick rise. It looks once before the file is read, too."""
    if _peak_memory() > limit:
        sys.stdout.write(json.dumps({"ok": False, "unreadable": TOO_MUCH_MEMORY}))
        sys.stdout.flush()
        os._exit(3)

    def watch():
        while True:
            if _peak_memory() > limit:
                sys.stdout.write(json.dumps({"ok": False, "unreadable": TOO_MUCH_MEMORY}))
                sys.stdout.flush()
                os._exit(3)
            time.sleep(0.1)
    threading.Thread(target=watch, daemon=True).start()


def _child_main(path, limits):
    """The child process: read one file within the limits it was given and print {"ok", "title", "text"} as JSON, or
    {"ok": false, "unreadable": reason}."""
    global MAX_FILE_BYTES, MAX_UNZIPPED, MAX_ZIP_ENTRIES, MAX_TEXT
    memory = MAX_PARSE_MEMORY
    try:
        lim = json.loads(limits)
        MAX_FILE_BYTES, MAX_UNZIPPED = int(lim["max_file"]), int(lim["max_unzipped"])
        MAX_ZIP_ENTRIES, MAX_TEXT = int(lim["max_entries"]), int(lim["max_text"])
        memory = int(lim["max_memory"])
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (int(lim["cpu"]), int(lim["cpu"])))  # a backstop to the timeout
    except Exception:  # noqa: BLE001 - the parent's timeout and memory checks still apply
        pass
    _memory_watchdog(memory)
    try:
        title, text = extract(path)
        out = {"ok": True, "title": title, "text": text[:MAX_TEXT]}
    except Unreadable as exc:
        out = {"ok": False, "unreadable": str(exc)}
    except BaseException:  # noqa: BLE001 - anything else is a damaged file, never a crash of the app
        out = {"ok": False}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


def _read_capped(path):
    """The file's bytes, opened without following a symlink, at most MAX_FILE_BYTES of them. Windows has no
    O_NOFOLLOW, so there the file opened must be the very file at path (same identity as lstat sees), not what a
    link points at."""
    try:
        link = os.lstat(path) if oscompat.WINDOWS else None
        fd = os.open(path, os.O_RDONLY | oscompat.NOFOLLOW | oscompat.BINARY)
    except OSError:
        raise Unreadable(NOT_A_FILE) from None  # a symlink (ELOOP), or gone
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise Unreadable(NOT_A_FILE)
        if link is not None and (not stat.S_ISREG(link.st_mode)
                                 or (link.st_dev, link.st_ino) != (st.st_dev, st.st_ino)):
            raise Unreadable(NOT_A_FILE)
        if st.st_size > MAX_FILE_BYTES:
            raise Unreadable(TOO_BIG)
        data = f.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise Unreadable(TOO_BIG)
    return data


class _Budget:
    """How much more the parts of one zip-based file may unpack to."""

    def __init__(self):
        self.left = MAX_UNZIPPED

    def read(self, z, name):
        info = z.getinfo(name)
        if info.file_size > self.left:
            raise Unreadable(TOO_BIG_UNPACKED)
        with z.open(info) as f:  # the header's size can lie: count what actually comes out
            data = f.read(self.left + 1)
        if len(data) > self.left:
            raise Unreadable(TOO_BIG_UNPACKED)
        self.left -= len(data)
        return data


def _open_zip(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    if len(z.infolist()) > MAX_ZIP_ENTRIES:
        raise Unreadable(TOO_BIG_UNPACKED)
    return z


class _Declaration(Unreadable):
    """XML with a DOCTYPE or an entity declaration: always refuses the whole file."""


class _MalformedXML(Unreadable):
    """XML that doesn't parse."""


def _refuse_declaration(*_):
    raise _Declaration(DAMAGED)


def _xml(data):
    """Parse XML from inside a document. These never need a DOCTYPE, and refusing one rules out entity expansion. The
    byte check is quick; an expat pass then catches a DOCTYPE or entity in any encoding (UTF-16 included)."""
    if re.search(rb"<!(DOCTYPE|ENTITY)", data, re.I):
        raise _Declaration(DAMAGED)
    check = xml.parsers.expat.ParserCreate()
    check.StartDoctypeDeclHandler = _refuse_declaration
    check.EntityDeclHandler = _refuse_declaration
    check.ExternalEntityRefHandler = _refuse_declaration
    try:
        check.Parse(data, True)
    except xml.parsers.expat.ExpatError:
        raise _MalformedXML(DAMAGED) from None
    return ET.fromstring(data)


def extract(path):
    """(title, text) of a file, or Unreadable with a reason the person can act on. Runs in this process: the app
    calls extract_isolated() instead."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in KINDS:
        raise Unreadable(CANT_READ)
    data = _read_capped(path)
    if ext in (".docx", ".odt", ".epub"):
        title, text = {".docx": _docx, ".odt": _odt, ".epub": _epub}[ext](data, _Budget())
    elif ext == ".pdf":
        title, text = _pdf(data)
    elif ext in (".html", ".htm"):
        title, text = _html(_decode(data))
    else:
        title, text = _plain(data, path)
    text = re.sub(r"\n{3,}", "\n\n", text[:MAX_TEXT]).strip()
    if not text:
        raise Unreadable("There's no text in this file.")
    if not (title or "").strip():  # no title of its own: a short first line that isn't a sentence, or the file name
        first = text.split("\n\n", 1)[0].lstrip("#").strip()
        title = first if len(first) <= 80 and "\n" not in first and not first.endswith(".") else ""
    stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ").strip()
    title = " ".join((title or "").split())[:150] or stem
    return title, text


def _pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unreadable("Reading PDFs needs pypdf, which this copy of Starholm is missing.") from None
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            raise Unreadable("This PDF is protected, so Starholm can't open it. Open it, print it to a new PDF "
                             "(Print, then Save as PDF), and add that copy.")
    pages = []
    for page in reader.pages:
        try:
            pages.append(_pdf_paragraphs(page.extract_text() or ""))
        except Exception:
            pages.append("")
    text = "\n\n".join(p for p in pages if p)
    if len(text) < 40:
        raise Unreadable("This PDF is pictures of pages (a scan or photo) with no text in it. "
                         "Starholm can't read those yet.")
    title = ""
    try:
        title = str((reader.metadata or {}).get("/Title") or "")
    except Exception:
        pass
    title = re.sub(r"^Microsoft (Word|PowerPoint) - ", "", title).strip()
    if re.fullmatch(r"(?i)(untitled.*|document\d*|.*\.(docx?|pdf|indd))", title):
        title = ""
    return title, text


def _pdf_paragraphs(text):
    """PDF text comes one printed line at a time: join lines into paragraphs, ending one at a short line that
    finishes a sentence, and rejoin words hyphenated across lines."""
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    width = max((len(line) for line in lines), default=0)
    paras, cur = [], ""
    for line in lines:
        if not line:
            if cur:
                paras.append(cur)
            cur = ""
            continue
        if cur.endswith("-") and not cur.endswith(" -") and line[:1].islower():
            cur = cur[:-1] + line
        else:
            cur = f"{cur} {line}" if cur else line
        if line.endswith((".", "!", "?", ":")) and len(line) < 0.75 * width:
            paras.append(cur)
            cur = ""
    if cur:
        paras.append(cur)
    return "\n\n".join(paras)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DC = "{http://purl.org/dc/elements/1.1/}"


def _docx(data, budget):
    with _open_zip(data) as z:
        body = _xml(budget.read(z, "word/document.xml"))
        title = _xml_title(z, "docProps/core.xml", budget)
    paras = []
    for p in body.iter(W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == W + "t":
                parts.append(el.text or "")
            elif el.tag == W + "tab":
                parts.append(" ")
            elif el.tag in (W + "br", W + "cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if not text:
            continue
        style = p.find(f"{W}pPr/{W}pStyle")
        style = style.get(W + "val", "") if style is not None else ""
        level = re.match(r"(?i)heading\s*(\d)", style)
        if level or style.lower() == "title":
            text = "#" * min(int(level.group(1)) if level else 1, 4) + " " + text
        elif p.find(f"{W}pPr/{W}numPr") is not None:
            text = "- " + text
        paras.append(text)
    return title, "\n\n".join(paras)


def _odt(data, budget):
    t = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    with _open_zip(data) as z:
        body = _xml(budget.read(z, "content.xml"))
        title = _xml_title(z, "meta.xml", budget)
    paras = []
    for el in body.iter():
        if el.tag == t + "h":
            text = " ".join("".join(el.itertext()).split())
            if text:
                paras.append("#" * min(int(el.get(t + "outline-level") or 1), 4) + " " + text)
        elif el.tag == t + "p":
            text = " ".join("".join(el.itertext()).split())
            if text:
                paras.append(text)
    return title, "\n\n".join(paras)


def _xml_title(z, name, budget):
    """The title from a document's metadata part, or "" when that part is missing or broken (the document is still
    read). A DOCTYPE or entity declaration there still refuses the whole file."""
    try:
        found = _xml(budget.read(z, name)).find(f".//{DC}title")
        return (found.text or "").strip() if found is not None else ""
    except (KeyError, ET.ParseError, _MalformedXML):
        return ""


def _epub(data, budget):
    opf_ns = "{http://www.idpf.org/2007/opf}"
    with _open_zip(data) as z:
        names = set(z.namelist())
        if "META-INF/encryption.xml" in names and re.search(rb"\.x?html?\b", budget.read(z, "META-INF/encryption.xml")):
            raise Unreadable("This ebook is copy-protected, so Starholm can't read it.")
        container = _xml(budget.read(z, "META-INF/container.xml"))
        root = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
        opf = _xml(budget.read(z, root))
        found = opf.find(f".//{DC}title")
        title = (found.text or "").strip() if found is not None else ""
        hrefs = {item.get("id"): item.get("href") for item in opf.iter(opf_ns + "item")}
        base = posixpath.dirname(root)
        chapters = []
        for ref in opf.iter(opf_ns + "itemref"):
            href = hrefs.get(ref.get("idref"))
            name = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(href or "")))
            if href and name in names:
                chapters.append(_html(budget.read(z, name).decode("utf-8", "replace"))[1])
    return title, "\n\n".join(chapters)


class _HTMLText(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer", "form", "button", "select"}
    BLOCK = {"p", "div", "section", "article", "main", "aside", "ul", "ol", "table", "tr", "blockquote", "pre",
             "dl", "dt", "dd", "figure", "figcaption", "hr", "header", "td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self.in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * min(int(tag[1]), 4) + " ")
        elif tag == "li":
            self.out.append("\n\n- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self.in_title = False
        elif tag in self.BLOCK or tag == "li" or re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def _html(source):
    parser = _HTMLText()
    parser.feed(source)
    parser.close()
    paras, bullet = [], False
    for para in re.split(r"\n{2,}", "".join(parser.out)):
        para = " ".join(para.split())
        if para == "-":  # a list item whose text sits in its own paragraph
            bullet = True
            continue
        if para and para.strip("#"):
            paras.append(("- " if bullet and not para.startswith("#") else "") + para)
            bullet = False
    return parser.title.strip(), "\n\n".join(paras)


def _plain(data, path):
    text = _decode(data)
    heading = re.search(r"^#\s+(.+)$", text, re.M) if path.lower().endswith((".md", ".markdown")) else None
    return (heading.group(1) if heading else ""), text.replace("\r\n", "\n")


def _decode(data):
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")
