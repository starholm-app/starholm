"""Categories for "Something to read": History, Science, Places, People, Nature (BUILD-SPEC.md section 10).

The originals are deleted after conversion, so the categories come from what the built library stores: each
article's title and the opening of its text (the infobox lines the converter keeps, then the lead paragraph).
classify() applies simple rules to those; build_reading() stores the result for the sources in READING_SOURCES in
two small tables of the build's index:

    reading(category TEXT, doc_id INT, PRIMARY KEY(category, doc_id)) WITHOUT ROWID
        one row per readable article; category is one of CATEGORIES, or "other" for a readable article that fits
        none (it still counts for "Anything")
    reading_done(key TEXT PRIMARY KEY, version INT)
        the sources classified, and with which version of the rules (VERSION)

Disambiguation pages, lists and very short articles get no row at all. A source is classified again when it is
converted again (build_library.drop_source() removes its rows) or when VERSION changes.
"""
import re

VERSION = 1
# Sources "Something to read" draws on. Wikivoyage would belong here too, but the library has no Wikivoyage yet.
READING_SOURCES = ("wikipedia",)
CATEGORIES = ("history", "science", "places", "people", "nature")
OTHER = "other"
MIN_BYTES = 3000      # an article shorter than this (header included) is too short to read for pleasure
LEAD_BYTES = 2500     # how much of the opening is read to classify an article

SKIP_TITLE = re.compile(r"^(lists? of|index of|outline of|glossary of|timeline of|bibliography of|"
                        r"discography of|filmography of)\b|\(disambiguation\)$", re.I)
SKIP_LEAD = re.compile(r"\bmay (also )?refer to\b|\bmay stand for\b|\bis a list of\b|\bthis is a list of\b", re.I)

# Infobox lines (the converter keeps them as "Label | value" lines before the lead).
TAXOBOX = re.compile(r"^Kingdom\s*\|\s*\S|Scientific classification|Binomial name", re.M)
BORN = re.compile(r"^(Born|Died)(\s*\||\s*$)", re.M)
COORDS = re.compile(r"^Coordinates:", re.M)
LIFESPAN = re.compile(r"\((?:[^()]*;\s*)?(?:born\b|b\. |c\. ?\d|died\b|d\. |fl\. |\d{3,4}\s*[–-]\s*\d{2,4}\)|"
                      r"\d{1,2} [A-Z][a-z]+ \d{3,4}\s*[–-]|[A-Z][a-z]+ \d{1,2}, \d{3,4}\s*[–-])")
HATNOTE = re.compile(r"^(This article is about|For other uses|For the |Not to be confused|See also|"
                     r"\"?[A-Z][^.]{0,60}\"? redirects here|In this [A-Z][\w -]{0,40} name\b)")

# Head nouns of the definition ("is a [small coastal] town in ..."), by category. POP (popular culture,
# organisations, events) only ever counts for "Anything".
POP = (r"albums?|singles?|songs?|films?|movies?|television|tv|series|sitcoms?|soap opera|video games?|games?|"
       r"novels?|books?|fiction|bands?|duo|group|football club|club|teams?|episodes?|musicals?|plays?|"
       r"operas?|comics?|manga|anime|characters?|magazines?|newspapers?|radio station|channel|record label|"
       r"companies|company|corporation|firm|brand|software|websites?|podcasts?|competitions?|tournaments?|"
       r"leagues?|seasons?|championships?|awards?|honou?rs?|medals?|elections?|referendums?|party|cars?|"
       r"automobiles?|vehicles?|saloons?|sedans?|airliners?|aircraft|tanks?|schools?|college|university|"
       r"organi[sz]ation|association|act|statute|match|race|marathon|event|pay-per-view|dish|cuisine|cheese|"
       r"beer|brewery|squadron|unit|franchise|council|legislature|government|ministry|department|agency|"
       r"armed forces|discography|instrument|title|peerage|diocese|order|electorate|electoral district|"
       r"congressional district|constituency|people|ethnic group|religion|movement|ideology|philosophy|"
       r"concept|term|idiom|phrase|style|genre|sport|ghost|deity|god|goddess|spirit|legend|myth|folklore")
