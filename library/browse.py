"""Read-only browsing of the built library, for the app's Library page and "Something to read" (BUILD-SPEC.md
sections 10 and 11). app/server.py exposes these over HTTP.

Every function takes `lib`, an open library.search.Library (its read-only connection `lib.db` and its text folder),
and only reads. The queries use the indexes every build has since build_library.REQUIRED_SCHEMA: docs_source_sort
(titles of one source in the Library page's order, by cleanup.sort_key()), docs_title (titles across sources),
passages_doc (a document's first passage) and the reading tables of library/categories.py. A library built before
them still answers, more slowly (titles() and letters() by raw title), except random_articles(), which returns []
without the reading table; `build_library.py --upgrade` adds them.

Importing this module imports library/search.py, which marks the live build as in use by this process (the server
has done that already).

Source facts:
    key        build key ("medlineplus")
    name       the name shown ("MedlinePlus (NIH)")
    topic      one of TOPICS' names ("Medical", "Emergency and preparedness", "How-to", "Repair",
               "Encyclopedia and travel"); "Your files" is the app's own folder (library/myfiles.py), not a source
    topic_key  the stored topic ("medical", "emergency", "howto", "repair", "encyclopedia")
    articles   documents in the build
    kind       "Wiki", "Q&A", "Web pages", "PDFs" or "Guides" (KINDS)
    official   True for sources published by a government health or emergency agency, the IFRC or WHO
               (search.OFFICIAL_LABEL)
    edition    the edition the build was converted from, as the catalog dates it ("2026-02"), or None
    licence    the licence line ("CC BY-SA 4.0, Wikipedia contributors https://..."), or ""
    updated    True if the build being read converted this source again (it changed in the refresh that made
               this build), else False
"""
import json
import os
import random
import re
import sqlite3

from library import build_library, categories, search, titles
cleanup = titles.cleanup()  # library/cleanup.py, or plain stand-ins where it is absent (library/titles.py)

TOPICS = (("medical", "Medical"), ("emergency", "Emergency and preparedness"), ("howto", "How-to"),
          ("repair", "Repair"), ("encyclopedia", "Encyclopedia and travel"), ("files", "Your files"))
KINDS = {"wiki": "Wiki", "se": "Q&A", "web": "Web pages", "pdf": "PDFs"}
CATEGORY_NAMES = {"anything": "Anything", "history": "History", "science": "Science", "places": "Places",
                  "people": "People", "nature": "Nature"}
LETTERS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
MAX_LIMIT = 200
_size_cache = {}


def _lib_dir(lib):
    return os.path.dirname(os.path.abspath(lib.text_dir or search.TEXT_DIR))


def _manifest(lib):
    try:
        with open(os.path.join(_lib_dir(lib), build_library.BUILD_MANIFEST), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _kind(key):
    kind = build_library.source_kind(key)
    if kind:
        return KINDS.get(kind, "Web pages")
    if key in build_library.PDF_SOURCES:
        return "PDFs"
    return "Guides"  # wikiHow


def _edition(origin):
    m = re.search(r"_(\d{4}-\d{2})[a-z]?\.zim$", str(origin or ""))
    return m.group(1) if m else None


def _limit(limit, default=50):
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return default


def _source_id(lib, source):
    row = lib.db.execute("SELECT id FROM sources WHERE key=? AND done=1", (source,)).fetchone()
    return row[0] if row else None


def sources(lib):
    """Every source in the library, largest first.

    Returns a list of dicts, one per source, with the source facts in the module docstring:
        [{"key": "wikipedia", "name": "Wikipedia", "topic": "Encyclopedia and travel", "topic_key": "encyclopedia",
          "articles": 974064, "kind": "Wiki", "official": False, "edition": "2026-03",
          "licence": "CC BY-SA 4.0, ...", "updated": False}, ...]"""
    manifest = _manifest(lib)
    built_from = manifest.get("sources") if isinstance(manifest.get("sources"), dict) else {}
    changed = set(manifest.get("changed") or ())
    names = dict(TOPICS)
    out = []
    for key, label, topic, docs, licence in lib.db.execute(
            "SELECT key, label, topic, docs, COALESCE(license, '') FROM sources WHERE done=1 ORDER BY docs DESC, key"):
        out.append({"key": key, "name": label, "topic": names.get(topic, topic), "topic_key": topic,
                    "articles": docs, "kind": _kind(key), "official": key in search.OFFICIAL_LABEL,
                    "edition": _edition((built_from.get(key) or {}).get("origin")), "licence": licence,
                    "updated": key in changed})
    return out


def _disk_bytes(folder):
    """Bytes the build's files take on disk (allocated blocks; Windows gives no block count, so there the file
    sizes), each file once."""
    total, seen = 0, set()
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".readers"]
        for name in names:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
    return total


