"""Turn the downloaded sources into a plain-text library plus a full-text search index, safely.

Usage: .venv/bin/python library/build_library.py [--workers N] [--dry-run] [--rebuild KEY ...]
       .venv/bin/python library/build_library.py --upgrade [--dry-run]
       .venv/bin/python library/build_library.py --reclean [--dry-run]   (library/reclean.py: the clean-up rules applied
                                                                          to the live build's text, no download)
refresh.py runs this after fetching; run it alone to rebuild from what is already downloaded.
--upgrade gives the live build the indexes and tables this code adds (needs_upgrade(): the Library page's title and
passage indexes, the "Something to read" categories) without downloading or converting anything: a new build is
made from the live one by the same carry-over, checks and swap as a refresh, and the live build becomes the previous
build. Sources whose input changed are left for the weekly refresh. With --dry-run it only says what is missing.

Each build is a new folder, data.noindex/libs/lib-<date>/ in the checkout, holding text/<source>/NNNN.md (plain-text
Markdown shards of about 16 MB) and index.sqlite (SQLite FTS5 over ~1,500-character passages). Only sources whose
origin changed since the live build are converted again; the others are carried over from it (the index is cloned
and the text shards hard-linked). The new build must pass check_build() (database integrity, no dead references, at least 90%
of each source's previous document count, and a few fixed searches) before the "current" symlink is swapped to it
atomically. The previous build is kept for one cycle (KEEP_PREVIOUS_BUILD); older ones are deleted. A failed check
leaves "current" alone. Once a good build is live, the ZIMs and the wikiHow CSV it was converted from are deleted
(retire_originals()); the live build's manifest records what each source was built from, so an unchanged source
is carried over without its file.
--rebuild KEY ... converts those sources again even if their origin did not change (known keys only, with input
on disk; refresh.py --rebuild downloads a deleted original first).
--dry-run lists the sources that would be rebuilt and builds nothing.
"""
import collections
import csv
import datetime
import hashlib
import itertools
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import categories, oscompat, pdftext, safe_fetch, titles  # noqa: E402
cleanup = titles.cleanup()  # library/cleanup.py, or plain stand-ins where it is absent (library/titles.py)
from library.passages import split_passages  # noqa: E402

LIB = None       # the build being written; set_output() points these at it
TEXT_DIR = None
DB_PATH = None
SHARD_BYTES = 16 * 1024 * 1024
MIN_CHARS = 300
MIN_RATIO = 0.9  # each source in a new build needs at least this share of its previous document count
# Searches every full library answers; a build that finds nothing for one of them is not swapped in.
CHECK_QUERIES = ("water purification", "burn first aid", "hypothermia", "bleeding wound")
# Searches that only a named source can answer: (query, keys). Each runs when one of its sources is in the build,
# and passes only if a result comes from one of them, so a build where a big ZIM came out empty cannot pass on
# wikiHow alone.
CHECK_SOURCE_QUERIES = (("photosynthesis", ("wikipedia",)),
                        ("myocardial infarction", ("wikimed", "medlineplus", "wikem")),
                        ("hurricane evacuation", ("ready-gov",)),
                        ("splint fracture", ("military-medicine", "medical-library", "wikimed")))
MIN_ZIM_SHARE = 0.1  # a ZIM source must yield at least this share of the ZIM's own article count (at least 1)
BUILD_MANIFEST = "build.json"
CHECKS_PASSED = "checks-passed.json"  # written into a build only after check_build passes, just before the swap
LIB_NAME_RE = re.compile(r"^lib-\d{4}-\d{2}-\d{2}(-\d+)?$")


def set_output(lib_dir):
    """Write the text and index into lib_dir from now on."""
    global LIB, TEXT_DIR, DB_PATH
    LIB = os.path.abspath(lib_dir)
    TEXT_DIR = os.path.join(LIB, "text")
    DB_PATH = os.path.join(LIB, "index.sqlite")


# ZIM file name (minus the date) -> (key, label shown to the model, topic, kind, URL prefix)
ZIM_SOURCES = {
    "wikipedia_en_top1m_nopic": ("wikipedia", "Wikipedia", "encyclopedia", "wiki", "https://en.wikipedia.org/wiki/"),
    "wikipedia_en_medicine_nopic": ("wikimed", "WikiMed (Wikipedia medicine)", "medical", "wiki", "https://en.wikipedia.org/wiki/"),
    "wikem_en_all_nopic": ("wikem", "WikEM emergency medicine", "medical", "wiki", "https://wikem.org/wiki/"),
    "appropedia_en_all_maxi": ("appropedia", "Appropedia", "howto", "wiki", "https://www.appropedia.org/"),
    "medlineplus.gov_en_all": ("medlineplus", "MedlinePlus (NIH)", "medical", "web", "https://"),
    "nhs.uk_en_medicines": ("nhs-medicines", "UK Medicines A to Z (public sector information)", "medical", "web",
                            "https://"),
    "wwwnc.cdc.gov_en_all": ("cdc-travel", "CDC Travelers' Health", "medical", "web", "https://"),
    "ready.gov_en_all": ("ready-gov", "Ready.gov (FEMA)", "emergency", "web", "https://"),
    "ifixit_en_all": ("ifixit", "iFixit repair guides", "repair", "web", "https://www.ifixit.com/"),
    "irp.fas.org_en_military-medicine": ("military-medicine", "Military medicine manuals", "medical", "pdf",
                                         "https://"),
    "zimgit-medicine_en": ("medical-library", "Medical Library", "medical", "pdf", ""),
    "zimgit-post-disaster_en": ("post-disaster", "Post Disaster Resource Library", "emergency", "pdf", ""),
    "zimgit-water_en": ("water", "Water Treatment Library", "emergency", "pdf", ""),
    "zimgit-food-preparation_en": ("food-prep", "Food for Preppers", "emergency", "pdf", ""),
    "zimgit-knots_en": ("knots", "Library of Knots", "howto", "pdf", ""),
    "usda-2015_en": ("usda-canning", "USDA Complete Guide to Home Canning", "emergency", "web", ""),
    "wikiciv_en_all_maxi": ("wikiciv", "WikiCiv (rebuilding technology)", "howto", "wiki", "https://wikiciv.org/wiki/"),
    "gardenology.org_en_all": ("gardenology", "Gardenology plant encyclopedia", "howto", "wiki", "https://"),
    "energypedia_en_all_nopic": ("energypedia", "Energypedia (solar, batteries, stoves)", "howto", "wiki",
                                 "https://energypedia.info/wiki/"),
}
# Standalone PDF manuals from library/fetch_manuals.py: key -> (label, topic, folder under raw/)
PDF_SOURCES = {
    "survival-manuals": ("U.S. government survival and first-aid manuals", "emergency", "manuals/us-gov"),
    "first-aid-guides": ("IFRC and WHO first-aid and emergency care guides", "medical", "manuals/nc"),
    "risk": ("Risk and micromort references", "medical", "risk"),  # from library/fetch_risk.py
}
SE_SITES = {
    "diy": ("Home Improvement Q&A", "repair"),
    "outdoors": ("The Great Outdoors Q&A", "howto"),
    "cooking": ("Cooking Q&A", "howto"),
    "gardening": ("Gardening Q&A", "howto"),
    "lifehacks": ("Lifehacks Q&A", "howto"),
    "sustainability": ("Sustainable Living Q&A", "howto"),
    "ham": ("Amateur Radio Q&A", "howto"),
    "medicalsciences": ("Medical Sciences Q&A", "medical"),
    "mechanics": ("Motor Vehicle Repair Q&A", "repair"),
    "pets": ("Pets Q&A", "howto"),
}
for site, (label, topic) in SE_SITES.items():
    ZIM_SOURCES[f"{site}.stackexchange.com_en_all"] = (
        f"se-{site}", f"{label} (Stack Exchange)", topic, "se", f"https://{site}.stackexchange.com/")

WIKI_FAMILY = {"wikipedia", "wikimed"}  # same articles can appear in both; keep one copy

# Each source's license, shown with its articles in the app (CC licenses ask for the license with every copy).
CC_BY_SA = "https://creativecommons.org/licenses/by-sa/4.0/"
CC_BY_NC_SA = "https://creativecommons.org/licenses/by-nc-sa/3.0/"
US_GOV = "Public domain (U.S. government work)"
LICENSES = {
    "wikipedia": f"CC BY-SA 4.0, Wikipedia contributors {CC_BY_SA}",
    "wikimed": f"CC BY-SA 4.0, Wikipedia contributors {CC_BY_SA}",
    "wikem": f"CC BY-SA 4.0, WikEM contributors {CC_BY_SA}",
    "appropedia": f"CC BY-SA 3.0 or 4.0 (per page), Appropedia contributors {CC_BY_SA}",
    "energypedia": f"CC BY-SA 3.0 or 4.0 (per page), Energypedia contributors {CC_BY_SA}",
    "wikiciv": f"CC BY-SA 4.0, WikiCiv contributors {CC_BY_SA}",
    "medlineplus": f"{US_GOV}, U.S. National Library of Medicine",
    "nhs-medicines": "Contains public sector information licensed under the Open Government Licence v3.0 "
                     "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    "cdc-travel": f"{US_GOV}, CDC (the printed Yellow Book is published by Oxford University Press)",
    "ready-gov": f"{US_GOV}, FEMA",
    "military-medicine": US_GOV,
    "usda-canning": f"{US_GOV}, USDA",
    "ifixit": f"CC BY-NC-SA 3.0 (non-commercial), iFixit guide authors {CC_BY_NC_SA}",
    "wikihow": f"CC BY-NC-SA 3.0 (non-commercial), wikiHow contributors {CC_BY_NC_SA}",
    "survival-manuals": f"{US_GOV}, except Nuclear War Survival Skills (1986 additions copyright Cresson H. Kearny, "
                        "reproduced with his notice) and the Emergency Response Guidebook 2024 (reproduction "
                        "permitted by the participating governments, not for resale)",
    "risk": f"U.S. government statistics (SSA, CDC, NHTSA, NWS): public domain. Wikipedia articles: {CC_BY_SA}",
    "first-aid-guides": "IFRC guidelines: non-commercial use with acknowledgement. WHO publications: CC BY-NC-SA 3.0 IGO "
                        "https://creativecommons.org/licenses/by-nc-sa/3.0/igo/ (WHO does not endorse this app)",
    **{f"se-{site}": "CC BY-SA 2.5, 3.0 or 4.0 by post date, the authors named with each post "
                     "https://stackoverflow.com/help/licensing" for site in SE_SITES},
}
# PDFs inside ZIMs that are only named by file: give them their real titles.
MILMED = "irp.fas.org/doddir/milmed/"
USDA = "files/GUIDE0{}_HomeCan_rev0715.pdf"
PDF_TITLES = {
    MILMED + "corpsman-pt1.pdf": "Hospital Corpsman (NAVEDTRA 14295), part 1",
    MILMED + "corpsman-pt2.pdf": "Hospital Corpsman (NAVEDTRA 14295), part 2",
    MILMED + "corpsman-pt3.pdf": "Hospital Corpsman (NAVEDTRA 14295), part 3",
    MILMED + "dosimetry.pdf": "Personnel Dosimetry Guidance (DA Pam 40-18)",
    MILMED + "duequip.pdf": "Handling Equipment Contaminated with Depleted Uranium (DA Pam 700-48)",
    MILMED + "first.pdf": "First Aid (FM 4-25.11)",
    MILMED + "malaria.pdf": "Navy Pocket Guide to Malaria Prevention and Control",
    MILMED + "milderm.pdf": "Military Dermatology (Textbook of Military Medicine)",
    MILMED + "preventive.pdf": "Preventive Medicine (DA Pam 40-11)",
    MILMED + "prevserv.pdf": "Preventive Medicine Services (FM 4-02.17)",
    MILMED + "rhpm.pdf": "Radiation Health Protection Manual (NAVMED P-5055)",
    MILMED + "sanitation.pdf": "Unit Field Sanitation Team (FM 4-25.12)",
    MILMED + "ships.pdf": "The Ship's Medicine Chest and Medical Aid at Sea",
    MILMED + "soldiers.pdf": "First Aid for Soldiers (FM 21-11, 1988)",
    MILMED + "sterile.pdf": "Sterilizing Medical, Surgical and Dental Equipment (Army technical bulletin)",
    MILMED + "surghand.pdf": "Division and Brigade Surgeons' Handbook (FM 4-02.21)",
    MILMED + "warpsychiatry.pdf": "War Psychiatry (Textbook of Military Medicine)",
    "files/INTRO_HomeCanrev0715.pdf": "USDA Complete Guide to Home Canning: Introduction",
    USDA.format(1): "USDA Home Canning Guide 1: Principles of Home Canning",
    USDA.format(2): "USDA Home Canning Guide 2: Selecting, Preparing, and Canning Fruit and Fruit Products",
    USDA.format(3): "USDA Home Canning Guide 3: Selecting, Preparing, and Canning Tomatoes and Tomato Products",
    USDA.format(4): "USDA Home Canning Guide 4: Selecting, Preparing, and Canning Vegetables and Vegetable Products",
    USDA.format(5): "USDA Home Canning Guide 5: Preparing and Canning Poultry, Red Meats, and Seafoods",
    USDA.format(6): "USDA Home Canning Guide 6: Preparing and Canning Fermented Foods and Pickled Vegetables",
    USDA.format(7): "USDA Home Canning Guide 7: Preparing and Canning Jams and Jellies",
}
# Sources whose files carry no usable web address: every article links to where the source is published.
FIXED_URLS = {"usda-canning": "https://nchfp.uga.edu/resources/category/usda-guide"}

# Page paths to leave out: WikEM's translated copies ("Page/tr") and sites' non-English sections, including
# language-code sections at the top of a site ("www.ready.gov/fr/..."), which often hold untranslated English copies.
FOREIGN_SECTIONS = r"^[^/]+/[a-z]{2}(-[a-z]+)?/|/(es|spanish|espanol|languages|translations)(/|$)"
SKIP_PATHS = {
    "wikem": r"/[a-z]{2}(-[a-z]+)?$",
    "medlineplus": FOREIGN_SECTIONS,
    "ready-gov": FOREIGN_SECTIONS,
    "cdc-travel": FOREIGN_SECTIONS,
}