PERSON = (r"kings?|queens?|emperor|empress|pharaoh|sultan|caliph|prince|princess|duke|duchess|count|countess|"
          r"monarch|ruler|chieftain|general|admiral|bishop|archbishop|pope|saint|monk|nun|priest|poet|writer|"
          r"author|painter|sculptor|composer|scholar|philosopher|scientist|physicist|chemist|mathematician|"
          r"astronomer|physician|explorer|warrior|soldier|statesman|politician|nobleman|noblewoman|merchant|"
          r"architect|inventor|engineer|artist|actor|actress|singer|musician|player|footballer|athlete")
NATURE = (r"species|subspecies|genus|genera|clade|breed|cultivar|plants?|trees?|shrubs?|herbs?|"
          r"fungus|fungi|mushrooms?|lichens?|mosses|ferns?|grass(?:es)?|algae|birds?|mammals?|"
          r"reptiles?|amphibians?|fish(?:es)?|frogs?|toads?|snakes?|lizards?|turtles?|insects?|moths?|"
          r"butterfl(?:y|ies)|beetles?|spiders?|mites?|snails?|slugs?|molluscs?|crustaceans?|crabs?|sharks?|"
          r"whales?|dolphins?|bats?|rodents?|dinosaurs?|animals?|ecosystems?|ecoregions?|forests?|rivers?|"
          r"streams?|creeks?|lakes?|mountains?|mountain range|volcano(?:es)?|glaciers?|waterfalls?|caves?|"
          r"deserts?|wetlands?|swamps?|reefs?|bays?|valleys?|tributary|tributaries|estuary|peaks?|hills?|"
          r"oceans?|seas?|straits?|lagoons?|plateaus?|nature reserve|wildlife|storms?|hurricanes?|"
          r"typhoons?|cyclones?|tornado(?:es)?|earthquakes?|weather|climate")
PLACES = (r"towns?|city|cities|villages?|hamlets?|communes?|comune|municipality|municipalities|suburbs?|"
          r"neighbou?rhoods?|boroughs?|districts?|county|counties|provinces?|prefecture|regions?|states?|"
          r"countries|country|capital|census-designated place|unincorporated community|community|settlements?|"
          r"parish|cantons?|oblast|township|territory|metropolitan area|quarter|locality|"
          r"port|seaport|archipelago|islands?|atoll|peninsula|street|road|avenue|square|highway|route|airport|"
          r"station|castle|fortress|fort|cathedral|church|mosque|temple|monastery|abbey|cemetery|"
          r"palace|bridge|museum|park|stadium|tower|building|house|mansion|lighthouse|"
          r"harbou?r|canal|dam|reservoir|ruins|site|landmark|monument|resort")
HISTORY = (r"battles?|wars?|siege|sieges|revolution|rebellion|revolt|uprising|treaty|ancient|medieval|"
           r"archaeological|civili[sz]ation|crusades?|massacre|expedition|military campaign|coup|"
           r"conquest|invasion|prehistoric|bronze age|iron age|neolithic|byzantine|viking|feudal|"
           r"assassination|shipwreck|plague|famine|pogrom|genocide|insurrection|mutiny|skirmish|"
           r"engagement|offensive|operation|raid|military operation|history")
HISTORY_PAST = (r"empire|dynasty|caliphate|sultanate|kingdom|principality|duchy|colony|polity|state|confederation|"
                r"army|regiment|battalion|division|fleet|warship|battleship|cruiser|destroyer|frigate|"
                r"submarine|ship of the line|ship|vessel|steamship|monarchy|republic|tribe|culture")