def info(lib):
    """Facts about the library as a whole.

    Returns {"articles": 1543012, "sources": 32, "size_bytes": 21880816000, "built": "2026-11-12T12:20:52Z" or None,
    "changed": ["medlineplus", ...]}. size_bytes is what the build's folder takes on disk (text shards shared with
    the previous build by hard link count in full here, since this build needs them); it is worked out once per
    build folder per process. changed lists the keys of the sources this build converted again (see "updated" in
    sources()); a build made by `build_library.py --upgrade` converted none."""
    articles, count = lib.db.execute(
        "SELECT COALESCE(SUM(docs), 0), COUNT(*) FROM sources WHERE done=1").fetchone()
    folder = _lib_dir(lib)
    if folder not in _size_cache:
        _size_cache[folder] = _disk_bytes(folder)
    manifest = _manifest(lib)
    return {"articles": articles, "sources": count, "size_bytes": _size_cache[folder],
            "built": manifest.get("built") or None, "changed": sorted(manifest.get("changed") or ())}


def _has_sort_keys(lib):
    """True when the library has the sort keys (docs.sort_key and its index docs_source_sort); a build from before
    them is listed by raw title until `build_library.py --upgrade` adds them."""
    cache = getattr(lib, "_sort_keys", None)
    if cache is None:
        cache = bool(lib.db.execute("SELECT 1 FROM sqlite_master WHERE name='docs_source_sort'").fetchone())
        try:
            lib._sort_keys = cache
        except AttributeError:
            pass
    return cache


def _jump(after):
    """The sort key a letter of the A to Z bar starts at: "M" -> "m"; "#" -> the start (None)."""
    if after == cleanup.OTHERS_MARK:
        return None
    if len(after) == 1 and after.upper() in LETTERS:
        return after.lower()
    return after


def titles(lib, source, after=None, after_id=None, limit=50):
    """One page of a source's titles in the Library page's order (cleanup.sort_key(): case, accents, leading
    punctuation and wikiHow's "How to" ignored, numbers by value), by keyset paging on docs_source_sort.

    source: a build key. after, after_id: where the page starts.
        both None           from the first title
        after only          a letter of the A to Z bar ("M"; "#" is the start) jumps to the first title under it;
                            any other text is a sort key: titles that sort at or after it
        after and after_id  titles after the row (after, after_id): the "next" of the page before; an after_id
                            that is not a number is ignored (as if only `after` were given)
    limit: titles per page, 1 to MAX_LIMIT (default 50).
    Returns {"source": key, "titles": [{"doc_id": 123, "title": "Malaria", "letter": "M"}, ...],
             "next": {"after": "<sort key>", "after_id": 456} or None when this is the last page},
    or None when the source is not in the library. "letter" is the A to Z bar's letter the title sits under
    ("A" to "Z" or "#"); title is as the app shows it (cleanup.display_title()). A library built before the sort
    keys is listed by title ignoring case, as before."""
    source_id = _source_id(lib, source)
    if source_id is None:
        return None
    limit = _limit(limit)
    try:
        after_id = None if after_id is None else int(after_id)
    except (TypeError, ValueError):
        after_id = None  # not a number: page from `after` alone, as if no id were given
    if after is not None and after_id is None:
        after = _jump(str(after))
    if _has_sort_keys(lib):
        col, cmp = "sort_key", ""
    else:
        col, cmp = "title", " COLLATE NOCASE"
    order = f"ORDER BY {col}{cmp}, id LIMIT ?"
    if after is None:
        rows = lib.db.execute(f"SELECT id, title, {col} FROM docs WHERE source_id=? {order}",
                              (source_id, limit + 1)).fetchall()
    elif after_id is None:
        rows = lib.db.execute(f"SELECT id, title, {col} FROM docs WHERE source_id=? AND {col} >= ?{cmp} {order}",
                              (source_id, str(after), limit + 1)).fetchall()
    else:
        rows = lib.db.execute(
            f"SELECT id, title, {col} FROM docs WHERE source_id=? AND {col} >= ?{cmp} "
            f"AND ({col} > ?{cmp} OR id > ?) {order}",
            (source_id, str(after), str(after), after_id, limit + 1)).fetchall()
    page = rows[:limit]
    nxt = {"after": page[-1][2], "after_id": page[-1][0]} if len(rows) > limit else None
    out = []
    for i, t, k in page:
        letter = cleanup.sort_letter(k) if col == "sort_key" else cleanup.sort_letter(cleanup.sort_key(None, t))
        out.append({"doc_id": i, "title": cleanup.display_title(source, t), "letter": letter})
    return {"source": source, "titles": out, "next": nxt}


