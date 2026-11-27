"""PDF to text, page by page: page furniture out, bullets kept with their items, columns read one after the other.

pdftotext's reading-order mode (what the build used before) loses where each page ends and emits a column of bullets
before the items they mark. This converter reads the PDF three times with poppler's pdftotext:

- `-layout` gives each page as a grid of characters, pages split by a form feed. A bullet sits on the same line as
  its item, and two columns sit side by side.
- `-bbox` gives every word's box on the page, so a line at a page edge can be told apart by where it sits and how
  big it is.
- the plain reading order, the old converter's text, which every page is checked against (step 4).

Then, page by page:

1. Page furniture (find_furniture()): a cell of a line near the top or bottom edge that the document prints on at
   least FURNITURE_PAGES pages with the same words (digits aside: "13 Ready.gov", "Page 1-7", "4-23 ATP 3-50.21"),
   at the same height on the page and in the same type size. Its numbers must be the same on every copy or count
   with the page (a constant offset, or a section label starting a new section). A line goes only when every cell
   on it is furniture, and only from the page edge inwards; a line of several cells only with its page number.
   What the rules keep:
   - the first copy of every repeated line (a footer's date, a running title, a table header over continuation
     pages), unless the document prints that text elsewhere as a line of its own (a chapter's title);
   - a table's row (two cells or more, over a line with cells in the same columns);
   - a line whose numbers rise or change any old way (a running count, a table's last row);
   - a heading a few sections repeat ("WARNING", "PROBLEM"), spread thinner than a running header;
   - a chapter's title on its opening page, printed larger or lower than the running header that repeats it;
   - a header block that runs on into the page's own text (the Emergency Response Guidebook's "GUIDE" over each
     guide's number).
   A bare page number printed elsewhere goes only alone on its line, at the page edge, counting with its neighbours.
2. Columns (columns()): where a run of lines shares a blank gutter, with text on both sides whose right-hand lines
   start in one column, the left column is read before the right one. A table with short cells keeps its rows.
3. Paragraphs (paragraphs()): lines join into paragraphs; a bullet starts a list item ("- item"), which runs on
   until a line comes back out of its indent (a dash, star or "o" only where a bullet can stand); a table's row
   label comes before the row's items when the block holds one row. A word split at a line end ("far-" /
   "reaching") is joined back. join_pages() joins a paragraph a page break cut.
4. The check (pages()): a page keeps this reading only when it has every letter and digit of the reading-order text
   apart from the furniture removed, and its words follow the old order except for whole blocks moved (see
   ORDER_SHARE). Otherwise the page keeps the reading-order text with only its furniture lines taken off, so no page
   reads worse than with the old converter. So does every page past the time budget (PY_BUDGET).

Nothing is written that the page does not print, except the "- " that marks a list item in place of its bullet
glyph. convert_path() returns the text; pages() also returns what was removed from each page and how each page was
read, for the evidence tooling and the tests.
"""
import collections
import html
import os
import re
import shutil
import subprocess
import time