SCIENCE = (r"chemical|compounds?|elements?|isotopes?|molecules?|proteins?|enzymes?|genes?|hormones?|cells?|"
           r"bacteri(?:um|a)|virus(?:es)?|theorem|conjecture|equations?|chemistry|physics|biology|astronomy|"
           r"geology|galax(?:y|ies)|nebula|star|binary star|star system|supergiant|"
           r"asteroids?|comets?|planets?|exoplanets?|extrasolar planet|dwarf planet|natural satellite|moons?|"
           r"craters?|constellation|particles?|algorithms?|scientific theory|diseases?|syndrome|disorders?|"
           r"infections?|drugs?|medications?|vaccines?|bones?|muscles?|nerves?|arter(?:y|ies)|"
           r"distribution|scientific instrument|programming language|telescopes?|spacecraft|satellites?|"
           r"space probes?|observatory|rockets?|alloys?|acids?|polymers?|minerals?|gemstones?|"
           r"impact structure|matri(?:x|ces)|reaction|neurotransmitter|organelle|tissue|vitamin|"
           r"amino acid|fatty acid|metal|semiconductor|laser|transistor|circuit|catalyst|fossil")

# The noun phrase ends at the first of these words or punctuation: "a town in the Clarence Valley" -> "town".
NP_END = re.compile(r"[,;:()]|\s(?:of|in|on|at|from|for|by|with|within|that|which|who|whose|where|located|founded|"
                    r"based|built|made|known|named|near|between|during|since|until|to|off|along|under|"
                    r"situated|consisting|containing|produced|developed|written|directed|released|used)\b")
HEADS = ((OTHER, POP), ("people", PERSON), ("nature", NATURE), ("history", HISTORY), ("history-past", HISTORY_PAST),
         ("places", PLACES), ("science", SCIENCE))
_cache = {}


def _noun_phrase(what):
    """The definition's first noun phrase ("small coastal town", "species", "1998 American war film")."""
    m = NP_END.search(what)
    return (what[:m.start()] if m else what[:120]).strip()


def _head_category(phrase, past=True):
    """The category of the last head noun in phrase (the noun the phrase is about), or None. The HISTORY_PAST
    nouns count only when the lead is in the past tense (past): "was an empire", not "is a state"."""
    best = None
    for n, (name, pattern) in enumerate(HEADS):
        if name == "history-past" and not past:
            continue
        if pattern not in _cache:
            _cache[pattern] = re.compile(r"\b(?:%s)\b" % pattern, re.I)
        for m in _cache[pattern].finditer(phrase):
            key = (m.end(), -n)
            if best is None or key > best[0]:
                best = (key, name)
    return best[1] if best else None


def opening(raw):
    """The article's opening without its "# title" and "Source:" header lines."""
    lines = raw.split("\n")
    while lines and (lines[0].startswith("# ") or lines[0].startswith("Source: ") or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines)


def lead(text, title=""):
    """The lead sentence: the first paragraph that reads like prose ("X is a ..."), preferring one that names the
    article's subject, cut to its first sentence."""
    first = re.sub(r"\s*\(.*\)$", "", title or "").split(",")[0].split()
    subject = first[0].lower() if first else ""
    fallback = ""
    for para in re.split(r"\n{2,}", text):
        para = " ".join(para.split())
        if len(para) < 40 or "|" in para or para.startswith(("#", "- ", "Coordinates")) or HATNOTE.match(para):
            continue
        if re.search(r"\b(is|was|are|were|refers?|may refer)\b", para):
            m = re.search(r"^(.{40,}?[a-z0-9)\]][.!?])\s+[A-Z]", para)
            sentence = (m.group(1) if m else para)[:400]
            if not subject or subject in sentence.lower()[:120]:
                return sentence
            fallback = fallback or sentence
    return fallback


def _definition(sentence):
    """What follows the first "is a", "was an", "are the" and so on: the noun phrase that says what the thing is."""
    m = re.search(r"\b(?:is|was|are|were)\b\s+(?:(?:one|any|each) of\s+)?(?:(?:a|an|the|two|three|several)\s+)?"
                  r"(.{0,200})", sentence)
    return m.group(1) if m else ""