# PDFs to leave out only when the same ZIM also has the page they print: {key: pattern}. The pattern's groups 1 and 2
# give the HTML page's path, "<1>/<2>/" or "<1>/<2>". MedlinePlus serves each genetics page as a printable PDF
# (download/genetics/condition/<name>.pdf, 2,844 of them in the 2025-01 ZIM) with the same text and a title like
# "1" or "ocular-albinism".
TWIN_PDFS = {"medlineplus": r"^([^/]+)/download/(genetics/[^/]+/[^/]+)\.pdf$"}

HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "#### ", "h6": "#### "}
BLOCKS = {"p", "div", "section", "article", "ul", "ol", "dl", "dt", "dd", "table", "pre", "blockquote",
          "figure", "figcaption", "details", "summary", "main", "center", "hr", "caption"}
DROP_XPATH = (".//script|.//style|.//noscript|.//template|.//svg|.//button|.//form|.//nav|.//footer|.//header"
              "|.//aside|.//sup[contains(@class,'reference')]")
DROP_CLASS = re.compile(
    r"\b(mw-editsection|references|mw-references-wrap|reflist|navbox|vertical-navbox|sidebar|metadata|ambox"
    r"|noprint|catlinks|zim-footer|mw-empty-elt|printfooter|mw-jump-link|toc|mw-cite-backlink|comments"
    r"|js-post-comments-component|credit-author-team-container)\b")
DROP_SECTIONS = {"references", "external links", "see also", "further reading", "notes", "sources",
                 "bibliography", "citations", "footnotes", "notes and references", "references and notes"}
TITLE_SUFFIX = re.compile(
    r"\s*[|:\-–—]\s*(NHS|Ready\.gov|FEMA|CDC|Travelers' Health|MedlinePlus[^|]*|iFixit[^|]*|Appropedia[^|]*"
    r"|Wikipedia|WikEM)\s*$", re.I)


# ---------- HTML / PDF to plain text ----------

def heading_level(el):
    return int(el.tag[1]) if isinstance(el.tag, str) and el.tag in HEADINGS else None


def drop_sections(node):
    """Remove References / External links / See also sections, whatever the markup style."""
    for h in node.xpath(".//h2|.//h3|.//h4"):
        # The list is taken up front: a heading inside a section already dropped is no longer in the article
        # (dropping it again made lxml assert, and about 0.7% of Wikipedia articles were lost that way).
        if not any(a is node for a in h.iterancestors()):
            continue
        if " ".join(h.text_content().split()).lower() not in DROP_SECTIONS:
            continue
        anchor = h
        parent = h.getparent()
        if parent.tag == "div" and "mw-heading" in (parent.get("class") or ""):
            anchor = parent  # MediaWiki 1.43+ wraps headings in <div class="mw-heading">
        wrapper = anchor.getparent()
        if wrapper is not None and wrapper.tag in ("section", "details") and len(wrapper) and wrapper[0] is anchor:
            wrapper.drop_tree()
            continue
        if wrapper is not None and wrapper.tag == "summary":
            if wrapper.getparent() is not None and wrapper.getparent().getparent() is not None:
                wrapper.getparent().drop_tree()
            continue
        if wrapper is None:
            continue
        level = int(h.tag[1])
        sib = anchor.getnext()
        anchor.drop_tree()
        while sib is not None:
            inner = sib if heading_level(sib) else (sib[0] if len(sib) and heading_level(sib[0]) else None)
            if inner is not None and heading_level(inner) <= level:
                break
            nxt = sib.getnext()
            sib.drop_tree()
            sib = nxt


def node_text(node):
    """Element tree to plain text, keeping headings, numbered steps, bullets and table rows."""
    for bad in node.xpath(DROP_XPATH):
        bad.drop_tree()
    for el in node.xpath(".//*[@class]"):
        if DROP_CLASS.search(el.get("class")):
            el.drop_tree()
    for ol in node.iter("ol"):
        for n, li in enumerate((c for c in ol if c.tag == "li"), 1):
            li.set("data-n", str(n))
    for el in node.iter():
        if not isinstance(el.tag, str):
            continue
        t = el.tag
        if t in HEADINGS:
            el.text = "\n\n" + HEADINGS[t] + (el.text or "")
            el.tail = "\n\n" + (el.tail or "")
        elif t == "li":
            el.text = (f"\n{el.get('data-n')}. " if el.get("data-n") else "\n- ") + (el.text or "")
        elif t == "tr":
            el.text = "\n" + (el.text or "")
        elif t in ("td", "th"):
            el.tail = " | " + (el.tail or "")
        elif t == "br":
            el.tail = "\n" + (el.tail or "")
        elif t in BLOCKS:
            el.text = "\n\n" + (el.text or "")
            el.tail = "\n\n" + (el.tail or "")
    lines = []
    for line in node.text_content().split("\n"):
        line = re.sub(r"[ \t ​]+", " ", line).strip()
        line = re.sub(r"(\s*\|)+\s*$", "", line)
        if line in ("-", "#", "##", "###", "####") or re.fullmatch(r"\d+\.", line):
            line = ""
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def clean_title(title):
    return TITLE_SUFFIX.sub("", " ".join(title.split())).strip()


def html_to_text(html, kind):
    import lxml.html
    doc = lxml.html.fromstring(html)
    if kind == "se":
        q = doc.xpath("//div[@id='question']")
        if not q:
            return None
        body = q[0].xpath(".//div[contains(@class,'js-post-body')]")
        answers = []
        for a in doc.xpath("//div[contains(concat(' ', @class, ' '), ' answer ')][@data-score]"):
            a_body = a.xpath(".//div[contains(@class,'js-post-body')]")
            if a_body:
                accepted = "accepted-answer" in (a.get("class") or "")
                answers.append((int(a.get("data-score") or 0), accepted, a_body[0]))
        if not body or not answers:
            return None  # unanswered questions are not worth the space
        answers.sort(key=lambda x: (not x[1], -x[0]))
        parts = [f"Question{se_author(q[0], question=True)} (score {q[0].get('data-score', '0')}):\n\n"
                 + node_text(body[0])]
        for score, accepted, a_body in answers[:5]:
            if score < 0:
                continue
            post = a_body.xpath("ancestor::div[@data-score][1]")
            by = se_author(post[0]) if post else ""
            parts.append(f"## Answer{by} (score {score}{', accepted' if accepted else ''})\n\n" + node_text(a_body))
        return "\n\n".join(parts)
    if kind == "wiki":
        found = doc.xpath("//*[@id='mw-content-text']") or doc.xpath("//*[contains(@class,'mw-parser-output')]")
        if found:  # otherwise (e.g. a scraped wiki) fall through to the general page extraction
            node = found[0]
            drop_sections(node)
            return node_text(node)
    # Scraped websites: take <main> or <article>; fall back to trafilatura's boilerplate removal.
    author = doc.xpath("//div[contains(@class,'guide-author')]")  # iFixit: "Written By: <name>"
    author = " ".join(author[0].text_content().split()) if author else ""
    found = doc.xpath("//main") or doc.xpath("//article") or doc.xpath("//*[@id='content']")
    text = node_text(found[0]) if found else ""
    if author and text:
        text = f"{author}\n\n{text}"
    if len(text) < MIN_CHARS:
        import trafilatura
        text = trafilatura.extract(html, include_tables=True, include_comments=False) or ""
    return text


def se_author(post, question=False):
    """ ' by <name>' for a Stack Exchange post (its license asks for the author's name), or ''."""
    sigs = post.xpath(".//div[contains(@class,'post-signature')]")
    if question:
        sigs = [s for s in sigs if "owner" in (s.get("class") or "")] or sigs
    name = sigs[-1].xpath(".//*[contains(@class,'s-user-card--link')]") if sigs else []
    name = " ".join(name[0].text_content().split()) if name else ""
    return f" by {name}" if name else ""