def _poppler(name):
    for path in (shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return f"/opt/homebrew/bin/{name}"


PDFTOTEXT = _poppler("pdftotext")
TIMEOUT = 60     # seconds per pdftotext run; a PDF takes three (layout, word boxes, reading order) ...
PY_BUDGET = 60   # ... and its page analysis this long at most, so a PDF stays within the build's 240 s. Past it,
                 # the pages not yet done keep pdftotext's reading-order text (see pages()).
MAX_ROUNDS = 20  # find_furniture() settles in two or three rounds; this caps it whatever the input

FURNITURE_PAGES = 4      # a running line repeats on at least this many pages ...
EDGE_LINES = 3           # ... among the first or last few lines of each ...
EDGE_BAND = 0.2          # ... in the top or bottom fifth of the page ...
SAME_Y = 2.5             # ... at the same height on the page (points) ...
SAME_SIZE = 1.0          # ... in the same type size (points)
SENTENCE_SHARE = 0.3     # a full sentence repeated at an edge must be on this share of the pages to count
NEAR_PAGES = 5           # a running line without numbers is printed on at least NEAR_COPIES of the pages within
NEAR_COPIES = 3          # this many pages of each copy (a heading a few sections repeat, "PROBLEM", "WARNING", is
                         # spread thinner); one with numbers shows itself by them (see _numbers_in_step())
# A line of several cells ("Disease   Carrier   Transmission   Region") goes only when one of them is the page number
# counting with the page ("August 2019   Page 1-7", "3-8   ATP 3-50.21   18 September 2018"): a table's header row
# over its continuation pages repeats as faithfully as any running header. Running headers without a page number
# ("CERT Unit 1: Disaster Preparedness   Participant Manual") stay. (Switchable for the evidence tooling only.)
MULTI_CELL_NEEDS_PAGE_NUMBER = True
SHORT_CELL = 12          # a cell this short under a header cell, aligned with it, ties the header to the page
STRONG_SHARE = 0.25      # a running line printed on at least this share of the pages (see find_furniture())
STEP_SHARE = 0.8         # the share of a running line's copies whose numbers keep step with the page (see below)
CELL_GAP = re.compile(r"\s{3,}")
DIGITS = re.compile(r"\d+")
PAGE_LABEL = re.compile(r"(?i)^(?:page\s+)?(?:(?P<pre>[A-Z]-?)?(?P<a>\d{1,4})(?:\s*[-–]\s*(?P<b>\d{1,4}))?"
                        r"|(?P<roman>[ivxlc]{1,6}))$")

# A bullet glyph at the start of a line, then a space: the round and square bullets, check boxes, arrows, Wingdings
# bullets in the private-use area, and a dash, star or "o" used as a bullet.
BULLET_GLYPHS = "•▪●◦○■□❑❏✓✔➢►▶❖♦◆◼◾▫‣⁃∙"
GLYPH = re.compile(r"^(?:[" + BULLET_GLYPHS + r"]|[-–—*o](?=\s+\S))\s*")
# A line that starts a numbered item or a numbered paragraph: "1.", "a)", "(3)", "3-22." (Army paragraph numbers).
NUMBERED = re.compile(r"^(?:\(?\d{1,2}[.)]|\(?[a-z][.)]|\(\d{1,2}\)|\([a-z]\)|\d{1,2}-\d{1,3}\.|[A-Z]-\d{1,3}\.)\s+\S")
# Army manuals set their bullets in ZapfDingbats, which pdftotext reads as the letter "z" ("z Forgetfulness."). A "z"
# is taken as a bullet only in a document where at least Z_BULLETS lines start with one.
Z_BULLET = re.compile(r"^z\s+(?=[A-Z0-9(“\"])")
Z_BULLETS = 5
LEADER = re.compile(r"(?:\.\s?){4,}\s*\S{0,8}$")  # a contents line: "Food ........ 4-1"
SENTENCE_END = re.compile(r"[.!?:;)\"”’]$")
SOFT_BULLETS = "-–—*o"  # a dash, star or "o" starts a list item only where a bullet can stand (see paragraphs())
# A page whose new text reads in another order than pdftotext's reading-order text keeps that text instead. Repaired
# lists and columns move whole blocks: their words stay in long runs that follow the old order. A table read down its
# columns instead of along its rows breaks into runs of a few words. A page falls back when more than ORDER_SLACK words
# and more than ORDER_SHARE of its words sit outside runs of at least RUN_MIN words in the old order.
RUN_MIN = 8
ORDER_SLACK = 15
ORDER_SHARE = 0.12


ROMAN_EDGE = re.compile(r"^[ivxlc]{1,6}(?=\s)|(?<=\s)[ivxlc]{1,6}$")


def _template(s):
    """A line's words with its numbers as "#" ("13 Ready.gov", "ii Ready.gov" -> "# Ready.gov")."""
    return ROMAN_EDGE.sub("#", DIGITS.sub("#", " ".join(s.split())))


def _run(args, timeout=TIMEOUT):
    r = subprocess.run([PDFTOTEXT, "-q", "-enc", "UTF-8", *args], capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext exited {r.returncode}")
    return r.stdout.decode("utf-8", "replace")


WORD_RX = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">(.*?)</word>', re.S)
PAGE_RX = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)


def _bbox(path):
    """[(page height, [(text, yMin, yMax)])] for every page."""
    out = []
    for m in PAGE_RX.finditer(_run(["-bbox", path, "-"])):
        words = [(html.unescape(w[4]), float(w[1]), float(w[3])) for w in WORD_RX.findall(m.group(3))]
        out.append((float(m.group(2)), words))
    return out


def _cells(line):
    """[(start column, end column, text)] of a layout line, split at runs of three spaces or more."""
    cells, pos = [], 0
    for part in CELL_GAP.split(line):
        start = line.index(part, pos) if part else pos
        if part.strip():
            cells.append((start, start + len(part), part.strip()))
        pos = start + len(part)
    return cells


def _locate(tokens, words, top):
    """(yMin, yMax, type size) of a cell's words on the page, or None. A run of words in the page's word list equal
    to the cell's tokens; when more than one, the one nearest the edge."""
    n = len(tokens)
    best = None
    for i in range(len(words) - n + 1):
        if words[i][0] != tokens[0] or [w[0] for w in words[i:i + n]] != tokens:
            continue
        run = words[i:i + n]
        box = (min(w[1] for w in run), max(w[2] for w in run), max(w[2] - w[1] for w in run))
        if best is None or (box[0] < best[0] if top else box[1] > best[1]):
            best = box
    return best


def _wordy(text):
    """A cell with something to read: a digit or two letters ("12", "Food"), not a bullet glyph ("•", "z")."""
    return bool(re.search(r"\d|[^\W\d_].*[^\W\d_]", text)) or bool(PAGE_LABEL.match(text.strip()))


def _label(text):
    """(shape, number, chapter) of a bare page label ("12", "4-23", "A-3", "Page 1-7", "iv"), or None."""
    m = PAGE_LABEL.match(text.strip())
    if not m:
        return None
    if m.group("roman"):
        return ("roman", None, None)
    if m.group("b"):
        return ("pair" + (m.group("pre") or ""), int(m.group("b")), int(m.group("a")))
    return ("one" + (m.group("pre") or ""), int(m.group("a")), None)


def _numbers_in_step(items):
    """True when the numbers in a running line's copies are page furniture: the same in every copy ("August 2019",
    "ATP 3-50.21"), or each either unchanged or counting on with the page from one copy to the next ("Page 1-7",
    "13 Ready.gov", "Chapter 3": the page number with a constant offset), or a section label starting a new section
    ("Page 1-29", then "Page 3-1": an earlier number up, the last one no further than the pages between). A number
    that merely rises ("Cumulative cases reported 3", "70") or changes any old way (a table's last row) is not one."""
    seq = sorted((p, [int(n) for n in DIGITS.findall(text)]) for p, text in items)
    if len({tuple(n) for _, n in seq}) <= 1:
        return True
    pairs = list(zip(seq, seq[1:]))
    good = sum(1 for (p, a), (q, b) in pairs if len(a) == len(b) and _steps(a, b, q - p))
    return good >= STEP_SHARE * len(pairs)


def _steps(a, b, d):
    """True when numbers b, d pages after numbers a, are the same page label counting on: each number unchanged or
    up by exactly d, or a new section (an earlier number up, the last one restarted at most d pages ago)."""
    if all(y - x in (0, d) for x, y in zip(a, b)):
        return True
    return len(a) >= 2 and 1 <= b[-1] <= d and any(y > x for x, y in zip(a[:-1], b[:-1]))


def _attached(lines, inner, order, side):
    """True when the line just inside a page's edge lines carries on their block: no blank line between, and a
    cell starting in the same column as one of theirs, or a short cell ending in the same column or centred under
    one. The Emergency Response Guidebook prints "GUIDE" over the guide's number and its title over "(Extreme
    Hazard)": the repeated half is that page's title, not furniture."""
    k = order.index(inner)
    if k + 1 >= len(order):
        return False
    nxt = order[k + 1]
    if abs(nxt - inner) != 1:
        return False
    mine = [(key[2], key[3]) for key in side if key[1] == inner]
    for c in _cells(lines[nxt]):
        if not _wordy(c[2]):
            continue
        for a, e in mine:
            if abs(c[0] - a) <= 1:
                return True
            # a short cell under the header's, aligned by its end or its middle: the guide's number right under a
            # right-aligned "GUIDE" on the Guidebook's right-hand pages
            if len(c[2]) <= SHORT_CELL and (abs(c[1] - e) <= 1 or abs((c[0] + c[1]) - (a + e)) <= 2):
                return True
    return False


def _titles_kept(page_lines, accepted, cands):
    """The furniture copies that stay: the first copy in each run of a repeated line ("HAZARD IDENTIFICATION NUMBERS"
    over four pages of the Emergency Response Guidebook, a table's header row over its continuation pages, the date
    in a footer), unless the document prints that text elsewhere as a line of its own (a chapter's title on its
    opening page). A line printed once (a page number) is not a repeated line: every copy differs."""
    body = set()
    for p, lines in enumerate(page_lines):
        for i, ln in enumerate(lines):
            for c in _cells(ln):
                if (p, i, c[0], c[1]) not in accepted:
                    body.add(" ".join(c[2].split()))
    runs = collections.defaultdict(list)
    for k in accepted:
        runs[" ".join(cands[k][4].split())].append(k)
    keep = set()
    for text, keys in runs.items():
        if text in body or len(keys) < 2:
            continue
        keys.sort()
        last = None
        for k in keys:
            if last is None or k[0] - last > NEAR_PAGES:
                keep.add(k)
            last = k[0]
    return keep


def _table_row(lines, i, order):
    """True when line i is a row of a table: two cells or more, and the next line inward has two cells or more
    starting in the same columns ("Disease   Carrier   Region" over a table's rows)."""
    mine = [c[0] for c in _cells(lines[i]) if _wordy(c[2])]
    k = order.index(i)
    if len(mine) < 2 or k + 1 >= len(order):
        return False
    theirs = [c[0] for c in _cells(lines[order[k + 1]])]
    return len(theirs) >= 2 and sum(any(abs(a - b) <= 2 for b in theirs) for a in mine) >= 2


def _in_step_nearby(k, cands, keys):
    """True when a copy of the same line within three pages, wherever it sits, reads the same ("Unit 1 Table of
    Contents" on both pages of a contents) or has numbers counting in step with this one's ("Page 1-28" on the page
    before "Page 1-29"). A chapter's opening "Chapter 5" has no such neighbour."""
    mine = [int(n) for n in DIGITS.findall(cands[k][4])]
    for o in keys:
        d = o[0] - k[0]
        if o == k or not 0 < abs(d) <= 3:
            continue
        theirs = [int(n) for n in DIGITS.findall(cands[o][4])]
        if cands[o][4] == cands[k][4] or (len(theirs) == len(mine) and all(y - x in (0, d) for x, y in zip(mine, theirs))
                                          and any(y - x == d for x, y in zip(mine, theirs))):
            return True
    return False


def _repeated_words(k, cands, by_tpl):
    """True for a cell of words ("Unit 2 Table of Contents", "2019 Update") whose kind repeats at page edges on at
    least FURNITURE_PAGES pages. A table cell of figures ("3,472") never is one."""
    return k in cands and bool(re.search(r"[^\W\d_]{3}", cands[k][4])) and \
        len({o[0] for o in by_tpl[cands[k][:2]]}) >= FURNITURE_PAGES


class OutOfTime(Exception):
    """The page analysis ran past its budget (PY_BUDGET)."""


def _check(deadline):
    if deadline is not None and time.monotonic() > deadline:
        raise OutOfTime()


def find_furniture(layout, boxes, deadline=None):
    """{page index: {(line index, cell start, cell end): text}} of the furniture cells (see the module notes).
    Raises OutOfTime past deadline (a time.monotonic() value)."""
    n_pages = len(layout)
    page_lines = [page.split("\n") for page in layout]
    cands = {}  # (page, line, cell start, cell end) -> (template, side, y, size, text)
    for p, lines in enumerate(page_lines):
        if p >= len(boxes):
            break
        _check(deadline)
        height, words = boxes[p]
        filled = [i for i, ln in enumerate(lines) if ln.strip()]
        for side, idxs in (("top", filled[:EDGE_LINES]), ("bottom", filled[-EDGE_LINES:])):
            for i in idxs:
                for cell in _cells(lines[i]):
                    if not _wordy(cell[2]):
                        continue  # a lone bullet glyph or mark, never furniture on its own
                    box = _locate(cell[2].split(), words, side == "top")
                    if not box:
                        continue
                    if (side == "top" and box[0] > EDGE_BAND * height) or \
                            (side == "bottom" and box[1] < (1 - EDGE_BAND) * height):
                        continue
                    cands[(p, i, cell[0], cell[1])] = (_template(cell[2]), side, box[0] if side == "top" else box[1],
                                                       box[2], cell[2])
    by_tpl = collections.defaultdict(list)
    for k, v in cands.items():
        by_tpl[v[:2]].append(k)
    accepted = set(cands)
    for _ in range(MAX_ROUNDS):
        _check(deadline)
        groups = collections.defaultdict(list)
        for k in accepted:
            groups[cands[k][:2]].append(k)
        keep = set()
        for (tpl, side), keys in groups.items():
            sentence = len(tpl.split()) >= 5 and re.search(r"[a-z][.!?]$", tpl)
            need = max(FURNITURE_PAGES, int(SENTENCE_SHARE * n_pages)) if sentence else FURNITURE_PAGES
            for k in keys:
                _, _, y, size, _ = cands[k]
                same = [o for o in keys if abs(cands[o][2] - y) <= SAME_Y and abs(cands[o][3] - size) <= SAME_SIZE]
                pages = {o[0] for o in same}
                near = sum(1 for q in pages if abs(q - k[0]) <= NEAR_PAGES)
                texts = {cands[o][4] for o in same}
                nearby = "#" in tpl and _in_step_nearby(k, cands, by_tpl[(tpl, side)])
                # a numbered line counting in step with a neighbour may sit a little higher or lower in each chapter
                # ("Unit 1 Table of Contents", "Unit 3 Table of Contents"): its pages are counted wherever it sits
                enough = len(pages) >= need or (nearby and len({o[0] for o in keys}) >= need)
                if enough and (near >= NEAR_COPIES or ("#" in tpl and (len(texts) == 1 or nearby))) and \
                        ("#" not in tpl or _numbers_in_step([(o[0], cands[o][4]) for o in same])):
                    keep.add(k)
        # Only whole lines go, and only from the page edge inwards: a line goes when every cell on it is furniture
        # and every line between it and the edge has gone (a line with nothing to read, a rule, is passed over).
        by_page = collections.defaultdict(set)
        for k in keep:
            by_page[k[0]].add(k)
        # The page's own footer, printed on most pages ("August 2019", "Page 2-17"), takes the outermost line with it
        # when the rest of that line repeats at page edges too ("Unit 2 Table of Contents" on the one contents page
        # of a unit, "2019 Update" on a page updated since).
        strong = collections.Counter()
        for k in keep:
            strong[cands[k][:2]] += 1
        strong = {t for t, n in strong.items() if n >= STRONG_SHARE * n_pages}
        edge = set()
        for p, keys in by_page.items():
            lines = page_lines[p]
            filled = [i for i, ln in enumerate(lines) if ln.strip()]
            for order in (filled, filled[::-1]):
                side = set()
                inner = None
                for i in order[:EDGE_LINES]:
                    cells = [c for c in _cells(lines[i]) if _wordy(c[2])]
                    if not cells:
                        continue
                    ks = [(p, i, c[0], c[1]) for c in cells]
                    if _table_row(lines, i, order):
                        break  # a table's header or last row stays, however often it repeats
                    if MULTI_CELL_NEEDS_PAGE_NUMBER and len(cells) > 1 and \
                            not any(x in keys and _label(cands[x][4]) for x in ks):
                        break  # a line of several cells goes only with its page number: else it may be a table's
                    if not all(x in keys for x in ks):
                        if not (i == order[0] and any(x in keys and cands[x][:2] in strong for x in ks) and
                                all(x in keys or _repeated_words(x, cands, by_tpl) for x in ks)):
                            break
                    side.update((p, i, c[0], c[1]) for c in cells)
                    inner = i
                if side and _attached(lines, inner, order, side):
                    # the block that runs on into the page's text stays; lines a blank line apart from it still go
                    block, j = {inner}, inner
                    while (j - 1 if order is filled else j + 1) in {k[1] for k in side}:
                        j = j - 1 if order is filled else j + 1
                        block.add(j)
                    side = {k for k in side if k[1] not in block}
                edge.update(side)
        if edge == accepted:
            break
        accepted = edge
    else:
        # never settled within MAX_ROUNDS: remove nothing rather than the larger guess
        print(f"    furniture search did not settle in {MAX_ROUNDS} rounds; no page furniture removed", flush=True)
        return {}
    accepted -= _titles_kept(page_lines, accepted, cands)
    found = collections.defaultdict(dict)
    labels = collections.defaultdict(dict)  # shape -> {page: (number, chapter)} of page labels found as furniture
    for k in accepted:
        found[k[0]][k[1:]] = cands[k][4]
        lab = _label(cands[k][4])
        if lab and lab[0] != "roman":
            labels[lab[0]][k[0]] = lab[1:]
    # A bare page label left at the outermost line of a page (a chapter's opening page prints its number elsewhere)
    # goes when it counts in step with the page labels found on the pages around it.
    for p, lines in enumerate(page_lines):
        gone = {k[0] for k in found.get(p, {})}
        filled = [i for i, ln in enumerate(lines) if ln.strip() and i not in gone]
        for i in {filled[0], filled[-1]} if filled else ():
            cells = _cells(lines[i])
            if len(cells) != 1 or lines[i].strip() != cells[0][2] or p >= len(boxes):
                continue  # alone on its line
            lab = _label(cells[0][2])
            if not lab or lab[0] not in labels:
                continue
            height, words = boxes[p]
            box = _locate(cells[0][2].split(), words, i == filled[0])
            if not box or EDGE_BAND * height < box[0] and box[1] < (1 - EDGE_BAND) * height:
                continue  # at the page edge
            known = labels[lab[0]]
            if any(q in known and known[q][1] == lab[2] and known[q][0] - lab[1] == q - p
                   for q in range(p - 3, p + 4) if q != p):
                found[p][(i, cells[0][0], cells[0][1])] = cells[0][2]
    return found


def _strip_furniture(page, cells):
    """The page's lines with the furniture cells blanked (a line left empty is dropped)."""
    lines = page.split("\n")
    by_line = collections.defaultdict(list)
    for (i, a, b) in cells:
        by_line[i].append((a, b))
    out = []
    for i, ln in enumerate(lines):
        for a, b in by_line.get(i, ()):
            ln = ln[:a] + " " * (b - a) + ln[b:]
        if i in by_line and not re.search(r"[^\W_]", ln):
            ln = ""  # what a footer leaves between its parts: "38 | International first aid ..." -> "|"
        out.append(ln.rstrip() if ln.strip() else "")
    return out


# ---------- columns ----------

MIN_TWO_SIDED = 3  # a gutter needs text on both sides of it on at least this many lines
ALIGN = 4          # a column's lines start within this many characters of each other (a bullet and its item)
MIN_CELL = 12      # half the lines on each side of a gutter hold at least this many characters (else: a table)


def _gutter_runs(lines, c):
    """[(start, end)] of the runs of lines where column c is a gutter: c-1 and c blank on every line."""
    runs, start = [], None
    for i, ln in enumerate(lines):
        holds = not ln.strip() or ((c >= len(ln) or ln[c] == " ") and (c - 1 >= len(ln) or ln[c - 1] == " "))
        if holds and start is None:
            start = i
        elif not holds and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(lines)))
    return runs