def classify(title, raw, length=None):
    """The category of one article, OTHER, or None to leave it out.

    title: the document title. raw: the start of the document as stored (the "# title" header, the "Source:"
    line, then the text; LEAD_BYTES of it is plenty). length: the document's stored length in bytes, if known.
    Returns one of CATEGORIES, OTHER, or None for a disambiguation page, a list, or an article shorter than
    MIN_BYTES."""
    title = title or ""
    if length is not None and length < MIN_BYTES:
        return None
    if SKIP_TITLE.search(title):
        return None
    text = opening(raw)
    sentence = lead(text, title)
    if SKIP_LEAD.search(sentence) or SKIP_LEAD.search(text[:300]):
        return None
    cut = text.find(sentence[:40]) if sentence else -1
    head = text[:cut] if cut > 0 else text[:1200]
    what = _definition(sentence)
    past = bool(re.search(r"\b(was|were)\b", sentence[:200]))
    if BORN.search(head) or LIFESPAN.search(sentence[:250]):
        return "people"
    if TAXOBOX.search(head):
        return "nature"
    if re.match(r"(Battle|Siege|Treaty|Sack|Fall|Massacre|Conquest|History|Raid|Revolt|Rebellion) of\b", title):
        return "history"
    name = _head_category(_noun_phrase(what), past)
    if name == "history-past":
        name = "history"
    if name:
        return name
    if COORDS.search(head):
        return "places"
    if re.search(r"\bIn (mathematics|physics|chemistry|biology|astronomy|geology|computer science|medicine|"
                 r"statistics|probability|enzymology|biochemistry|genetics|kinematics|metrology)\b", sentence):
        return "science"
    return OTHER


SCHEMA = """
    CREATE TABLE IF NOT EXISTS reading(category TEXT, doc_id INT, PRIMARY KEY(category, doc_id)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS reading_doc ON reading(doc_id);
    CREATE TABLE IF NOT EXISTS reading_done(key TEXT PRIMARY KEY, version INT);
"""


def ensure_schema(db):
    db.executescript(SCHEMA)


def forget_source(db, source_id):
    """Remove one source's rows (before its documents are deleted, so a reused doc id can never inherit them)."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='reading'").fetchone():
        db.execute("DELETE FROM reading WHERE doc_id IN (SELECT id FROM docs WHERE source_id=?)", (source_id,))
        db.execute("DELETE FROM reading_done WHERE key=(SELECT key FROM sources WHERE id=?)", (source_id,))


def build_reading(db, text_dir, keys=READING_SOURCES, log=print):
    """Classify every document of the sources in keys that is not classified with today's VERSION yet, reading
    each document's opening from its text shard. db: an open connection to the build's index (autocommit mode, as
    build_library.open_db() returns). Returns {key: {category: count}} for the sources it classified."""
    import os
    ensure_schema(db)
    done = dict(db.execute("SELECT key, version FROM reading_done"))
    paths = dict(db.execute("SELECT id, path FROM files"))
    result = {}
    for key in keys:
        row = db.execute("SELECT id FROM sources WHERE key=? AND done=1", (key,)).fetchone()
        if not row or done.get(key) == VERSION:
            continue
        counts = {}
        db.execute("BEGIN")
        try:
            db.execute("DELETE FROM reading WHERE doc_id IN (SELECT id FROM docs WHERE source_id=?)", row)
            handle, handle_id = None, None
            for doc_id, title, file_id, offset, length in db.execute(
                    "SELECT id, title, file_id, offset, length FROM docs WHERE source_id=? ORDER BY file_id, offset",
                    row).fetchall():
                if length < MIN_BYTES:
                    continue
                if file_id != handle_id:
                    if handle:
                        handle.close()
                    rel = paths.get(file_id)
                    handle, handle_id = (open(os.path.join(text_dir, rel), "rb") if rel else None), file_id
                if not handle:
                    continue
                handle.seek(offset)
                raw = handle.read(min(length, LEAD_BYTES)).decode("utf-8", "replace")
                category = classify(title, raw, length)
                if category:
                    db.execute("INSERT OR IGNORE INTO reading(category, doc_id) VALUES(?,?)", (category, doc_id))
                    counts[category] = counts.get(category, 0) + 1
            if handle:
                handle.close()
            db.execute("INSERT OR REPLACE INTO reading_done(key, version) VALUES(?,?)", (key, VERSION))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        log(f"categories for {key}: " + ", ".join(f"{c} {n:,}" for c, n in sorted(counts.items())), flush=True)
        result[key] = counts
    return result