def poppler_tool(name):
    """A poppler program: the one on PATH, else Homebrew's on Apple silicon or on Intel (launchd's PATH may lack
    /usr/local/bin), else the Apple silicon path, which then fails loudly as a missing converter."""
    for path in (shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return f"/opt/homebrew/bin/{name}"


PDFTOTEXT = pdftext.PDFTOTEXT  # from Homebrew's poppler
PDFTOTEXT_TIMEOUT = 3 * pdftext.TIMEOUT + pdftext.PY_BUDGET  # seconds per PDF (three pdftotext runs and the page
                                                             # analysis), so 50 PDFs always finish inside RANGE_TIMEOUT


PDFINFO = poppler_tool("pdfinfo")  # also from poppler


def pdf_to_text(data, meta=False, raw=False):
    """A PDF's text layer ("" for a scan without one), page by page with its page furniture out and its lists and
    columns in reading order (library/pdftext.py). Raises if pdftotext is missing, fails on the file or runs past
    its timeout, so a broken converter is counted as an error, never mistaken for empty PDFs. meta=True returns
    (text, the title in the PDF's metadata, or "" when it has none or pdfinfo cannot read it). raw=True (the free
    library) is pdftotext's own text as it comes, running headers, page labels and broken lines included."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        text = raw_pdf_text(f.name) if raw else pdftext.convert_path(f.name)
        title = pdf_meta_title(f.name) if meta else ""
    return (text, title) if meta else text


def raw_pdf_text(path):
    """pdftotext's plain output for the free library's raw build, with none of library/pdftext.py's page work."""
    r = subprocess.run([PDFTOTEXT, "-enc", "UTF-8", path, "-"], capture_output=True, timeout=pdftext.TIMEOUT)
    if r.returncode:
        raise RuntimeError(f"pdftotext failed (exit {r.returncode})")
    return r.stdout.decode("utf-8", "replace").replace("\f", "\n")


def pdf_meta_title(path):
    """The Title field of a PDF's metadata, or "" (none, or pdfinfo missing or failing: a title is not worth an
    error)."""
    try:
        r = subprocess.run([PDFINFO, "-enc", "UTF-8", path], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("Title:"):
            return line[len("Title:"):].strip()
    return ""


# ---------- ZIM workers ----------

_ARCHIVE = None
_KIND = None
_SKIP = None
_TWIN = None
_RAW = False


def _init_worker(path, kind, skip=None, twin=None, raw=False):
    global _ARCHIVE, _KIND, _SKIP, _TWIN, _RAW
    from libzim.reader import Archive
    oscompat.lower_priority()
    _ARCHIVE = Archive(path)
    _KIND = kind
    _SKIP = re.compile(skip) if skip else None
    _TWIN = re.compile(twin) if twin else None
    _RAW = raw


def _has_twin(path):
    """True if path is a PDF matching the source's TWIN_PDFS pattern and the ZIM also has the page it prints."""
    m = _TWIN.match(path) if _TWIN else None
    return bool(m) and any(_ARCHIVE.has_entry_by_path(f"{m.group(1)}/{m.group(2)}{end}") for end in ("/", ""))


class SourceError(Exception):
    """A source that cannot be converted properly (too many entries failed). The build fails; "current" stays."""


MAX_ERROR_SHARE = 0.02  # a source fails when more than this share of its entries cannot be read or converted
MIN_ERRORS = 20         # ... and more than this many (a handful of broken pages in a small ZIM is normal)
ERROR_SAMPLES = 5       # entry errors logged in full per source; the rest are counted


def too_many_errors(entries, errors):
    return errors > MIN_ERRORS and errors > entries * MAX_ERROR_SHARE


def _convert_range(bounds):
    """Convert the entries with ids in [start, end) through libzim's public API. Returns (docs, entries read,
    errors, the first few error messages, PDF copies skipped because the ZIM has their page)."""
    out, errors, samples, twins = [], 0, [], 0
    for i in range(*bounds):
        try:
            e = _ARCHIVE.get_entry_by_id(i)
            if e.is_redirect or (_SKIP and _SKIP.search(e.path)) or re.match(r"(?i)h?ttps?:", e.path):
                continue
            if _has_twin(e.path):
                twins += 1
                continue
            item = e.get_item()
            mime = item.mimetype.split(";")[0]
            if mime == "application/pdf" and _KIND in ("pdf", "web"):
                title = e.title if e.title and e.title != e.path else os.path.basename(e.path)
                title = re.sub(r"\.pdf$", "", title.replace("_", " "), flags=re.I)
                title = PDF_TITLES.get(e.path, title)
                if _RAW:
                    text = pdf_to_text(bytes(item.content), raw=True)
                elif _KIND == "pdf" and re.search(r"\(\d+\)$", title):
                    # zimgit names files "Water (7)": the PDF's own title, its first heading, or the file name
                    text, meta = pdf_to_text(bytes(item.content), meta=True)
                    title = cleanup.pdf_title(meta, text, e.path)[0]
                else:
                    text = pdf_to_text(bytes(item.content))
            elif mime == "text/html":
                if _KIND == "se" and not e.path.startswith("questions/"):
                    continue
                text = html_to_text(bytes(item.content), _KIND)
                title = e.title or e.path
            else:
                continue
            if text and len(text) >= MIN_CHARS:
                out.append((clean_title(title), e.path, text))
        except Exception as exc:  # one bad page must not stop a million-page build, but every one is counted
            errors += 1
            if len(samples) < ERROR_SAMPLES:
                samples.append(f"entry {i}: {exc.__class__.__name__}: {str(exc)[:200]}")
    return out, bounds[1] - bounds[0], errors, samples, twins


def _workers(value):
    """A worker count from a flag or SURVIVAL_LLM_WORKERS: a whole number from 1 to 16, else the default 4."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 4
    return n if 1 <= n <= 16 else 4


# Conversion processes per ZIM. Each keeps a CPU core busy, so the default is modest for a laptop; the environment
# variable SURVIVAL_LLM_WORKERS or the --workers N flag of build_library.py and refresh.py sets another (1 to 16).
WORKERS = _workers(os.environ.get("SURVIVAL_LLM_WORKERS", 4))
WINDOW = 2 * WORKERS  # ranges in flight at once: converted text waits in memory for at most this many ranges


def set_workers(args):
    """Apply a "--workers N" in args (and remove it from args); returns the count in use."""
    global WORKERS, WINDOW
    if "--workers" in args:
        i = args.index("--workers")
        value = args[i + 1] if i + 1 < len(args) else ""
        if not value.isdigit() or not 1 <= int(value) <= 16:
            sys.exit("--workers takes a whole number from 1 to 16")
        del args[i:i + 2]
        WORKERS, WINDOW = int(value), 2 * int(value)
    return WORKERS


RANGE_TIMEOUT = 4 * 3600  # seconds to wait for one range. It only stops a lost worker from hanging the run forever,
                          # so it is generous for every kind: 50 PDFs at up to 240 s of pdftotext each is 3.3 hours


def _bounded_imap(pool, ranges, fn=None, timeout=RANGE_TIMEOUT):
    """pool.imap over ranges, but never more than WINDOW ranges submitted ahead of the consumer, so converted text
    cannot pile up in memory while SQLite writes fall behind. Results come back in order. A range whose result does
    not arrive within timeout seconds of being waited for (a worker killed by the OS or crashed inside libzim takes
    its task with it, and the pool never reports it) raises SourceError instead of waiting forever."""
    fn = fn or _convert_range
    pending = collections.deque()
    it = iter(ranges)
    for r in itertools.islice(it, WINDOW):
        pending.append((r, pool.apply_async(fn, (r,))))
    while pending:
        r, job = pending.popleft()
        try:
            result = job.get(timeout)
        except mp.TimeoutError:
            raise SourceError(f"no result for entries {r} after {timeout} s: a conversion worker died or hung") \
                from None
        nxt = next(it, None)
        if nxt is not None:
            pending.append((nxt, pool.apply_async(fn, (nxt,))))
        yield result


def zim_docs(path, kind, skip=None, twin=None, raw=False):
    """(title, path, text) for every convertible entry of a ZIM, converted in parallel. Raises SourceError when
    too many entries fail (checked as it goes, so a broken converter stops early, and again at the end).
    raw=True: PDFs inside it keep their page furniture (pdf_to_text raw)."""
    from libzim.reader import Archive
    count = Archive(path).entry_count
    step = 50 if kind in ("pdf", "web") else 1000  # web ZIMs hold PDFs too (Ready.gov, CDC, MedlinePlus, USDA)
    ranges = [(s, min(s + step, count)) for s in range(0, count, step)]
    entries = errors = twins = 0
    logged = []
    name = os.path.basename(path)
    pool = mp.Pool(WORKERS, initializer=_init_worker, initargs=(path, kind, skip, twin, raw))
    try:
        for batch, n, bad, samples, copies in _bounded_imap(pool, ranges):
            entries, errors, twins = entries + n, errors + bad, twins + copies
            for line in samples[:ERROR_SAMPLES - len(logged)]:
                logged.append(line)
                print(f"    {name}: {line}", flush=True)
            if entries >= 2000 and too_many_errors(entries, errors):
                raise SourceError(f"{name}: {errors:,} of the first {entries:,} entries failed")
            yield from batch
    except SourceError as exc:
        raise SourceError(f"{name}: {exc}" if not str(exc).startswith(name) else str(exc)) from None
    finally:  # on every path, a timeout included: no worker outlives the source
        pool.terminate()
        pool.join()
    if twins:
        print(f"    {name}: skipped {twins:,} PDF copies of pages the ZIM also has", flush=True)
    if errors:
        print(f"    {name}: {errors:,} of {entries:,} entries could not be converted", flush=True)
    if too_many_errors(entries, errors):
        raise SourceError(f"{name}: {errors:,} of {entries:,} entries failed")


def manual_docs(folder, skip_wikipedia=False, raw=False):
    """Files listed in folder/manifest.json (written by fetch_manuals.py or fetch_risk.py): PDFs, web pages, text.
    Copies of Wikipedia articles are skipped when the build has Wikipedia itself (skip_wikipedia). raw=True: PDFs as
    pdftotext gives them (the free library)."""
    with open(os.path.join(folder, "manifest.json")) as f:
        items = json.load(f)
    for item in items:
        if item.get("wikipedia") and skip_wikipedia:
            continue
        name = os.path.basename(str(item.get("file", "")))
        if not name or name != item["file"]:  # a manifest names files in its own folder only
            continue
        with open(os.path.join(folder, name), "rb") as f:
            data = f.read()
        if name.endswith(".pdf"):
            text = pdf_to_text(data, raw=raw)
        elif name.endswith((".html", ".htm")):
            text = html_to_text(data, item.get("kind", "web")) or ""
        else:
            text = data.decode("utf-8", "replace")
        if len(text) >= MIN_CHARS:
            yield item["title"], item["url"], text
        else:
            print(f"    no text layer (needs OCR): {item['title']}", flush=True)


WIKIHOW_FIELD_LIMIT = 4 << 20  # 4 MB: the longest real article is far smaller; a stray quote is not


class _Lines:
    """The CSV's lines, read with their own endings (newline=""), so the reader can skip to the start of the next
    record. In wikihowAll.csv a record ends with \r\n while line breaks inside a field are a bare \n; the
    header's ending says which one ends records."""

    def __init__(self, f):
        self.f, self.last, self.record_end = f, "", None

    def __iter__(self):
        return self

    def __next__(self):
        self.last = next(self.f)
        if self.record_end is None:  # the header line
            self.record_end = "\r\n" if self.last.endswith("\r\n") else "\n"
        return self.last

    def skip_to_record_start(self):
        """Read on until the last line read ended a record, so the next line read starts one."""
        while not self.last.endswith(self.record_end or "\n"):
            self.last = next(self.f)


def wikihow_rows(path, field_limit=WIKIHOW_FIELD_LIMIT, max_skips=1000):
    """The CSV's rows. A row with a field over field_limit (one unterminated quote would swallow the rest of the
    file into a single field) is skipped with a log line, and reading resumes at the start of the next record."""
    csv.field_size_limit(field_limit)
    skipped = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        lines = _Lines(f)
        reader = csv.DictReader(lines)
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error as exc:
                skipped += 1
                print(f"    wikiHow: skipped a row near line {reader.reader.line_num}: {exc}", flush=True)
                if skipped >= max_skips:
                    print(f"    wikiHow: {skipped} bad rows; the rest of the file is left out", flush=True)
                    break
                try:
                    lines.skip_to_record_start()
                except StopIteration:
                    break
                continue
            yield row


def wikihow_titles(raw_titles):
    """{raw title: (article title, section number)} for the dump's titles. A section counter is trailing digits
    that, among all titles sharing the same stem, run 1, 2, ... n with n of at least 2. Every split of the digits
    is tried, and among the splits that fit, the stem with the longest run wins (ties go to the shorter counter):
    so "Stem11" and "Stem12" are sections 11 and 12 of a 12-section "Stem", not of a ghost "Stem1"; "How to Learn
    About World War I1" .. "I3" are sections of "How to Learn About World War I"; "How to Dunk on NBA 2K141" is
    section 1 of "How to Dunk on NBA 2K14". Digits that are part of a name stay: "Sony Xperia Z1" (no "Z2"),
    "Vitamin B12" next to "B1" and "B2". Only articles with several sections carry counters in this dump, so a lone
    "1" is part of the title.
    Accepted ambiguity: separate one-section articles whose names end in a run (an "iPhone 11" next to an
    "iPhone 12", or "Vitamin B1" next to "B2") look exactly like sections of one article from the titles alone,
    and are merged into it ("iPhone 1", "Vitamin B")."""
    numbers = collections.defaultdict(set)
    for raw in raw_titles:
        m = re.search(r"\d+$", raw)
        if m:
            digits = m.group(0)
            for k in range(1, len(digits) + 1):
                if not digits[-k:].startswith("0"):
                    numbers[raw[:-k]].add(int(digits[-k:]))

    runs = {}

    def run_length(stem):
        if stem not in runs:
            n = 0
            while n + 1 in numbers.get(stem, ()):
                n += 1
            runs[stem] = n
        return runs[stem]

    out = {}
    for raw in raw_titles:
        out[raw] = (raw, 0)
        m = re.search(r"\d+$", raw)
        if not m:
            continue
        digits, best = m.group(0), None
        for k in range(1, len(digits) + 1):
            tail, stem = digits[-k:], raw[:-k]
            n = run_length(stem)
            if tail.startswith("0") or not stem.strip() or n < 2 or not 1 <= int(tail) <= n:
                continue
            if best is None or (n, -k) > best[0]:  # the longest run; on a tie, the shorter counter
                best = ((n, -k), stem.strip(), int(tail))
        if best:
            out[raw] = (best[1], best[2])
    return out


def wikihow_docs(path, raw=False):
    """One document per wikiHow article. The dump (Koupaee and Wang, 2018) has one row per section of an article
    (a method or a part), each titled with a counter ("How to Be an Organized Artist1", "...Artist2"), and the
    sections of one article are scattered through the file. A first pass reads every title and works out the
    articles (wikihow_titles); a second gathers the rows by article in a temporary SQLite file inside the build's
    own folder (so the 620 MB of text is not held in memory, and a killed run's leftovers go with the staging
    folder), and each article becomes one document with its sections in order. raw=True (the free library): the
    glued words are left as the dump has them (no cleanup.wikihow_spacing)."""
    titles = wikihow_titles([(r.get("title") or "").strip() for r in wikihow_rows(path)])
    tmpdir = tempfile.mkdtemp(prefix=".wikihow-", dir=LIB if LIB and os.path.isdir(LIB) else None)
    db = sqlite3.connect(os.path.join(tmpdir, "rows.sqlite"))
    try:
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA synchronous=OFF")
        db.execute("CREATE TABLE rows(title TEXT, part INT, n INT, headline TEXT, text TEXT)")
        for n, row in enumerate(wikihow_rows(path)):
            title, part = titles.get((row.get("title") or "").strip(), ("", 0))
            if title:
                db.execute("INSERT INTO rows VALUES(?,?,?,?,?)",
                           (title, part, n, row.get("headline") or "", row.get("text") or ""))
        db.commit()
        current, sections = None, []
        for title, headline, text in db.execute("SELECT title, headline, text FROM rows ORDER BY title, part, n"):
            if title != current:
                if current is not None:
                    yield from _wikihow_article(current, sections, raw)
                current, sections = title, []
            sections.append((headline, text))
        if current is not None:
            yield from _wikihow_article(current, sections, raw)
    finally:
        db.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _wikihow_section(headline, text, raw=False):
    """One section as numbered steps (each step's headline, then its paragraph), or the steps then the details."""
    spacing = (lambda t: t) if raw else cleanup.wikihow_spacing
    heads = [spacing(h.strip().rstrip(",").strip()) for h in headline.split("\n")]
    heads = [h for h in heads if h]
    paras = [spacing(" ".join(p.split())) for p in re.split(r";?\n,", text)]
    paras = [p.strip(" ;,") for p in paras if p.strip(" ;,")]
    if not paras:
        return ""
    if len(heads) == len(paras):
        return "\n\n".join(f"{n}. {h}\n\n{p}" for n, (h, p) in enumerate(zip(heads, paras), 1))
    steps = ["## Steps\n\n" + "\n".join(f"{n}. {h}" for n, h in enumerate(heads, 1))] if heads else []
    return "\n\n".join(steps + ["## Details"] + paras)


def _wikihow_article(title, sections, raw=False):
    parts, seen = [], set()
    for headline, text in sections:
        part = _wikihow_section(headline, text, raw)
        if part and part not in seen:  # the dump repeats some sections word for word
            seen.add(part)
            parts.append(part)
    if not parts:
        return
    text = parts[0] if len(parts) == 1 else "\n\n".join(f"## Part {i}\n\n{p}" for i, p in enumerate(parts, 1))
    if len(text) >= MIN_CHARS:
        yield title, "https://www.wikihow.com/" + title.replace("How to ", "").replace(" ", "-"), text


# ---------- writing shards and the index ----------

class DocIds:
    """Document ids for one build. A document a rebuilt source still holds keeps the id it had in the previous build,
    so the Library's lists, "Something to read" and an article open in the app still name it after the swap; every
    other document gets an id above all of the previous build's, so no id ever names a different document. The same
    document is the same source with the same address and title, or, when the title changed (a PDF's title comes
    from its text), the same address held by only that one document before. Saved chats also keep each source's key,
    address and title (app/server.py resolve_ref()), so they find a document even when its id changed."""

    def __init__(self, db):
        self.next = (db.execute("SELECT max(id) FROM docs").fetchone()[0] or 0) + 1
        self.before, self.used = {}, set()

    def remember(self, db, key):
        """Note the ids of source key's documents, before the build drops the source to convert it again."""
        by_both, by_url = collections.defaultdict(list), collections.defaultdict(list)
        for doc_id, title, url in db.execute("SELECT d.id, d.title, d.url FROM docs d JOIN sources s "
                                             "ON s.id = d.source_id WHERE s.key=? ORDER BY d.id", (key,)):
            by_both[(title, url or "")].append(doc_id)
            by_url[url or ""].append(doc_id)
        self.before[key] = (by_both, by_url)

    def pick(self, key, title, url):
        by_both, by_url = self.before.get(key, ({}, {}))
        for doc_id in by_both.get((title, url or ""), ()):
            if doc_id not in self.used:
                self.used.add(doc_id)
                return doc_id
        same = by_url.get(url) if url else None
        if same and len(same) == 1 and same[0] not in self.used:
            self.used.add(same[0])
            return same[0]
        self.next += 1
        return self.next - 1


class SourceWriter:
    def __init__(self, db, source_id, key, label, ids=None):
        self.db, self.source_id, self.label, self.ids = db, source_id, label, ids
        self.dir = os.path.join(TEXT_DIR, key)
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.key, self.shard, self.f, self.pos, self.file_id, self.docs = key, -1, None, 0, None, 0

    def _next_shard(self):
        if self.f:
            self.f.close()
        self.shard += 1
        rel = f"{self.key}/{self.shard:04d}.md"
        self.f = open(os.path.join(TEXT_DIR, rel), "wb")
        self.pos = 0
        self.file_id = self.db.execute("INSERT INTO files(path) VALUES(?)", (rel,)).lastrowid

    def _write(self, s):
        b = s.encode("utf-8")
        self.f.write(b)
        self.pos += len(b)
        return len(b)

    def add(self, title, url, text):
        if self.f is None or self.pos > SHARD_BYTES:
            self._next_shard()
        start = self.pos
        self._write(f"# {title}\nSource: {self.label}" + (f" | {url}" if url else "") + "\n\n")
        passages = []
        for heading, chunk in split_passages(text):
            offset = self.pos
            passages.append((offset, self._write(chunk), heading, chunk))
            self._write("\n\n")
        self._write("---\n\n")
        doc_id = self.db.execute(
            "INSERT INTO docs(id, source_id, title, url, file_id, offset, length, sort_key) VALUES(?,?,?,?,?,?,?,?)",
            (self.ids.pick(self.key, title, url) if self.ids else None, self.source_id, title, url, self.file_id,
             start, self.pos - start, cleanup.sort_key(self.key, title))).lastrowid
        for offset, length, heading, chunk in passages:
            pid = self.db.execute("INSERT INTO passages(doc_id, offset, length) VALUES(?,?,?)",
                                  (doc_id, offset, length)).lastrowid
            self.db.execute("INSERT INTO fts(rowid, title, body) VALUES(?,?,?)",
                            (pid, f"{title} {heading}".strip(), chunk))
        self.docs += 1

    def close(self):
        if self.f:
            self.f.close()


def open_db():
    db = sqlite3.connect(DB_PATH, isolation_level=None)
    db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=OFF;
        CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT, topic TEXT,
                                           origin TEXT, docs INT DEFAULT 0, done INT DEFAULT 0, license TEXT);
        CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, source_id INT, title TEXT, url TEXT,
                                        file_id INT, offset INT, length INT, sort_key TEXT);
        CREATE INDEX IF NOT EXISTS docs_title ON docs(title COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS docs_source_title ON docs(source_id, title COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, doc_id INT, offset INT, length INT);
        CREATE INDEX IF NOT EXISTS passages_doc ON passages(doc_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, body, content='', contentless_delete=1,
                                                          tokenize='porter unicode61 remove_diacritics 2');
    """)
    if "license" not in [c[1] for c in db.execute("PRAGMA table_info(sources)")]:
        db.execute("ALTER TABLE sources ADD COLUMN license TEXT")  # libraries built before licenses were kept
    ensure_sort_keys(db)
    categories.ensure_schema(db)
    return db


def ensure_sort_keys(db):
    """Give a library built before the Library page's sort keys (cleanup.sort_key()) the docs.sort_key column, a
    key for every document and the index docs_source_sort. Changes no title, text or id. Does nothing when the
    index is there already (every document written since has its key: SourceWriter.add, reclean's rewrite)."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='docs_source_sort'").fetchone():
        return
    if "sort_key" not in [c[1] for c in db.execute("PRAGMA table_info(docs)")]:
        db.execute("ALTER TABLE docs ADD COLUMN sort_key TEXT")
    keys = dict(db.execute("SELECT id, key FROM sources"))
    db.create_function("title_sort_key", 2, lambda sid, title: cleanup.sort_key(keys.get(sid), title),
                       deterministic=True)
    start = time.time()
    db.execute("BEGIN")
    try:
        db.execute("UPDATE docs SET sort_key = title_sort_key(source_id, title) WHERE sort_key IS NULL")
        db.execute("CREATE INDEX docs_source_sort ON docs(source_id, sort_key)")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    n = db.execute("SELECT count(*) FROM docs").fetchone()[0]
    if n:
        print(f"sort keys for {n:,} titles, {time.time() - start:.0f}s", flush=True)


# Indexes and tables every build must have (check_build refuses a build without them). docs_source_sort and
# passages_doc serve the Library page (library/browse.py): titles of one source in order (by cleanup.sort_key()),
# and a title's first passage; docs_source_title serves the look-up of a saved source by its title. reading and
# reading_done hold the "Something to read" categories (library/categories.py).
REQUIRED_SCHEMA = ("docs_title", "docs_source_title", "docs_source_sort", "passages_doc", "reading", "reading_doc",
                   "reading_done")


WIKI_ORDER = ("wikimed", "wikipedia")  # WikiMed first, so medical articles keep WikiMed as their source


LEFT_OUT = {}  # {key: {why: documents}} left out by the last build_source() of each source (cleanup.py's rules)


def build_source(db, key, label, topic, origin, docs_iter, ids=None, raw=False):
    """Convert one source into the index. raw=True (the free library): every document as it was converted, with
    none of library/cleanup.py's rules (no documents left out, no furniture removed, titles unchanged); only empty
    documents and exact copies are skipped."""
    row = db.execute("SELECT done FROM sources WHERE key=?", (key,)).fetchone()
    if row and row[0]:
        return False
    start = time.time()
    print(f"building {key} from {os.path.basename(origin)}", flush=True)
    seen_titles = set()
    if key in WIKI_FAMILY:  # skip articles a family member earlier in WIKI_ORDER already holds, never later ones
        earlier = [k for k in WIKI_ORDER[:WIKI_ORDER.index(key)]]
        if earlier:
            seen_titles = {t.lower() for (t,) in db.execute(
                "SELECT d.title FROM docs d JOIN sources s ON s.id = d.source_id WHERE s.key IN (%s)"
                % ",".join("?" * len(earlier)), earlier)}
    db.execute("BEGIN")
    try:
        db.execute("DELETE FROM sources WHERE key=?", (key,))  # leftover from an interrupted run
        source_id = db.execute("INSERT INTO sources(key, label, topic, origin) VALUES(?,?,?,?)",
                               (key, label, topic, os.path.basename(origin))).lastrowid
        writer = SourceWriter(db, source_id, key, label, ids)
        copies = set()  # sites serve some pages at several addresses (CDC's Yellow Book under every country)
        left_out, furniture, cleaned = collections.Counter(), collections.Counter(), collections.Counter()
        titles = collections.Counter()
        deferred, seen_final = [], set()

        def store(title, text, url):
            digest = hashlib.blake2b(f"{title}\n{text}".encode(), digest_size=16).digest()
            if digest in copies:
                return
            copies.add(digest)
            titles[title.lower()] += 1
            if not raw and key in cleanup.PDF_GROUP and titles[title.lower()] > 1:  # two PDFs, one title: name the file
                title = f"{title} ({cleanup.file_title(url)})"
            writer.add(title, url, text)
            if writer.docs % 20000 == 0:
                print(f"    {writer.docs:,} articles, {time.time() - start:.0f}s", flush=True)

        for title, url, text in docs_iter:
            if seen_titles and title.lower() in seen_titles:
                continue
            if raw:
                if text.strip():
                    store(title, text, url)
                continue
            text, why, removed = cleanup.clean_document(key, title, url, text)  # English only, site furniture
            if why is None and not text.strip():
                why = "empty once cleaned"  # furniture alone never drops a document that has anything else
            if why:
                left_out[why] += 1
                continue
            for name, n in removed.items():
                furniture[name] += n
                cleaned[name] += 1
            if key == "medlineplus" and cleanup.medline_synonyms(title, url):
                deferred.append((title, url, text))  # its title depends on the titles of the whole source
                continue
            if key == "medlineplus":
                seen_final.add(cleanup.medline_title_without_text(title, url).lower())
            store(*cleanup.clean_title(key, title, url, text), url)
        for title, url, text in deferred:  # MedlinePlus topics: "Name (overview)" when an article has the name
            new_title, text = cleanup.clean_title(key, title, url, text, others=seen_final)
            store(new_title, text, url)
        writer.close()
        db.execute("UPDATE sources SET docs=?, done=1 WHERE id=?", (writer.docs, source_id))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    LEFT_OUT[key] = dict(left_out)
    if left_out:
        print(f"    left out {sum(left_out.values()):,} documents not in English or empty once cleaned: "
              + ", ".join(f"{why} {n:,}" for why, n in sorted(left_out.items())), flush=True)
    for name, n in sorted(furniture.items()):
        print(f"    removed site furniture ({name}): {n:,} paragraphs from {cleaned[name]:,} documents", flush=True)
    print(f"    done: {writer.docs:,} articles in {time.time() - start:.0f}s", flush=True)
    return True


def drop_duplicates_of(db, key, other):
    """Remove from source key the documents whose title (ignoring case, as build_source compares) source other also
    has. Their passages leave the index; their text stays in the shards unreferenced. Returns how many went."""
    row = db.execute("SELECT id FROM sources WHERE key=?", (key,)).fetchone()
    if not row:
        return 0
    db.create_function("py_lower", 1, lambda t: t.lower() if isinstance(t, str) else t, deterministic=True)
    db.execute("BEGIN")
    try:
        db.execute("CREATE TEMP TABLE other_titles(t TEXT PRIMARY KEY) WITHOUT ROWID")
        db.execute("INSERT OR IGNORE INTO other_titles SELECT py_lower(d.title) FROM docs d JOIN sources s "
                   "ON s.id = d.source_id WHERE s.key=?", (other,))
        db.execute("CREATE TEMP TABLE gone(id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO gone SELECT d.id FROM docs d JOIN other_titles o ON o.t = py_lower(d.title) "
                   "WHERE d.source_id=?", row)
        n = _remove_gone(db, row[0])
        db.execute("DROP TABLE other_titles")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    print(f"removed {n:,} {key} articles that {other} now holds", flush=True)
    return n


def _remove_gone(db, source_id):
    """Inside a transaction: remove the documents listed in the temp table gone(id), all of source source_id, from
    every table that points at a document (the full-text index, passages, "Something to read", docs) and lower the
    source's count. Their text stays in the shards, unreferenced. Drops the temp table; returns how many went."""
    n = db.execute("SELECT count(*) FROM gone").fetchone()[0]
    db.execute("DELETE FROM fts WHERE rowid IN (SELECT p.id FROM passages p JOIN gone g ON g.id = p.doc_id)")
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='reading'").fetchone():
        db.execute("DELETE FROM reading WHERE doc_id IN (SELECT id FROM gone)")
    db.execute("DELETE FROM passages WHERE doc_id IN (SELECT id FROM gone)")
    db.execute("DELETE FROM docs WHERE id IN (SELECT id FROM gone)")
    db.execute("UPDATE sources SET docs = docs - ? WHERE id=?", (n, source_id))
    db.execute("DROP TABLE gone")
    return n


def stored_text(raw):
    """A document's text as SourceWriter stored it, without its "# title / Source:" header and closing rule."""
    body = raw.partition("\n\n")[2].rstrip()
    return body[:-3].rstrip() if body.endswith("---") else body


def dedupe_pdf_group(db, text_dir=None):
    """Keep one copy of each PDF that several zimgit collections bundle (cleanup.PDF_GROUP), in the first collection
    that has it, by the content of its stored text (cleanup.content_key). Works on converted and carried-over
    sources alike, so the result does not depend on which of them a build converted. Returns {key: removed}."""
    text_dir = text_dir or TEXT_DIR
    ids = dict(db.execute("SELECT key, id FROM sources WHERE key IN (%s)" % ",".join("?" * len(cleanup.PDF_GROUP)),
                          cleanup.PDF_GROUP))
    seen, removed = set(), {}
    for key in cleanup.PDF_GROUP:
        if key not in ids:
            continue
        gone = []
        for doc_id, path, offset, length in db.execute(
                "SELECT d.id, f.path, d.offset, d.length FROM docs d JOIN files f ON f.id = d.file_id "
                "WHERE d.source_id=? ORDER BY d.id", (ids[key],)).fetchall():
            with open(os.path.join(text_dir, path), "rb") as f:
                f.seek(offset)
                text = stored_text(f.read(length).decode("utf-8", "replace"))
            digest = hashlib.blake2b(cleanup.content_key(text).encode(), digest_size=16).digest()
            if digest in seen:
                gone.append(doc_id)
            seen.add(digest)
        if not gone:
            continue
        db.execute("BEGIN")
        try:
            db.execute("CREATE TEMP TABLE gone(id INTEGER PRIMARY KEY)")
            db.executemany("INSERT INTO gone VALUES(?)", [(i,) for i in gone])
            removed[key] = _remove_gone(db, ids[key])
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        print(f"removed {removed[key]:,} {key} PDFs that an earlier collection already holds", flush=True)
    return removed


def drop_source(db, key):
    """Remove one source from the index and disk so the next build redoes it."""
    row = db.execute("SELECT id FROM sources WHERE key=?", (key,)).fetchone()
    if row:
        db.execute("BEGIN")
        categories.forget_source(db, row[0])  # before its docs go, so a reused doc id never inherits a category
        db.execute("DELETE FROM fts WHERE rowid IN (SELECT p.id FROM passages p JOIN docs d ON d.id = p.doc_id "
                   "WHERE d.source_id=?)", row)
        db.execute("DELETE FROM passages WHERE doc_id IN (SELECT id FROM docs WHERE source_id=?)", row)
        db.execute("DELETE FROM files WHERE id IN (SELECT DISTINCT file_id FROM docs WHERE source_id=?)", row)
        db.execute("DELETE FROM docs WHERE source_id=?", row)
        db.execute("DELETE FROM sources WHERE id=?", row)
        db.execute("COMMIT")
    shutil.rmtree(os.path.join(TEXT_DIR, key), ignore_errors=True)
    print(f"dropped {key}", flush=True)


# ---------- what each source is built from ----------

ZIM_KEYS = {v[0]: prefix for prefix, v in ZIM_SOURCES.items()}
CATALOG_FILE_PREFIX = {"gardenology_en_all": "gardenology.org_en_all"}  # catalog name -> its files' prefix
KNOWN_KEYS = frozenset(list(ZIM_KEYS) + list(PDF_SOURCES) + ["wikihow"])


def folder_fingerprint(folder):
    """A hash over a manifest folder: the manifest itself and every file it lists."""
    digest = hashlib.sha256()
    path = os.path.join(folder, "manifest.json")
    with open(path, "rb") as f:
        data = f.read()
    digest.update(data)
    for item in json.loads(data):
        name = os.path.basename(str(item.get("file", "")))
        target = os.path.join(folder, name)
        digest.update(name.encode() + b"\0")
        digest.update((safe_fetch.sha256_file(target) if os.path.exists(target) else "missing").encode() + b"\n")
    return digest.hexdigest()


# Converter versions. Raising one changes the fingerprint of the sources it converts, so the next refresh converts
# them again even though their input did not change. Version 1 is implied and leaves fingerprints as they were.
CONVERTER = {"key:wikihow": 5,      # 4: one document per article, sections merged; 5: glued sentences spaced
             "kind:wiki": 2,        # drop_sections no longer loses ~0.7% of articles to an lxml assertion
             "key:medlineplus": 5,  # 2: the PDF copies of its genetics pages are skipped (TWIN_PDFS); 3: furniture
             # 2 or 3: library/cleanup.py's English-only and furniture rules fire on these sources
             "key:appropedia": 4, "key:energypedia": 4, "key:gardenology": 4, "key:wikiciv": 3,
             "key:cdc-travel": 4, "key:ifixit": 3,
             # 2: PDF titles from metadata, heading or file name; one copy of a PDF the collections share
             # (the PDF sources moved on to the page-aware converter are at the end)
             # one higher than above (2 for the rest): the title review's stored titles and junk pages
             # (cleanup.clean_title(), page_rule(), junk_rule(), the added PDF_TITLE_OVERRIDES)
             "key:wikipedia": 3, "key:wikimed": 3, "key:nhs-medicines": 2,
             # one higher again (cleanup.tidy_text(), the 16 November 2026 reading-view review): converted PDFs lose
             # their page furniture and get their lists and headings back (medlineplus, ready-gov, cdc-travel and the
             # PDF collections above, and these), Q&A pages keep their question under a "Question"
             # label, with no user names or scores (the se- sources)
             # one higher again (library/pdftext.py, 16 November 2026): the PDF sources converted page by page, page
             # furniture out at the page edges, bullets with their items, columns in reading order. MedlinePlus and
             # CDC Travelers' Health keep their few PDFs as they are (9 PDFs, 2.1 GB of ZIMs to download again).
             # one higher again (cleanup.remove_page_furniture(), 16 November 2026): the running lines the page-aware
             # converter leaves without their page number go by the line's own page label, a machine stamp or the
             # document's name. A re-clean of the stored text (RECLEAN), no download.
             # one higher again (the same day): such a labelled line or stamp run into body text goes too, a title
             # loses a printout's page label, and PDF_TITLE_OVERRIDES names the PDFs whose cover line is a poor title
             # (the 17 the first re-clean renamed, and three more). Re-clean.
             "key:ready-gov": 7, "key:food-prep": 7, "key:knots": 7, "key:medical-library": 7, "key:water": 7,
             "key:post-disaster": 7, "key:military-medicine": 5, "key:usda-canning": 5,
             "key:survival-manuals": 5, "key:first-aid-guides": 5, "key:risk": 5,
             **{f"key:{k}": 3 for k in ("se-diy", "se-cooking", "se-mechanics", "se-gardening", "se-pets",
                                        "se-outdoors", "se-medicalsciences", "se-ham", "se-lifehacks",
                                        "se-sustainability")}}
# The 90% comparison stays on for a converter change, except for a source that declares here that the new version
# is meant to produce fewer documents: {key: versions whose build may drop below 90% of the one before}.
EXPECTED_DROP = {"wikihow": {2, 3, 4},  # sections merged into articles: 205,785 documents become about 126,000
                 "medlineplus": {2},    # 17,346 documents become about 14,500 without the genetics PDFs
                 "appropedia": {3},     # English only: 21,658 documents become about 12,750 (8,099 translations)
                 "energypedia": {3},    # English only: 10,282 documents become about 8,380 (foreign records, file pages)
                 "ready-gov": {2}}      # English only: 649 documents become about 443 (Spanish and other PDFs)
# Documents a build removes on purpose from what it carried over (one copy of a PDF the zimgit collections share,
# Wikipedia articles WikiMed now holds, the Wikipedia trim, the re-clean pass) are recorded per source in the build's
# manifest as "removed_on_purpose" and added back before the 90% comparison, so it still catches a loss nobody meant.

# Converter versions the re-clean pass (library/reclean.py) reaches from a build's stored text, without the original:
# {key: (version the live build was converted with, version the re-clean pass brings it to)}. A source the live build
# holds at the first version counts as built (is_built()) and unchanged (changed_sources()): no download, no
# conversion; `build_library.py --reclean` brings it to the second.
# The PDF sources on library/pdftext.py, re-cleaned to their running-line fix (6 or 4), reach its second part (7 or
# 5) here; a change to pdftext.py itself needs the PDFs converted again, which no re-clean of stored text can do.
PDF_RUNNING_LINES = {"ready-gov": 6, "food-prep": 6, "knots": 6, "medical-library": 6, "water": 6, "post-disaster": 6,
                     "military-medicine": 4, "usda-canning": 4, "survival-manuals": 4, "first-aid-guides": 4, "risk": 4}
RECLEAN = {**{k: (v, v + 1) for k, v in PDF_RUNNING_LINES.items()},**{k: (3, 4) for k in ("appropedia", "energypedia", "gardenology")}, "medlineplus": (4, 5),
           "cdc-travel": (3, 4),
           "ifixit": (2, 3), "wikiciv": (2, 3), "wikihow": (4, 5), "wikipedia": (2, 3), "wikimed": (2, 3),
           "nhs-medicines": (1, 2),
           **{k: (2, 3) for k in ("se-diy", "se-cooking", "se-mechanics", "se-gardening", "se-pets", "se-outdoors",
                                  "se-medicalsciences", "se-ham", "se-lifehacks", "se-sustainability")}}
MIN_RATIO_EXPECTED_DROP = 0.5  # even a declared drop keeps at least half of the previous build's documents


def converter_version(key, kind=None):
    return max(CONVERTER.get(f"key:{key}", 1), CONVERTER.get(f"kind:{kind}", 1) if kind else 1)


def _versioned(fingerprint, version):
    return fingerprint if version == 1 else f"{fingerprint}+converter{version}"


def source_inputs(zim_dir=None, raw_dir=None):
    """Every source whose input is on disk now: {key: {label, topic, origin, fingerprint, docs}}, where docs is a
    function that takes skip_wikipedia and returns the (title, url, text) iterator. Only verified ZIMs count."""
    from library.fetch_zims import latest_verified, verified_record
    zim_dir = zim_dir or safe_fetch.ZIM_DIR
    raw_dir = raw_dir or safe_fetch.RAW_DIR
    inputs = {}
    off = cleanup.sources_off()  # switched off in library/source-switch.json: as if its input were not here
    from library import fetch_wikihow
    wikihow = fetch_wikihow.ready(safe_fetch.load_lock(), raw_dir)  # the pinned copy, or one a person accepted
    if wikihow and "wikihow" not in off:
        inputs["wikihow"] = dict(label="wikiHow", topic="howto", origin=wikihow[0], converter=converter_version("wikihow"),
                                 fingerprint=_versioned(wikihow[1], converter_version("wikihow")),
                                 docs=lambda skip_wikipedia, p=wikihow[0]: wikihow_docs(p))
    for key, (label, topic, sub) in PDF_SOURCES.items():
        folder = os.path.join(raw_dir, sub)
        if key not in off and os.path.exists(os.path.join(folder, "manifest.json")):
            # The converter version is part of the fingerprint, as for the ZIMs, so a cleanup rule change reaches
            # these through the re-clean pass (RECLEAN) without converting the PDFs again.
            inputs[key] = dict(label=label, topic=topic, origin=folder, converter=converter_version(key),
                               fingerprint=_versioned(folder_fingerprint(folder), converter_version(key)),
                               docs=lambda skip_wikipedia, f=folder: manual_docs(f, skip_wikipedia))
    for prefix, (key, label, topic, kind, url_prefix) in ZIM_SOURCES.items():
        path = latest_verified(prefix, zim_dir) if key not in off else None
        if not path:
            continue

        def docs(skip_wikipedia, path=path, key=key, kind=kind, url_prefix=url_prefix):
            return ((t, FIXED_URLS.get(key) or (url_prefix + p if url_prefix else p), x)
                    for t, p, x in zim_docs(path, kind, SKIP_PATHS.get(key), TWIN_PDFS.get(key)))
        inputs[key] = dict(label=label, topic=topic, origin=path, converter=converter_version(key, kind),
                           fingerprint=_versioned(verified_record(path)["sha256"], converter_version(key, kind)),
                           docs=docs, articles=(articles := zim_articles(path)), kind=kind,
                           min_docs=zim_min_docs(articles, kind))
    return inputs


SHARE_RULE_KINDS = {"wiki", "se"}  # ZIMs whose article_count really counts articles (mwoffliner, sotoki)


def zim_articles(path):
    from libzim.reader import Archive
    try:
        return Archive(path).article_count
    except Exception:  # an unreadable ZIM: the build itself will fail on it
        return 0


def zim_min_docs(articles, kind):
    """The fewest documents a sane build gets from a ZIM. For wiki and Stack Exchange ZIMs, whose article_count
    counts articles (mwoffliner counts redirects too: Wikipedia's 5.9 million for about 930,000 articles),
    MIN_ZIM_SHARE of it. For PDF and web ZIMs (nautilus, warc2zim) article_count may be just the front page or
    count scans without a text layer, so only at least one document; check_build logs their ratio instead."""
    if kind in SHARE_RULE_KINDS:
        return max(1, math.ceil(articles * MIN_ZIM_SHARE))
    return 1


def build_order(keys):
    """wikiHow, then the manifest folders, then the ZIMs, with WikiMed before Wikipedia."""
    rank = {"wikihow": 0, **{k: 1 for k in PDF_SOURCES}}
    return sorted(keys, key=lambda k: (rank.get(k, 2), k == "wikipedia", k != "wikimed", k))


# ---------- builds on disk ----------

def current_build(current=None):
    """The folder the "current" symlink points at, or None."""
    current = current or safe_fetch.CURRENT
    if not oscompat.is_link(current):  # a symlink, or the junction the Windows app makes
        return None
    target = os.path.realpath(current)
    return target if os.path.isfile(os.path.join(target, "index.sqlite")) else None


def _lib_sort_key(name):
    m = re.match(r"^lib-(\d{4}-\d{2}-\d{2})(?:-(\d+))?$", name)
    return (m.group(1), int(m.group(2) or 1))


def _complete(path):
    """A build that passed its checks: the index, its manifest, and the CHECKS_PASSED record written after
    check_build() succeeded. A build without that record (one that failed, or one from before the record existed,
    like the empty build of 12 November 2026) is never used as the previous build."""
    return all(os.path.isfile(os.path.join(path, name)) for name in ("index.sqlite", BUILD_MANIFEST, CHECKS_PASSED))


def live_build(current=None, libs_dir=None):
    """The build "current" points at, when that is a lib-* folder in libs_dir that passed its checks; else None."""
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    target = current_build(current)
    if target and os.path.dirname(target) == os.path.realpath(libs_dir) \
            and LIB_NAME_RE.match(os.path.basename(target)) and _complete(target):
        return target
    return None


def previous_build(current=None, libs_dir=None):
    """The build a new one is compared with and carried over from: the one "current" points at when that is a
    complete lib-* folder in libs_dir that passed its checks; else (current missing, dangling, pointing elsewhere,
    or at a build that never passed) the one of those folders that passed its checks last; else None, and then nothing may be pruned and
    every source is converted again."""
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    target = live_build(current, libs_dir)
    if target:
        return target
    try:
        names = [n for n in os.listdir(libs_dir) if LIB_NAME_RE.match(n) and not os.path.islink(os.path.join(libs_dir, n))
                 and _complete(os.path.join(libs_dir, n))]
    except FileNotFoundError:
        return None
    return os.path.realpath(max((os.path.join(libs_dir, n) for n in names), key=_went_live)) if names else None


def read_manifest(lib_dir):
    """{key: {origin, fingerprint, docs}} recorded by the build in lib_dir, or {} (none, or a build from before
    manifests were kept, which then gets rebuilt in full)."""
    if not lib_dir:
        return {}
    try:
        with open(os.path.join(lib_dir, BUILD_MANIFEST), encoding="utf-8") as f:
            data = json.load(f)
        sources = data.get("sources")
        return sources if isinstance(sources, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def changed_sources(inputs, previous, force=()):
    """The keys to convert again: forced ones, new ones, and those whose input fingerprint changed. A new WikiMed
    also rebuilds Wikipedia (when its ZIM is here), so the two keep one copy of each article between them."""
    changed = {k for k, inp in inputs.items()
               if k in force or k not in previous or (previous[k].get("fingerprint") != inp["fingerprint"]
                                                      and not recleanable(previous[k], k, inp["fingerprint"]))}
    if "wikimed" in changed and "wikipedia" in inputs and "wikipedia" in previous:
        changed.add("wikipedia")
    return changed


def _clone_file(src, dest):
    """An APFS copy-on-write clone (instant, no extra disk until one side changes), else a plain copy."""
    if sys.platform == "darwin" and subprocess.run(["/bin/cp", "-c", "--", src, dest],
                                                   capture_output=True).returncode == 0:
        return
    shutil.copyfile(src, dest)


def carry_over(prev_dir, new_dir, keys):
    """Start the new build from the previous one: a clone of its index, and hard links to the text shards of the
    sources in keys. Shards are never written in place (SourceWriter deletes a source's folder and writes new
    files), so a hard-linked shard can never change under the previous build."""
    _clone_file(os.path.join(prev_dir, "index.sqlite"), os.path.join(new_dir, "index.sqlite"))
    for key in keys:
        src = os.path.join(prev_dir, "text", key)
        if not os.path.isdir(src):
            continue
        dest = os.path.join(new_dir, "text", key)
        os.makedirs(dest)
        for name in os.listdir(src):
            s, d = os.path.join(src, name), os.path.join(dest, name)
            if os.path.islink(s) or not os.path.isfile(s):
                continue
            try:
                os.link(s, d)
            except OSError:
                shutil.copy2(s, d)


def unique_lib_dir(libs_dir, date=None):
    date = date or datetime.date.today().isoformat()
    path = os.path.join(libs_dir, f"lib-{date}")
    n = 2
    while os.path.lexists(path):
        path = os.path.join(libs_dir, f"lib-{date}-{n}")
        n += 1
    return path


def needs_upgrade(lib_dir):
    """The parts of REQUIRED_SCHEMA a build lacks, and the READING_SOURCES it holds that are not classified with
    today's categories.VERSION: [] when it is up to date (or is not a readable build)."""
    try:
        db = sqlite3.connect(f"file:{os.path.join(lib_dir, 'index.sqlite')}?mode=ro", uri=True)
    except (sqlite3.Error, TypeError):
        return []
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        missing = [n for n in REQUIRED_SCHEMA if n not in names]
        keys = {r[0] for r in db.execute("SELECT key FROM sources WHERE done=1")}
        done = dict(db.execute("SELECT key, version FROM reading_done")) if "reading_done" in names else {}
    except sqlite3.Error:
        return []
    finally:
        db.close()
    return missing + [f"categories for {k}" for k in categories.READING_SOURCES
                      if k in keys and done.get(k) != categories.VERSION]


def _remove_tree(path, name):
    """Delete a build folder; True when it is gone. On Windows a file something still has open can't be deleted, so
    the folder is kept for the next tidy-up (False) instead of stopping the build."""
    if not oscompat.WINDOWS:
        shutil.rmtree(path)
        return True
    try:
        shutil.rmtree(path)
    except OSError as exc:
        print(f"keeping {name} for now: {exc}", flush=True)
        return False
    return True


def remove_leftovers(libs_dir, current=None):
    """Before a new build starts: delete the .staging-* folders a killed run left in libs_dir (up to a whole build
    each) and every kept .failed-* build, so neither adds to this run's peak disk use. Only while this process holds
    the refresh lock (safe_fetch.holds_lock()): then no other run can be writing one. Never the folder "current"
    points at, and never one a running process has open (readers()). No run ever resumes from an old staging
    folder: build() always starts a new one with mkdtemp. A failed build kept for --recheck goes too; if this build
    fails again it is kept instead. Without the lock nothing is deleted here (tidy_builds() still removes them after
    the next good run). Returns the names removed."""
    if not safe_fetch.holds_lock():
        return []
    live = os.path.realpath(current or safe_fetch.CURRENT)
    removed = []
    for name in sorted(os.listdir(libs_dir)):
        path = os.path.join(libs_dir, name)
        if not name.startswith((".staging-", FAILED_PREFIX)) or not os.path.isdir(path) or os.path.islink(path) \
                or os.path.realpath(path) == live:
            continue
        in_use = readers(path)
        if in_use:
            print(f"keeping {name}: still open in process {', '.join(map(str, in_use))}", flush=True)
            continue
        if not _remove_tree(path, name):
            continue
        removed.append(name)
        print(f"removed {name} before building", flush=True)
    return removed


def build(force=(), prev=None, inputs=None, libs_dir=None, date=None, upgrade=False, current=None, only=None,
          raw=False):
    """Build a new library folder from the live one plus whatever changed. Returns (folder, changed keys), or
    (None, set()) when nothing changed. The folder is not checked or swapped in here.
    upgrade=True builds a new folder even when no source changed, when the previous build lacks an index, a table
    or a category classification this code adds (needs_upgrade()): every source is carried over from it unchanged,
    nothing is downloaded or converted, and the new build gets what was missing.
    only: a set of source keys (refresh.py --only); no other source is converted, removed or switched off.
    raw=True: the free library (library/free_library.py), every source in inputs converted afresh with none of the
    cleaning rules (build_source raw), nothing carried over, no source switched off or deduplicated; its manifest
    says "raw" and its fingerprints end in "+raw", so the next cleaned build converts every source again."""
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    if raw:
        prev, force, upgrade, only = None, set(inputs), False, None
    off = set() if raw else cleanup.sources_off()
    inputs = source_inputs() if inputs is None else inputs
    inputs = {k: v for k, v in inputs.items() if k not in off}
    previous = read_manifest(prev)
    changed = changed_sources(inputs, previous, force)
    switched_off = set(previous) & off if prev else set()  # in the live build, switched off since: removed
    if only:
        changed &= set(only)
        switched_off &= set(only)
    if not changed and not switched_off and not (upgrade and prev and previous and needs_upgrade(prev)):
        return None, set()
    os.makedirs(libs_dir, exist_ok=True)
    remove_leftovers(libs_dir, current)
    staging = tempfile.mkdtemp(prefix=".staging-", dir=libs_dir)
    db = None
    try:
        set_output(staging)
        os.makedirs(TEXT_DIR)
        old_keys = set(previous) if prev and previous else set()
        if old_keys:
            carry_over(prev, staging, old_keys - changed - switched_off)
        db = open_db()
        ids = DocIds(db)
        dropped = False
        for key in sorted((changed | switched_off) & old_keys):
            if key in changed:
                ids.remember(db, key)
            drop_source(db, key)
            dropped = True
        skip_wikipedia = "wikipedia" in inputs or "wikipedia" in old_keys - switched_off
        manifest = {k: {f: x for f, x in v.items() if f != "removed_on_purpose"} for k, v in previous.items()
                    if k not in changed and k not in switched_off} if old_keys else {}
        for key in build_order(changed):
            inp = inputs[key]
            build_source(db, key, inp["label"], inp["topic"], inp["origin"], inp["docs"](skip_wikipedia), ids, raw=raw)
            docs = db.execute("SELECT docs FROM sources WHERE key=?", (key,)).fetchone()
            manifest[key] = {"origin": os.path.basename(inp["origin"]), "fingerprint": inp["fingerprint"],
                             "docs": docs[0] if docs else 0, "converter": inp.get("converter", 1)}
            if raw:
                manifest[key].update(raw=True, fingerprint=f"{inp['fingerprint']}+raw")
            elif key == "wikipedia":
                manifest[key]["trim"] = sorted(cleanup.trim_tiers())
                if set(previous.get(key, {}).get("trim") or ()) != set(manifest[key]["trim"]):
                    # Tiers the previous build had not applied: the articles they leave out were in it, on purpose.
                    manifest[key]["removed_on_purpose"] = sum(
                        n for why, n in LEFT_OUT.get(key, {}).items() if why.startswith("Wikipedia trim"))
        if "wikimed" in changed and "wikipedia" in manifest and "wikipedia" not in changed:
            # Wikipedia is carried over (its ZIM was deleted after conversion), so it was deduplicated against the
            # old WikiMed. Remove the articles the new WikiMed now holds, so no article is in the library twice.
            removed = drop_duplicates_of(db, "wikipedia", "wikimed")
            if removed:
                dropped = True
                docs = db.execute("SELECT docs FROM sources WHERE key='wikipedia'").fetchone()
                manifest["wikipedia"] = dict(manifest["wikipedia"], docs=docs[0] if docs else 0,
                                             removed_on_purpose=removed)
        for key, n in ({} if raw else dedupe_pdf_group(db)).items():  # one copy of each PDF the zimgit collections share
            dropped = True
            docs = db.execute("SELECT docs FROM sources WHERE key=?", (key,)).fetchone()
            manifest[key] = dict(manifest[key], docs=docs[0] if docs else 0,
                                 removed_on_purpose=manifest[key].get("removed_on_purpose", 0) + n)
        categories.build_reading(db, TEXT_DIR)
        for key, text in LICENSES.items():
            db.execute("UPDATE sources SET license=? WHERE key=?", (text, key))
        if changed:  # an upgrade changed no passage: optimizing would rewrite the whole full-text index for nothing
            print("optimizing index", flush=True)
            db.execute("INSERT INTO fts(fts) VALUES('optimize')")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if dropped:  # the dropped sources' pages stay in the file until this
            print("compacting index", flush=True)
            db.execute("VACUUM")
        db.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, opened read-only by the app
        db.close()
        with open(os.path.join(staging, BUILD_MANIFEST), "w", encoding="utf-8") as f:
            json.dump({"built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "changed": sorted(changed), "switched_off": sorted(switched_off), "sources": manifest,
                       **({"only": sorted(only)} if only else {}), **({"raw": True} if raw else {})}, f,
                      indent=1, sort_keys=True)
        os.chmod(staging, 0o755)  # mkdtemp makes it private; the app reads it as the same user anyway
        final = unique_lib_dir(libs_dir, date)
        os.rename(staging, final)
        return final, changed
    except BaseException:
        if db is not None:  # closed first: Windows can't delete the index while it is open
            try:
                db.close()
            except sqlite3.Error:
                pass
        shutil.rmtree(staging, ignore_errors=True)
        raise


# ---------- originals deleted once the live build holds them ----------
# The ZIMs and the wikiHow CSV are about 33 GB and are needed only to convert a source. Once a build that passed its
# checks is live, retire_originals() deletes each one the live build was converted from, after recording it in
# data.noindex/originals.json. The live build's manifest still says which file (name and SHA-256) each source came
# from and with which converter, so the weekly run knows a source is current without its file (is_built()), carries
# it over unchanged, and downloads it again only when the catalog offers another file, its converter version
# changes, or --rebuild names it. The small hash-locked PDFs and risk tables stay on disk.

def source_kind(key):
    """The kind of a ZIM source ("wiki", "se", "web", "pdf"), or None for wikiHow and the manifest folders."""
    prefix = ZIM_KEYS.get(key)
    return ZIM_SOURCES[prefix][3] if prefix else None


def original_sha(fingerprint):
    """The SHA-256 of the file a ZIM or wikiHow source was built from, out of its fingerprint ("<sha256>+converterN")."""
    return str(fingerprint or "").split("+", 1)[0]


def is_built(manifest, key, origin, sha256):
    """True if the build with this manifest holds source key converted from exactly this file (name and SHA-256)
    with today's converter version, or with the version the re-clean pass brings to today's (RECLEAN), so the file
    itself is not needed on disk."""
    entry = manifest.get(key) if key else None
    version = converter_version(key, source_kind(key))
    return bool(entry) and entry.get("origin") == origin and (
        entry.get("fingerprint") == _versioned(sha256, version) or recleanable(entry, key, _versioned(sha256, version)))


def recleanable(entry, key, fingerprint):
    """True if a manifest entry of source key differs from fingerprint (the input's, at today's converter) only by
    a converter change the re-clean pass makes from stored text: same original, the version RECLEAN starts from."""
    if key not in RECLEAN or not isinstance(entry, dict):
        return False
    start, target = RECLEAN[key]
    return converter_version(key, source_kind(key)) == target and entry.get("converter", 1) == start \
        and entry.get("fingerprint") == _versioned(original_sha(fingerprint), start)


def load_originals(path=None):
    """{key: record} of the originals deleted after conversion (file, size, sha256, catalog_date, converter,
    deleted, and url for wikiHow), or {}."""
    try:
        with open(path or safe_fetch.ORIGINALS, encoding="utf-8") as f:
            sources = json.load(f).get("sources")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(sources, dict):
        return {}
    for key in [k for k, v in sources.items() if not isinstance(v, dict)]:
        print(f"WARNING: originals.json: the record for {key!r} is not readable; it is ignored", flush=True)
        del sources[key]
    return sources


def _save_originals(sources, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "sources": sources}, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _catalog_date(name):
    m = re.search(r"_(\d{4}-\d{2}[a-z]?)\.zim$", name)
    return m.group(1) if m else None


def retire_originals(current=None, libs_dir=None, zim_dir=None, raw_dir=None, ledger=None):
    """Delete the ZIMs and the wikiHow CSV the live build was converted from. Only for a live build that passed its
    checks (live_build(); a failed or .failed-* build never counts), and only a file whose SHA-256 is the one the
    build records for it. Each is recorded in the ledger before it goes. Returns [(file, bytes)] deleted."""
    zim_dir = zim_dir or safe_fetch.ZIM_DIR
    raw_dir = raw_dir or safe_fetch.RAW_DIR
    ledger = ledger or safe_fetch.ORIGINALS
    live = live_build(current, libs_dir)
    if not live:
        return []
    manifest = read_manifest(live)
    from library import fetch_wikihow
    from library.fetch_zims import file_prefix, remove_zim, verified_record
    # Records of originals the live build no longer holds (a newer file was built since) are dropped.
    known = {k: v for k, v in load_originals(ledger).items()
             if k in manifest and isinstance(v, dict) and v.get("file") == manifest[k].get("origin")}
    lock = None
    deleted = []
    for key, entry in sorted(manifest.items()):
        origin = str(entry.get("origin") or "")
        if key == "wikihow":
            if origin != fetch_wikihow.FILE:
                continue
            path = os.path.join(raw_dir, origin)
        elif key in ZIM_KEYS:
            # Only a Kiwix file name of this very source: never a path, and never another source's file.
            if not safe_fetch.ZIM_NAME_RE.match(origin) or file_prefix(origin) != ZIM_KEYS[key]:
                continue
            path = os.path.join(zim_dir, origin)
        else:
            continue  # a manifest folder: small, hash-locked, kept
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        sha = original_sha(entry.get("fingerprint"))
        rec = verified_record(path)
        if not (rec and rec["sha256"] == sha):
            if key != "wikihow":
                continue
            lock = lock or safe_fetch.load_lock()
            accepted = lock["files"].get(fetch_wikihow.REL) or {}
            if accepted.get("sha256") != sha or not safe_fetch.locked_ok(lock, fetch_wikihow.REL, raw_dir):
                continue
        size = os.path.getsize(path)
        record = {"file": origin, "size": size, "sha256": sha, "converter": entry.get("converter", 1),
                  "catalog_date": _catalog_date(origin), "deleted": _now()}
        if key == "wikihow":
            lock = lock or safe_fetch.load_lock()
            accepted = lock["files"].get(fetch_wikihow.REL) or {}
            record["url"] = accepted.get("url") if accepted.get("sha256") == sha else \
                fetch_wikihow.download_url(fetch_wikihow.PIN["commit"])
        known[key] = record
        _save_originals(known, ledger)  # recorded before the file goes
        remove_zim(path)  # the file and its .verified record
        print(f"deleted the original {origin} ({size / 1e9:.2f} GB): {key} is in the live build", flush=True)
        deleted.append((origin, size))
    if not deleted and known != load_originals(ledger) and os.path.exists(ledger):
        _save_originals(known, ledger)
    return deleted


# ---------- checking a build and swapping it in ----------

def _source_counts(db_path):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return dict(db.execute("SELECT key, docs FROM sources WHERE done=1"))
    finally:
        db.close()


def source_hits(db_path, query, keys):
    """How many passages (at most 3) from the sources in keys match all of the query's words. The search is
    restricted to those sources, so it does not depend on how they rank against a million Wikipedia articles."""
    from library.search import query_words
    words = query_words(query)
    if not words or not keys:
        return 0
    terms = " ".join('"' + w.replace('"', "") + '"' for w in words)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM (SELECT p.id FROM fts JOIN passages p ON p.id = fts.rowid "
            "JOIN docs d ON d.id = p.doc_id JOIN sources s ON s.id = d.source_id "
            f"WHERE fts MATCH ? AND s.key IN ({','.join('?' * len(keys))}) LIMIT 3)", [terms, *keys]).fetchone()[0]
    finally:
        db.close()


def schema_missing(db_path):
    """The names in REQUIRED_SCHEMA that the index at db_path lacks."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    finally:
        db.close()
    return [n for n in REQUIRED_SCHEMA if n not in names]


# Every way a row can point at something that is not there: (what is wrong, a query counting such rows). Search
# results, articles, the Library page's lists (they read docs through docs_title and docs_source_title) and
# "Something to read" all start from one of these rows, so a count above zero is a dead link somewhere in the app.
DEAD_REFERENCES = (
    ("documents of a source that is not in the build",
     "SELECT count(*) FROM docs d LEFT JOIN sources s ON s.id = d.source_id AND s.done = 1 WHERE s.id IS NULL"),
    ("documents whose text file is not in the index",
     "SELECT count(*) FROM docs d LEFT JOIN files f ON f.id = d.file_id WHERE f.id IS NULL"),
    ("documents with no passage", "SELECT count(*) FROM docs d WHERE NOT EXISTS "
                                  "(SELECT 1 FROM passages p WHERE p.doc_id = d.id)"),
    ("passages of a missing document",
     "SELECT count(*) FROM passages p LEFT JOIN docs d ON d.id = p.doc_id WHERE d.id IS NULL"),
    ("passages outside their document", "SELECT count(*) FROM passages p JOIN docs d ON d.id = p.doc_id "
                                        "WHERE p.offset < d.offset OR p.offset + p.length > d.offset + d.length"),
    # fts is contentless: fts_docsize holds one row per indexed passage, under the passage's id.
    ("full-text rows of a missing passage",
     "SELECT count(*) FROM fts_docsize x LEFT JOIN passages p ON p.id = x.id WHERE p.id IS NULL"),
    ("passages missing from the full-text index",
     "SELECT count(*) FROM passages p LEFT JOIN fts_docsize x ON x.id = p.id WHERE x.id IS NULL"),
    ("\"Something to read\" rows of a missing document",
     "SELECT count(*) FROM reading r LEFT JOIN docs d ON d.id = r.doc_id WHERE d.id IS NULL"),
    ("\"Something to read\" marks for a source not in the build",
     "SELECT count(*) FROM reading_done r LEFT JOIN sources s ON s.key = r.key WHERE s.id IS NULL"),
    ("text files no document uses",
     "SELECT count(*) FROM files WHERE id NOT IN (SELECT file_id FROM docs)"),
    ("sources whose document count is wrong",
     "SELECT count(*) FROM sources s WHERE s.done = 1 AND s.docs != (SELECT count(*) FROM docs d WHERE d.source_id = s.id)"),
)


def reference_problems(db_path, text_dir):
    """The dead references in a build: rows in docs, passages, the full-text index or "Something to read" that
    point at a document, passage, source or text that is missing (DEAD_REFERENCES), documents that run past the end
    of their text file, and sources switched off in library/source-switch.json that are still in it. [] if none."""
    problems = []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        for what, query in DEAD_REFERENCES:
            if not set(re.findall(r"(?:FROM|JOIN) (\w+)", query)) <= names:
                continue  # a build from before "Something to read" (check_build reports the missing tables)
            n = db.execute(query).fetchone()[0]
            if n:
                problems.append(f"dead references: {n:,} {what}")
        real_text = os.path.realpath(text_dir)
        for rel, end in db.execute("SELECT f.path, max(d.offset + d.length) FROM files f JOIN docs d "
                                   "ON d.file_id = f.id GROUP BY f.id"):
            path = os.path.realpath(os.path.join(text_dir, rel))
            if path.startswith(real_text + os.sep) and os.path.isfile(path) and os.path.getsize(path) < end:
                problems.append(f"dead references: documents run past the end of {rel}")
        off = cleanup.sources_off() & {r[0] for r in db.execute("SELECT key FROM sources")}
        try:  # a build limited to some sources (refresh.py --only) leaves the others as they were
            with open(os.path.join(os.path.dirname(db_path), BUILD_MANIFEST), encoding="utf-8") as f:
                limited = json.load(f).get("only")
        except (OSError, ValueError, AttributeError):
            limited = None
        off = sorted(off & set(limited) if limited else off)
        if off:
            problems.append("switched off but still in the build: " + ", ".join(off))
    finally:
        db.close()
    return problems


def id_problems(new_dir, prev_dir):
    """Document ids a rebuilt source broke (see DocIds): a document still there, with the same address and title,
    under another id; or an id that now names a different document from the one it named before."""
    try:
        with open(os.path.join(new_dir, BUILD_MANIFEST), encoding="utf-8") as f:
            changed = json.load(f).get("changed") or []
    except (OSError, ValueError, AttributeError):
        return []
    old_db = os.path.join(prev_dir, "index.sqlite")
    if not changed or not os.path.exists(old_db):
        return []
    problems = []
    db = sqlite3.connect(f"file:{os.path.join(new_dir, 'index.sqlite')}?mode=ro", uri=True)
    try:
        db.execute("ATTACH DATABASE ? AS p", (f"file:{old_db}?mode=ro",))
        for key in changed:
            moved = db.execute(
                "SELECT count(*) FROM docs d JOIN sources s ON s.id = d.source_id AND s.key = ? "
                "JOIN p.sources os ON os.key = s.key "
                "JOIN p.docs o ON o.source_id = os.id AND o.title = d.title COLLATE NOCASE "
                "AND o.title = d.title AND coalesce(o.url, '') = coalesce(d.url, '') "
                "WHERE o.id != d.id AND NOT EXISTS (SELECT 1 FROM p.docs o2 WHERE o2.id = d.id "
                "AND o2.source_id = os.id AND o2.title = d.title AND coalesce(o2.url, '') = coalesce(d.url, ''))",
                (key,)).fetchone()[0]
            other = db.execute(
                "SELECT count(*) FROM docs d JOIN sources s ON s.id = d.source_id AND s.key = ? "
                "JOIN p.docs o ON o.id = d.id JOIN p.sources os ON os.id = o.source_id "
                "WHERE NOT (os.key = s.key AND (o.title = d.title "
                "OR (coalesce(o.url, '') = coalesce(d.url, '') AND coalesce(d.url, '') != '')))", (key,)).fetchone()[0]
            if moved:
                problems.append(f"{key}: {moved:,} documents kept their address and title but changed id")
            if other:
                problems.append(f"{key}: {other:,} document ids now name a different document than before")
    except sqlite3.Error as exc:
        problems.append(f"document id check failed: {exc}")
    finally:
        db.close()
    return problems


def check_build(new_dir, prev_dir=None, queries=CHECK_QUERIES, expected=None, source_queries=CHECK_SOURCE_QUERIES):
    """The problems that keep a new build from being swapped in ([] means it passed): the database's integrity,
    every text shard present, each expected source with at least its minimum number of documents (expected:
    {key: min docs}, every source with verified input), each previous source with at least MIN_RATIO of its
    documents, a result for each fixed search, and for each source-specific search a match within that source."""
    from library import search
    problems = []
    db_path = os.path.join(new_dir, "index.sqlite")
    text_dir = os.path.join(new_dir, "text")
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            result = [r[0] for r in db.execute("PRAGMA integrity_check")]
            if result != ["ok"]:
                problems.append("integrity check: " + "; ".join(map(str, result[:5])))
            real_text = os.path.realpath(text_dir)
            for (rel,) in db.execute("SELECT path FROM files"):
                path = os.path.realpath(os.path.join(text_dir, rel))
                if not path.startswith(real_text + os.sep) or not os.path.isfile(path):
                    problems.append(f"missing text shard {rel}")
                    break
        finally:
            db.close()
        rw = sqlite3.connect(db_path)  # the full-text index's own check; it reads, it does not change the file
        try:
            rw.execute("INSERT INTO fts(fts) VALUES('integrity-check')")
        finally:
            rw.close()
    except sqlite3.Error as exc:
        return problems + [f"database: {exc}"]
    if problems:  # a damaged build: the searches below would only fail on it in less readable ways
        return problems
    problems += reference_problems(db_path, text_dir)
    if prev_dir:
        problems += id_problems(new_dir, prev_dir)
    missing = schema_missing(db_path)
    if missing:
        problems.append("missing from the index: " + ", ".join(missing))
    new = _source_counts(db_path)
    if not new or not sum(new.values()):
        problems.append("the build has no documents")
    retired = load_originals() if expected else {}
    for key, least in sorted((expected or {}).items()):
        if new.get(key, 0) < least:
            problem = f"{key}: {new.get(key, 0):,} documents, expected at least {least:,}"
            if key in retired:
                problem += (f" (its original was deleted after conversion, so the build must still hold it; if the "
                            f"source is retired for good, remove its record from {safe_fetch.ORIGINALS} by hand)")
            problems.append(problem)
    if prev_dir and os.path.exists(os.path.join(prev_dir, "index.sqlite")):
        before = read_manifest(prev_dir)
        mine = read_manifest(new_dir)
        off = cleanup.sources_off()
        for key, old in sorted(_source_counts(os.path.join(prev_dir, "index.sqlite")).items()):
            if key in off:
                continue  # switched off: meant to be gone
            meant = int(mine.get(key, {}).get("removed_on_purpose") or 0)
            have = new.get(key, 0) + meant  # documents this build removed on purpose count as kept here
            version = mine.get(key, {}).get("converter", 1)
            if version != before.get(key, {}).get("converter", 1) and version in EXPECTED_DROP.get(key, ()):
                if have < math.ceil(old * MIN_RATIO_EXPECTED_DROP):
                    problems.append(f"{key}: {have - meant:,} documents, down from {old:,}; converter {version} may drop "
                                    f"documents, but not below {MIN_RATIO_EXPECTED_DROP:.0%}")
                else:
                    print(f"{key}: {have - meant:,} documents ({meant:,} removed on purpose), {old:,} before; "
                          f"converter {version} is declared to produce "
                          f"fewer, so it needs {MIN_RATIO_EXPECTED_DROP:.0%} instead of {MIN_RATIO:.0%} this time",
                          flush=True)
                continue
            if have < math.ceil(old * MIN_RATIO):
                problems.append(f"{key}: {have - meant:,} documents"
                                + (f" ({meant:,} removed on purpose)" if meant else "")
                                + f", down from {old:,} (needs {MIN_RATIO:.0%})")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        labels = dict(db.execute("SELECT key, label FROM sources WHERE done=1"))
    finally:
        db.close()
    lib = search.Library(db_path, text_dir=text_dir)
    try:
        for q in queries:
            if not lib.search(q, limit=3):
                problems.append(f"no results for the check search {q!r}")
        for q, keys in source_queries:
            wanted = sorted(k for k in keys if k in labels and k in (expected or new))
            if wanted and not source_hits(db_path, q, wanted):
                problems.append(f"the check search {q!r} found nothing in {', '.join(labels[k] for k in wanted)}")
    except (OSError, sqlite3.Error) as exc:
        problems.append(f"check search failed: {exc}")
    finally:
        lib.db.close()
    return problems


def swap_current(new_dir, current=None):
    """Point the "current" symlink at new_dir atomically: a temporary symlink beside it, renamed over it. The
    link is relative, so the data folder can be moved as a whole."""
    current = current or safe_fetch.CURRENT
    if os.path.lexists(current) and not oscompat.is_link(current):
        raise RuntimeError(f"{current} is not a symlink; move it aside by hand")
    parent = os.path.dirname(current)
    # On Windows a directory junction instead of a symlink, absolute, swapped in two steps (library/oscompat.py).
    oscompat.swap_link(new_dir, current, tmp=os.path.join(parent, f".current-{os.getpid()}.tmp"))


def readers(lib_dir):
    """Process ids of other running programs that opened lib_dir as their library (library/search.py leaves
    <build>/.readers/<pid> with the process's start time when it resolves "current"). A marker is stale, and
    removed, only when the pid is certainly not that process: ps reports a different start time, or the pid is not
    running at all (ProcessLookupError). When ps gives no answer, os.kill(pid, 0) decides; keeping a build too long
    only costs disk."""
    from library.search import process_start
    folder = os.path.join(lib_dir, ".readers")
    alive = []
    try:
        names = os.listdir(folder)
    except OSError:
        return alive
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == os.getpid():
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                recorded = str(json.load(f).get("start") or "")
        except (OSError, ValueError, AttributeError):
            recorded = ""
        now = process_start(pid)
        if now and recorded:
            stale = now != recorded
        else:  # ps gave no answer, or the marker has no start time: is the pid running at all?
            stale = oscompat.pid_gone(pid)  # os.kill(pid, 0) on macOS; on Windows that call would end the process
        if not stale:
            alive.append(pid)
            continue
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    return alive


def prune_builds(keep, libs_dir=None, builds=True):
    """Delete leftover staging folders, kept failed builds (.failed-*), and, with builds=True, every build folder in
    libs_dir, except those in keep and those a running app may still be reading (see readers())."""
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    keep = {os.path.realpath(k) for k in keep if k}
    removed = []
    for name in sorted(os.listdir(libs_dir)):
        path = os.path.join(libs_dir, name)
        leftover = name.startswith((".staging-", FAILED_PREFIX))
        if not (leftover or (builds and LIB_NAME_RE.match(name))) or oscompat.is_link(path):
            continue
        if os.path.realpath(path) in keep or not os.path.isdir(path):
            continue
        in_use = readers(path)
        if in_use:
            print(f"keeping {name}: still open in process {', '.join(map(str, in_use))}", flush=True)
            continue
        if not _remove_tree(path, name):
            continue
        removed.append(name)
    return removed


# Keep the build before the live one for a week, as a fallback a person can point "current" back at by hand. False
# keeps only the live build: the one before goes at the swap, or at a later refresh while a running app still has it
# open (see readers()). Then nothing is left to fall back to without downloading every source again.
KEEP_PREVIOUS_BUILD = True


def _went_live(path):
    """When a build passed its checks (CHECKS_PASSED's "passed", the moment it could go live), for ordering builds:
    folder names do not order them (a re-checked build keeps the date of the run that made it)."""
    try:
        with open(os.path.join(path, CHECKS_PASSED), encoding="utf-8") as f:
            passed = str(json.load(f).get("passed") or "")
    except (OSError, ValueError, AttributeError):
        passed = ""
    return (passed, _lib_sort_key(os.path.basename(path)))


def tidy_builds(current=None, libs_dir=None, previous=None):
    """After a good build is live: delete leftover staging and failed folders, and every lib-* build except the live
    one and (with KEEP_PREVIOUS_BUILD) one previous build: `previous` when given (check_and_swap passes the build
    that was live before the swap) and it is a complete build in libs_dir, else the other complete build that passed
    its checks last. Builds are deleted only when such a previous build exists: without one there is no telling
    which folders are safe to delete. Builds a running app reads are kept (prune_builds). Returns the names removed."""
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    live = live_build(current, libs_dir)
    if not live:
        return []
    others = [os.path.realpath(os.path.join(libs_dir, n)) for n in os.listdir(libs_dir) if LIB_NAME_RE.match(n)
              and not os.path.islink(os.path.join(libs_dir, n)) and _complete(os.path.join(libs_dir, n))
              and os.path.realpath(os.path.join(libs_dir, n)) != live]
    previous = os.path.realpath(previous) if previous else None
    if previous not in others:
        previous = max(others, key=_went_live) if others else None
    keep = {live} | ({previous} if previous and KEEP_PREVIOUS_BUILD else set())
    return prune_builds(keep, libs_dir, builds=previous is not None)


def build_and_swap(force=(), queries=CHECK_QUERIES, current=None, libs_dir=None, inputs=None, date=None,
                   source_queries=CHECK_SOURCE_QUERIES, upgrade=False, only=None):
    """Build what changed, check it, and swap it in. Returns a dict: status ("noop", "swapped" or "failed"),
    changed (keys), build (the new folder, when swapped), problems (why it failed), removed (pruned builds).
    A no-op tidies the builds too (tidy_builds()), so a build kept at the last swap because a running app still
    had it open goes at the next run once that app has closed it, even when nothing changed.
    upgrade=True: see build(); the new folder goes through the same checks and swap as any build."""
    current = current or safe_fetch.CURRENT
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    inputs = source_inputs() if inputs is None else inputs
    prev = previous_build(current, libs_dir)
    try:
        new, changed = build(force, prev=prev, inputs=inputs, libs_dir=libs_dir, date=date, upgrade=upgrade,
                             current=current, only=only)
    except SourceError as exc:  # the staging folder is already gone; "current" was never touched
        return {"status": "failed", "changed": [], "problems": [str(exc)]}
    if new is None:
        return {"status": "noop", "changed": [], "problems": [], "removed": tidy_builds(current, libs_dir)}
    return check_and_swap(new, prev, inputs, sorted(changed), queries, source_queries, current, libs_dir)


FAILED_PREFIX = ".failed-"
CHECKS_FAILED = "checks-failed.json"


def remove_older_failed(libs_dir, newest, current=None):
    """Delete the .failed-* builds in libs_dir other than newest (the one just kept for --recheck), except the folder
    "current" points at and any a running process has open (readers()), so failed weeks do not pile up a build
    each. Returns the names removed."""
    newest = os.path.realpath(newest)
    live = os.path.realpath(current or safe_fetch.CURRENT)
    removed = []
    for name in sorted(os.listdir(libs_dir)):
        path = os.path.join(libs_dir, name)
        if not name.startswith(FAILED_PREFIX) or os.path.islink(path) or not os.path.isdir(path) \
                or os.path.realpath(path) in (newest, live):
            continue
        in_use = readers(path)
        if in_use:
            print(f"keeping {name}: still open in process {', '.join(map(str, in_use))}", flush=True)
            continue
        if not _remove_tree(path, name):
            continue
        removed.append(name)
    return removed


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def expected_docs(inputs, build_dir):
    """{key: fewest documents} for check_build: each source with input on disk at its minimum, and each source the
    build carried over without its input (an original deleted after conversion) at 1, so the searches that need a
    source still run when only its converted text is left."""
    off = cleanup.sources_off()
    expected = {k: inp.get("min_docs", 1) for k, inp in inputs.items() if k not in off}
    for key in read_manifest(build_dir):
        expected.setdefault(key, 1)
    # A source whose original was deleted after conversion must still be in the build, even when no previous build
    # is left to carry it over and its download failed: a smaller library is never swapped in for that. Sources the
    # Kiwix catalog no longer lists are exempt (their copy is gone for good).
    gone = catalog_missing_keys() | off
    for key in load_originals():
        if key not in gone:
            expected.setdefault(key, 1)
    return expected


def catalog_missing_keys(path=None):
    """Build keys of the sources the Kiwix catalog no longer listed at the last refresh (refresh.py writes the
    names, "<catalog name> <flavour>", to missing-sources.json)."""
    try:
        with open(path or safe_fetch.MISSING_SOURCES, encoding="utf-8") as f:
            names = json.load(f)
    except (OSError, ValueError):
        return set()
    keys = set()
    for name in names if isinstance(names, list) else ():
        parts = str(name).split()
        prefix = "_".join(parts)
        prefix = CATALOG_FILE_PREFIX.get(prefix, prefix)
        if prefix in ZIM_SOURCES:
            keys.add(ZIM_SOURCES[prefix][0])
    return keys


def log_ratios(build_dir, inputs):
    """For PDF and web ZIMs, where the share rule does not apply, log how many documents came from how many
    "articles", with a warning below MIN_ZIM_SHARE, so a thin source is seen without failing the build."""
    try:
        counts = _source_counts(os.path.join(build_dir, "index.sqlite"))
    except sqlite3.Error as exc:  # a damaged index: check_build reports it; the build must still be kept aside
        print(f"ratios not logged: {exc}", flush=True)
        return
    for key, inp in sorted(inputs.items()):
        articles = inp.get("articles") or 0
        if "kind" not in inp or inp["kind"] in SHARE_RULE_KINDS or articles <= 1:
            continue
        have = counts.get(key, 0)
        flag = "WARNING: " if have < articles * MIN_ZIM_SHARE else ""
        print(f"{flag}{key}: {have:,} documents from {articles:,} ZIM articles ({have / articles:.0%}; "
              f"no share rule for {inp['kind']} ZIMs)", flush=True)


def check_and_swap(new, prev, inputs, changed, queries=CHECK_QUERIES, source_queries=CHECK_SOURCE_QUERIES,
                   current=None, libs_dir=None):
    """Check a finished build and swap it in, or keep it as libs/.failed-<name> for refresh.py --recheck."""
    current = current or safe_fetch.CURRENT
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    expected = expected_docs(inputs, new)
    try:
        problems = check_build(new, prev, queries, expected, source_queries)
    except Exception as exc:  # a check that crashes is a failed check: the build is still kept for --recheck
        problems = [f"the checks crashed: {exc!r}"]
    log_ratios(new, inputs)
    if problems:
        kept = new
        if not os.path.basename(new).startswith(FAILED_PREFIX):
            kept = os.path.join(libs_dir, FAILED_PREFIX + os.path.basename(new))
            n = 2
            while os.path.lexists(kept):
                kept = os.path.join(libs_dir, f"{FAILED_PREFIX}{os.path.basename(new)}.{n}")
                n += 1
            os.rename(new, kept)  # never counted as a build, never "current"
        with open(os.path.join(kept, CHECKS_FAILED), "w", encoding="utf-8") as f:
            json.dump({"failed": _now(), "problems": problems, "expected": expected, "changed": changed}, f,
                      indent=1, sort_keys=True)
        removed = remove_older_failed(libs_dir, kept, current)
        for name in removed:
            print(f"removed the older failed build {name}", flush=True)
        print("build NOT swapped in; these checks failed:", flush=True)
        for problem in problems:
            print(f"  - {problem}", flush=True)
        print(f"kept for a re-check without converting again: {kept}", flush=True)
        print(f"  .venv/bin/python library/refresh.py --recheck {kept}", flush=True)
        return {"status": "failed", "changed": changed, "problems": problems, "kept": kept, "removed": removed}
    with open(os.path.join(new, CHECKS_PASSED), "w", encoding="utf-8") as f:
        json.dump({"passed": _now(), "expected": expected}, f, indent=1, sort_keys=True)
    swap_current(new, current)
    removed = tidy_builds(current, libs_dir, previous=prev)
    return {"status": "swapped", "changed": changed, "build": new, "problems": [], "removed": removed}


def recheck(path, queries=CHECK_QUERIES, source_queries=CHECK_SOURCE_QUERIES, current=None, libs_dir=None,
            inputs=None):
    """Run the checks again on a build kept as libs/.failed-<name> (after a check was fixed or adjusted) and swap it
    in under its own lib-<date> name if they pass. Refused when it is not such a folder, or when a good build newer
    than it is live (swapping it in would go back in time)."""
    current = current or safe_fetch.CURRENT
    libs_dir = libs_dir or safe_fetch.LIBS_DIR
    real, real_libs = os.path.realpath(path), os.path.realpath(libs_dir)
    name = os.path.basename(real)
    base = re.sub(r"\.\d+$", "", name[len(FAILED_PREFIX):]) if name.startswith(FAILED_PREFIX) else ""
    if os.path.dirname(real) != real_libs or not LIB_NAME_RE.match(base) or not os.path.isfile(
            os.path.join(real, "index.sqlite")) or not os.path.isfile(os.path.join(real, BUILD_MANIFEST)):
        return {"status": "refused", "problems": [f"{path} is not a failed build kept in {libs_dir}"]}
    prev = previous_build(current, libs_dir)

    def built(folder):
        try:
            with open(os.path.join(folder, BUILD_MANIFEST), encoding="utf-8") as f:
                return json.load(f).get("built", "")
        except (OSError, ValueError):
            return ""
    if prev and built(prev) > built(real):
        return {"status": "refused", "problems": [f"a newer good build is live ({os.path.basename(prev)})"]}
    inputs = source_inputs() if inputs is None else inputs
    print(f"re-checking {name}", flush=True)
    final = unique_lib_dir(libs_dir, date=_lib_sort_key(base)[0])
    expected = expected_docs(inputs, real)
    problems = check_build(real, prev, queries, expected, source_queries)
    log_ratios(real, inputs)
    if problems:
        with open(os.path.join(real, CHECKS_FAILED), "w", encoding="utf-8") as f:
            json.dump({"failed": _now(), "problems": problems, "expected": expected}, f, indent=1, sort_keys=True)
        print("still failing:", flush=True)
        for problem in problems:
            print(f"  - {problem}", flush=True)
        return {"status": "failed", "problems": problems, "kept": real}
    os.rename(real, final)
    if os.path.exists(os.path.join(final, CHECKS_FAILED)):
        os.remove(os.path.join(final, CHECKS_FAILED))
    with open(os.path.join(final, CHECKS_PASSED), "w", encoding="utf-8") as f:
        json.dump({"passed": _now(), "expected": expected, "rechecked": True}, f, indent=1, sort_keys=True)
    swap_current(final, current)
    removed = tidy_builds(current, libs_dir, previous=prev)
    return {"status": "swapped", "build": final, "problems": [], "removed": removed}


def parse_rebuild(args, flag="--rebuild"):
    """The keys after --rebuild (or another flag that takes source keys, such as refresh.py's --only); SystemExit for
    an unknown key."""
    if flag not in args:
        return set()
    keys = set(itertools.takewhile(lambda a: not a.startswith("--"), args[args.index(flag) + 1:]))
    unknown = keys - KNOWN_KEYS
    if unknown or not keys:
        sys.exit(f"{flag} takes known source keys: " + (", ".join(sorted(unknown)) + " not known; " if unknown
                                                         else "") + "known: " + ", ".join(sorted(KNOWN_KEYS)))
    return keys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    set_workers(args)
    force = parse_rebuild(args)
    from library.refresh import stay_low_priority
    stay_low_priority()  # a build started by hand stays in the background too
    lock = safe_fetch.take_lock()
    if lock is None:
        sys.exit("another refresh or build is already running")
    if "--reclean" in args:
        if force:
            sys.exit("--reclean takes no --rebuild; it converts nothing")
        from library import reclean
        if "--dry-run" in args:
            todo = reclean.pending(read_manifest(previous_build()))
            print("would re-clean: " + (", ".join(f"{k} ({why})" for k, why in todo.items()) or "nothing"), flush=True)
            if todo:  # what each rule would remove, read from the live build; nothing is written to it
                counts, path = reclean.preview()
                for why, n in sorted(counts["removed"].items(), key=lambda x: -x[1]):
                    print(f"  would remove {n:,}: {why}", flush=True)
                print(f"  would retitle {counts['retitled']:,}", flush=True)
                print(f"20 random titles per rule: {path}", flush=True)
            return 0
        result = reclean.reclean_and_swap()
        print(json.dumps(result, indent=1), flush=True)
        return 1 if result["status"] == "failed" else 0
    inputs = source_inputs()
    if "--dry-run" in args:
        changed = changed_sources(inputs, read_manifest(previous_build()), force)
        print("would rebuild: " + (", ".join(build_order(changed)) or "nothing"), flush=True)
        if "--upgrade" in args:
            print("would add: " + (", ".join(needs_upgrade(previous_build()) or []) or "nothing"), flush=True)
        return 0
    if "--upgrade" in args:
        if force:
            sys.exit("--upgrade takes no --rebuild; it converts nothing")
        missing = needs_upgrade(previous_build())
        print("upgrade: the live build lacks " + (", ".join(missing) or "nothing"), flush=True)
        # Inputs without the sources whose input changed: --upgrade only adds what the live build lacks; a source
        # with a newer original waits for the weekly refresh, which converts it.
        prev_manifest = read_manifest(previous_build())
        unchanged = {k: v for k, v in inputs.items() if k not in changed_sources(inputs, prev_manifest)}
        result = build_and_swap(inputs=unchanged, upgrade=True)
        print(json.dumps(result, indent=1), flush=True)
        return 1 if result["status"] == "failed" else 0
    missing = force - set(inputs)
    if missing:
        sys.exit("no input on disk for: " + ", ".join(sorted(missing)) + " (an original deleted after conversion: "
                 "refresh.py --rebuild " + " ".join(sorted(missing)) + " downloads it again)")
    result = build_and_swap(force, inputs=inputs)
    print(json.dumps(result, indent=1), flush=True)
    if result["status"] == "failed":
        return 1
    retire_originals()
    return 0


if __name__ == "__main__":
    sys.exit(main())