def _score(lines, c):
    """How many lines with text on both sides of column c start their right-hand text in about the same column, or
    0 when the two sides are not columns of text: a column's lines start at one place (a run of spaces inside a
    line of scanned prose, "weapons-for     overall", does not line up from line to line), and a table's short
    cells ("Transportation", "Structures") stay in their rows."""
    both = [ln for ln in lines if ln[:c].strip() and ln[c:].strip()]
    if len(both) < MIN_TWO_SIDED:
        return 0
    starts = collections.Counter(c + len(ln[c:]) - len(ln[c:].lstrip(" ")) for ln in both)
    mode = starts.most_common(1)[0][0]
    n = sum(k for at, k in starts.items() if mode - ALIGN <= at <= mode + ALIGN)  # a bullet's hanging indent
    left = sorted(len(ln[:c].strip()) for ln in both)
    right = sorted(len(ln[c:].strip()) for ln in both)
    if left[len(left) // 2] < MIN_CELL or right[len(right) // 2] < MIN_CELL:
        return 0
    filled = sum(1 for ln in lines if ln.strip())
    return n if n >= MIN_TWO_SIDED and n >= 0.3 * filled else 0


def columns(lines, depth=0):
    """[lines] in reading order: a run of lines printed in two columns becomes the left column's lines, then the
    right's. The best gutter on the page is split first; the lines above and below it are looked at again."""
    width = max((len(ln) for ln in lines), default=0)
    if width < 20 or depth > 3 or sum(1 for ln in lines if ln.strip()) < MIN_TWO_SIDED:
        return [lines]
    best = (0, None, None)
    for c in range(max(int(0.15 * width), 1), int(0.85 * width)):
        for a, b in _gutter_runs(lines, c):
            if b - a >= MIN_TWO_SIDED:
                n = _score(lines[a:b], c)
                if n > best[0]:
                    best = (n, c, (a, b))
    if not best[1]:
        return [lines]
    _, c, (a, b) = best
    part = lines[a:b]
    # A half left empty only because the other column has text on that line is no blank line: dropping it keeps
    # a paragraph or list item in one piece.
    left = [ln[:c].rstrip() for ln in part if ln[:c].strip() or not ln.strip()]
    right = [ln[c:] for ln in part if ln[c:].strip() or not ln.strip()]
    out = columns(lines[:a], depth) if a else []
    out += columns(left, depth + 1) + columns(right, depth + 1)
    out += columns(lines[b:], depth) if b < len(lines) else []
    return out


# ---------- paragraphs ----------

def _indent(ln):
    return len(ln) - len(ln.lstrip(" "))


def _row_labels(lines, z_bullets=False):
    """The lines with a table's row labels moved out of the way: in a block of lines (between blank lines) whose
    bullets sit in a second column, the words printed in the first column ("Fuel Supplies", beside "• Increased risk
    of fire or explosion from fuel line" / "rupture") are that row's label. They come out as a line of their own
    before the block, so the items keep their lines whole. Only one label per block: rows printed without a blank
    line between them would put every label before every item, so such a block is left as it is."""
    out, block = [], []

    def flush():
        cols = [len(ln) - len(ln.lstrip()) for ln in block
                if GLYPH.match(ln.lstrip()) or (z_bullets and Z_BULLET.match(ln.lstrip()))]
        g = min(cols) if cols else 0
        labels, rest, at = [], [], []
        for n, ln in enumerate(block):
            m = re.match(r"^(\s*)(\S.*?)\s{3,}(?=\S)", ln) if g > 6 else None
            if m and len(m.group(1)) + len(m.group(2)) < g - 2 and len(ln) > g:
                labels.append(m.group(2))
                rest.append(" " * m.end() + ln[m.end():])
                at.append(n)
            elif g > 6 and ln.strip() and len(ln) - len(ln.lstrip()) < g - 2 and len(ln.strip()) < g:
                labels.append(ln.strip())  # a label line of its own, beside no item text
                at.append(n)
            else:
                rest.append(ln)
        if labels and at != list(range(at[0], at[0] + len(at))):
            labels, rest = [], list(block)  # labels on lines apart: several rows in one block
        if labels:
            out.append(" " * (len(block[0]) - len(block[0].lstrip())) + " ".join(labels))
            out.append("")
        out.extend(rest)
        block.clear()

    for ln in lines:
        if ln.strip():
            block.append(ln)
        else:
            flush()
            out.append(ln)
    flush()
    return out


def paragraphs(lines, vocab=frozenset(), z_bullets=False):
    """[(kind, text)] for a block's lines: kind "li" for a bulleted list item, "p" for anything else."""
    rows = [(_indent(ln), ln.strip()) for ln in _row_labels(lines, z_bullets)]
    width = max((len(t) + i for i, t in rows if t), default=0)
    out = []  # [kind, [lines], text indent, bullet indent]
    prev = None
    for ind, text in rows:
        if not text:
            prev = None
            continue
        if out and out[-1][0] == "li" and not out[-1][1]:  # the item of a bullet printed on a line of its own
            out[-1][1].append(text)
            out[-1][2] = ind
            prev = ind
            continue
        if text in BULLET_GLYPHS or (z_bullets and text == "z"):
            out.append(["li", [], ind, ind])
            prev = ind
            continue
        m = GLYPH.match(text) or (Z_BULLET.match(text) if z_bullets else None)
        if m and text[0] in SOFT_BULLETS and prev is not None and out and out[-1][1]:
            # "– a wrapped line" inside a sentence is no bullet: a dash starts an item only after a finished
            # sentence or a lead-in colon, or at the same indent as the item before it
            if not (SENTENCE_END.search(out[-1][1][-1]) or (out[-1][0] == "li" and out[-1][3] == ind)):
                m = None
        if m and len(text) > m.end():
            out.append(["li", [text[m.end():]], ind + m.end(), ind])
        elif not m and NUMBERED.match(text) or LEADER.search(text) or (out and LEADER.search(out[-1][1][-1])):
            out.append(["p", [text], ind, None])  # a numbered item or paragraph, a contents line
        elif prev is None:
            if out and out[-1][0] == "li" and out[-1][2] > 0 and not LEADER.search(text) and \
                    (abs(ind - out[-1][2]) <= 1 or (ind >= out[-1][2] - 1 and text[:1].islower())):
                out[-1][1].append(text)  # an item's lines on after a blank line, at the item's own indent
            else:
                out.append(["p", [text], ind, None])
        else:
            kind, body, item_indent, _ = out[-1]
            last = body[-1]
            if kind == "li":
                same = ind >= item_indent - 1
            else:
                short = len(last) + prev < 0.6 * width and not re.search(r"[,\-–]$", last) and text[:1].isupper()
                ended = SENTENCE_END.search(last) and (len(last) + prev < 0.75 * width) and text[:1].isupper()
                same = abs(ind - prev) <= 1 and not short and not ended or \
                    (ind < prev and abs(ind - prev) > 1 and len(body) >= 1 and NUMBERED.match(body[0]) and
                     ind >= item_indent - 1 and not ended) or \
                    (ind > prev and not SENTENCE_END.search(last) and text[:1].islower())
            if same:
                body.append(text)
            else:
                out.append(["p", [text], ind, None])
        prev = ind
    return [(k, _join_lines(b, vocab)) for k, b, _, _ in out if b]


def _join_lines(lines, vocab):
    text = lines[0]
    for ln in lines[1:]:
        text = _join(text, ln, vocab)
    return " ".join(text.split())


def _join(text, ln, vocab):
    """text and the line after it as one: a word split at the line end ("far-" / "reaching", "figure 3-" / "2)")
    joins back, keeping its hyphen unless the document spells the word without one and never with one."""
    m = re.search(r"([A-Za-z]+)-$", text)
    n = re.match(r"([a-z]+)", ln)
    if m and n and not text.endswith("--"):
        whole = (m.group(1) + n.group(1)).lower()
        split = f"{m.group(1)}-{n.group(1)}".lower()
        return text[:-1] + ln if whole in vocab and split not in vocab else text + ln
    if re.search(r"[A-Za-z0-9]-$", text) and not text.endswith("--") and re.match(r"[A-Za-z0-9]", ln):
        return text + ln
    return text + " " + ln


def _vocab(layout):
    words = collections.Counter(w.lower() for w in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", " ".join(layout))
                                if not w.endswith("-"))
    return frozenset(words)


# ---------- the whole document ----------

TOKEN = re.compile(r"[\W_]+")


def tokens(text):
    """A text's words for comparing conversions: lowercase letters and digits, punctuation dropped ("far-reaching"
    and "farreaching" are one word)."""
    return [t for t in (TOKEN.sub("", w.lower()) for w in text.split()) if t]


def out_of_order(new, old):
    """How many of new's words are not in a run of at least RUN_MIN words that follows old's order. Bullet glyphs
    read as letters ("z", "o") are left out of both: list repair moves them."""
    old = [w for w in old if w not in ("z", "o")]
    new = [w for w in new if w not in ("z", "o")]
    at = collections.defaultdict(list)
    for i, pair in enumerate(zip(old, old[1:])):
        at[pair].append(i)
    runs, run, last = [], 0, None
    for pair in zip(new, new[1:]):
        hits = at.get(pair, ())
        if last is not None and last + 1 in hits:
            run, last = run + 1, last + 1
        else:
            runs.append(run)
            run, last = (1, hits[0]) if hits else (0, None)
    runs.append(run)
    return max(len(new) - sum(r + 1 for r in runs if r + 1 >= RUN_MIN), 0)


def reordered(new, old):
    """True when new reads in another order than old beyond moved blocks (see ORDER_SHARE)."""
    out = out_of_order(new, old)
    return out > ORDER_SLACK and out > ORDER_SHARE * len(new)


def _lost(new, old, removed, items):
    """Letters and digits of old that are neither in new nor in the removed furniture, less the bullet glyphs read as
    letters ("z", "o") that became the "- " of a list item."""
    lost = collections.Counter(c for c in old.lower() if c.isalnum())
    lost -= collections.Counter(c for c in new.lower() if c.isalnum())
    lost -= collections.Counter(c for c in removed.lower() if c.isalnum())
    for g in "zo":
        lost[g] -= min(lost[g], items)
    return +lost


def _sides(page, cells):
    """[(text, "top" or "bottom")] for a page's furniture cells: the edge each was found at."""
    n = len(page.split("\n"))
    return [(text, "top" if i < n / 2 else "bottom") for (i, _, _), text in sorted(cells.items())]


def _old_page(text, removed):
    """pdftotext's reading-order text of a page as the old converter gave it, one paragraph per block, less the
    furniture lines the layout found. removed: [(text, side)]. Each comes off only as a whole line among the first
    (top) or last (bottom) EDGE_LINES lines of the page, never from the middle, where the same words may be a
    table's value. Returns (blocks, [(line index, text)] taken off)."""
    lines = text.split("\n")
    gone = []
    for want, side in removed:
        target = " ".join(want.split())
        filled = [i for i, ln in enumerate(lines) if ln.strip()]
        for i in (filled[:EDGE_LINES] if side == "top" else filled[::-1][:EDGE_LINES]):
            if " ".join(lines[i].split()) == target:
                lines[i] = ""
                gone.append((i, want))
                break
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", "\n".join(lines))]
    return [("p", p) for p in paras if p], gone


def _taken(cells, gone):
    """The furniture cells whose text a fallback page had taken off, each matched once."""
    left = collections.Counter(t for _, t in gone)
    out = {}
    for k, v in sorted(cells.items()):
        if left[v] > 0:
            out[k] = v
            left[v] -= 1
    return out


def _log_time(path, what):
    print(f"    {os.path.basename(path)}: page analysis past {PY_BUDGET} s; {what} keep pdftotext's reading-order "
          "text", flush=True)


def pages(path):
    """[(page blocks [(kind, text)], {(line, start, end): removed text}, how the page was read)] for every page of
    the PDF at path. How: "layout" (this module's reading), or "fallback" (pdftotext's reading-order text, less the
    page's furniture lines, where the layout reading would lose a character or change the order of the page's words:
    nothing is worse than the old converter), or "fallback (time)" past PY_BUDGET."""
    layout = _run(["-layout", path, "-"]).split("\f")
    if layout and not layout[-1].strip():
        layout = layout[:-1]
    reading = _run([path, "-"]).split("\f")
    reading += [""] * (len(layout) - len(reading))
    boxes = _bbox(path)
    deadline = time.monotonic() + PY_BUDGET  # the page analysis alone; each pdftotext run has its own TIMEOUT
    timed_out = False
    try:
        furniture = find_furniture(layout, boxes, deadline)
    except OutOfTime:
        _log_time(path, "all pages")
        return [(_old_page(reading[p], ())[0], {}, "fallback (time)") for p in range(len(layout))]
    vocab = _vocab(layout)
    z_bullets = sum(1 for page in layout for ln in page.split("\n") if Z_BULLET.match(ln.strip())) >= Z_BULLETS
    out = []
    for p, page in enumerate(layout):
        cells = furniture.get(p, {})
        old_blocks, old_gone = _old_page(reading[p], _sides(page, cells))
        kept_cells = _taken(cells, old_gone)
        if time.monotonic() > deadline:
            if not timed_out:
                _log_time(path, f"pages {p + 1}-{len(layout)}")
                timed_out = True
            out.append((old_blocks, kept_cells, "fallback (time)"))
            continue
        blocks = []
        for col in columns(_strip_furniture(page, cells)):
            blocks.extend(paragraphs(col, vocab, z_bullets))
        new = " ".join(x for _, x in blocks)
        old = " ".join(x for _, x in old_blocks)
        items = sum(k == "li" for k, _ in blocks)
        new_words, old_words = tokens(new), tokens(old)
        bad = _lost(new, reading[p], " ".join(cells.values()), items) or reordered(new_words, old_words)
        if bad:
            out.append((old_blocks, kept_cells, "fallback"))
        else:
            out.append((blocks, cells, "layout"))
    return out


def join_pages(page_blocks, vocab=frozenset()):
    """The document's text from its pages' blocks: a paragraph or list item a page break (or a figure) cut in two
    is put back together; consecutive list items share one paragraph, one "- item" per line."""
    flat = []
    for blocks in page_blocks:
        for kind, text in blocks:
            if flat and text[:1].islower() and not SENTENCE_END.search(flat[-1][1]) \
                    and not re.search(r"[.!?]\W*$", flat[-1][1]):
                flat[-1] = (flat[-1][0], " ".join(_join(flat[-1][1], text, vocab).split()))
            else:
                flat.append((kind, text))
    paras = []
    for kind, text in flat:
        if kind == "li" and paras and paras[-1].startswith("- "):
            paras[-1] += "\n- " + text
        else:
            paras.append(("- " if kind == "li" else "") + text)
    return "\n\n".join(paras)


def convert_path(path):
    """The text of the PDF at path ("" for a scan without a text layer)."""
    done = pages(path)
    return join_pages([b for b, _, _ in done], _vocab([x for b, _, _ in done for _, x in b]))