def letters(lib, source):
    """The A to Z bar's buttons that have titles in the source: "#" first when some title sits under no letter A
    to Z (a digit, or a script with no A to Z equivalent, once cleanup.sort_key() has dropped leading punctuation,
    accents and wikiHow's "How to"), then the letters.

    Returns ["#", "A", "B", "D", ...] in order, or None when the source is not in the library. On a library built
    before the sort keys, "#" stands for the titles that sort before "A" ignoring case."""
    source_id = _source_id(lib, source)
    if source_id is None:
        return None
    found = []
    if _has_sort_keys(lib):
        if lib.db.execute("SELECT 1 FROM docs WHERE source_id=? AND sort_key < 'a' LIMIT 1",
                          (source_id,)).fetchone():
            found.append(cleanup.OTHERS_MARK)
        for letter, nxt in zip(LETTERS, LETTERS[1:] + ("{",)):
            if lib.db.execute("SELECT 1 FROM docs WHERE source_id=? AND sort_key >= ? AND sort_key < ? LIMIT 1",
                              (source_id, letter.lower(), nxt.lower())).fetchone():
                found.append(letter)
        return found
    if lib.db.execute("SELECT 1 FROM docs WHERE source_id=? AND title < 'a' COLLATE NOCASE LIMIT 1",
                      (source_id,)).fetchone():
        found.append(cleanup.OTHERS_MARK)
    # NOCASE compares ASCII letters as lower case, so each range is [letter, next letter) in lower case terms and
    # Z's upper bound is "{", the character after "z" (not "[", which sorts before every lower-case letter).
    for letter, nxt in zip(LETTERS, LETTERS[1:] + ("{",)):
        if lib.db.execute("SELECT 1 FROM docs WHERE source_id=? AND title >= ? COLLATE NOCASE "
                          "AND title < ? COLLATE NOCASE LIMIT 1", (source_id, letter, nxt)).fetchone():
            found.append(letter)
    return found


def _like_prefix(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def find_titles(lib, query, sources=None, official=False, limit=50):
    """Titles matching a query across the library: titles that start with it first (alphabetical), then titles
    holding all its words.

    query: the text typed (case is ignored). sources: None for every source, or a list of build keys to search in.
    official: True to keep only sources with the "Official" label. limit: results, 1 to MAX_LIMIT (default 50).
    Returns [{"doc_id": 123, "title": "Water purification", "source": "wikipedia", "source_name": "Wikipedia",
    "official": False}, ...], at most limit long, each document once; [] for an empty query."""
    query = " ".join(str(query or "").split())
    if not query:
        return []
    limit = _limit(limit)
    keys = {k: (sid, label) for sid, k, label in lib.db.execute("SELECT id, key, label FROM sources WHERE done=1")}
    allowed = {k for k in keys if (sources is None or k in sources) and (not official or k in search.OFFICIAL_LABEL)}
    if not allowed:
        return []
    by_id = {sid: (k, label) for k, (sid, label) in keys.items()}
    out, seen = [], set()

    def add(doc_id, title, source_id):
        key, label = by_id.get(source_id, (None, None))
        if key in allowed and doc_id not in seen:
            seen.add(doc_id)
            out.append({"doc_id": doc_id, "title": cleanup.display_title(key, title), "source": key,
                        "source_name": label, "official": key in search.OFFICIAL_LABEL})
    pattern = _like_prefix(query)
    if len(allowed) <= 3:  # a few sources: their own title index
        for key in sorted(allowed):
            for doc_id, title, sid in lib.db.execute(
                    "SELECT id, title, source_id FROM docs WHERE source_id=? AND title LIKE ? ESCAPE '\\' "
                    "ORDER BY title COLLATE NOCASE LIMIT ?", (keys[key][0], pattern, limit)):
                add(doc_id, title, sid)
        out.sort(key=lambda r: (r["title"].lower(), r["doc_id"]))
        del out[limit:]
    else:
        for doc_id, title, sid in lib.db.execute(
                "SELECT id, title, source_id FROM docs WHERE title LIKE ? ESCAPE '\\' "
                "ORDER BY title COLLATE NOCASE LIMIT ?", (pattern, limit * 20)):
            add(doc_id, title, sid)
            if len(out) >= limit:
                break
    words = search.query_words(query)
    if len(out) < limit and words:
        terms = " ".join('"' + w.replace('"', "") + '"*' for w in words)
        try:
            rows = lib.db.execute(
                "SELECT d.id, d.title, d.source_id FROM fts JOIN passages p ON p.id = fts.rowid "
                "JOIN docs d ON d.id = p.doc_id WHERE fts MATCH ? LIMIT 600", (f"title : ({terms})",)).fetchall()
        except sqlite3.Error:
            rows = []
        for doc_id, title, sid in rows:
            low = title.lower()
            if all(re.search(r"\b" + re.escape(w), low) for w in words):
                add(doc_id, title, sid)
                if len(out) >= limit:
                    break
    return out[:limit]


def first_passage(lib, doc_id):
    """The first passage of a document, so a title can open its article.

    Returns {"doc_id": 123, "passage_id": 4567, "title": "Malaria", "source": "wikipedia", "source_name":
    "Wikipedia", "official": False, "text": "the passage's text"}, or None when there is no such document.
    passage_id is what search.Library.document() and .read() take to show the whole article."""
    try:
        doc_id = int(doc_id)
    except (TypeError, ValueError):
        return None
    row = lib.db.execute(
        "SELECT d.title, s.key, s.label, d.file_id FROM docs d JOIN sources s ON s.id = d.source_id WHERE d.id=?",
        (doc_id,)).fetchone()
    if not row:
        return None
    passage = lib.db.execute("SELECT id, offset, length FROM passages WHERE doc_id=? ORDER BY id LIMIT 1",
                             (doc_id,)).fetchone()
    if not passage:
        return None
    title, key, label, file_id = row
    return {"doc_id": doc_id, "passage_id": passage[0], "title": cleanup.display_title(key, title), "source": key,
            "source_name": label,
            "official": key in search.OFFICIAL_LABEL, "text": lib._read(file_id, passage[1], passage[2])}


def random_articles(lib, category="anything", count=1, rng=None):
    """Random readable articles for "Something to read", from the sources in categories.READING_SOURCES.

    category: "anything" or one of categories.CATEGORIES ("history", "science", "places", "people", "nature").
    count: how many, 1 to 50 (anything else that is not a number counts as 1). rng: a random.Random to draw with (tests pass a seeded one).
    Returns [{"doc_id": 123, "passage_id": 4567, "title": "Barbary Coast", "source": "wikipedia",
    "source_name": "Wikipedia", "category": "places"}, ...], different articles, up to count long ([] for an
    unknown category or a library built before the categories). "category" is the article's own category
    ("other" is possible with "anything")."""
    rng = rng or random.SystemRandom()
    try:
        count = max(1, min(int(count or 1), 50))
    except (TypeError, ValueError):
        count = 1
    if category != "anything" and category not in categories.CATEGORIES:
        return []
    try:
        if category == "anything":
            lo, hi = lib.db.execute("SELECT MIN(doc_id), MAX(doc_id) FROM reading").fetchone()
        else:
            lo, hi = lib.db.execute("SELECT MIN(doc_id), MAX(doc_id) FROM reading WHERE category=?",
                                    (category,)).fetchone()
    except sqlite3.OperationalError:  # no reading table: a build from before the categories
        return []
    if lo is None:
        return []
    picked, out = set(), []
    for _ in range(count * 4):
        start = rng.randint(lo, hi)
        if category == "anything":
            row = lib.db.execute("SELECT doc_id, category FROM reading WHERE doc_id >= ? ORDER BY doc_id LIMIT 1",
                                 (start,)).fetchone()
        else:
            row = lib.db.execute("SELECT doc_id, category FROM reading WHERE category=? AND doc_id >= ? "
                                 "ORDER BY doc_id LIMIT 1", (category, start)).fetchone()
        if not row or row[0] in picked:
            continue
        picked.add(row[0])
        first = first_passage(lib, row[0])
        if first:
            out.append({"doc_id": first["doc_id"], "passage_id": first["passage_id"], "title": first["title"],
                        "source": first["source"], "source_name": first["source_name"], "category": row[1]})
        if len(out) >= count:
            break
    return out
