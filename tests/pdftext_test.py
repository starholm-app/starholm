"""The page-aware PDF converter (library/pdftext.py): page furniture removed from the page edges, bullets kept with
their items, two columns read one after the other. The pages are real snippets from the manuals (FEMA's CERT
Participant Manual and "Are You Ready?", Army ATP 3-50.21, the Emergency Response Guidebook, a CDC report), as
pdftotext -layout prints them, with word boxes made up from where each line sits. No pdftotext needed.

Usage: .venv/bin/python tests/pdftext_test.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import pdftext  # noqa: E402

FAILS = []
COUNT = [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


HEIGHT = 792.0


def boxes_for(pages, sizes=None):
    """Word boxes for layout pages: line i of a page sits at a height spread over the page (the first line near the
    top, the last near the bottom), 10 points tall unless sizes {(page, line): size} says otherwise."""
    out = []
    for p, page in enumerate(pages):
        lines = page.split("\n")
        words = []
        for i, ln in enumerate(lines):
            y = 30 + i * (730 / max(len(lines) - 1, 1))
            size = (sizes or {}).get((p, i), 10.0)
            words += [(w, y, y + size) for w in ln.split()]
        out.append((HEIGHT, words))
    return out


def furniture(pages, sizes=None):
    found = pdftext.find_furniture(pages, boxes_for(pages, sizes))
    return {p: sorted(found.get(p, {}).values()) for p in range(len(pages))}


WORDS = ["shelter", "water", "radio", "blanket", "candle", "map", "whistle", "rope", "knife", "stove", "tarp",
         "bucket", "gloves", "mask", "boots", "matches", "compass", "lamp", "tent", "kettle"]


def body(n):
    """Eight lines of text unique to page n (no numbers: a page's own text does not count with the page)."""
    return "\n".join(f"   The {WORDS[n % 20]} and the {WORDS[(n * 3 + i) % 20]} line {chr(97 + i)} of this page, "
                     f"ordinary text {WORDS[(n + i) % 20]} that changes." for i in range(8))


# ---------- page furniture ----------

# CERT Basic Training Participant Manual: a header and a footer on every page, the date and the page label.
cert = []
for n in range(7, 13):
    cert.append("CERT Unit 1: Disaster Preparedness" + " " * 49 + "Participant Manual\n\n" + body(n) +
                "\n\nAugust 2019" + " " * 81 + f"Page 1-{n}")
cert[2] = cert[2].replace("line d of this page", "line d of August 2019")  # a date inside the text stays
got = furniture(cert)
check("CERT: the footer (with its page number) goes from every page, its date's first copy stays; the header of "
      "two cells without a page number stays",
      got[0] == ["Page 1-7"] and all(got[p] == ["August 2019", f"Page 1-{p + 7}"] for p in range(1, 6)), got)
found = pdftext.find_furniture(cert, boxes_for(cert))
text = pdftext.join_pages([pdftext.paragraphs(pdftext._strip_furniture(pg, found.get(p, {})))
                           for p, pg in enumerate(cert)])
check("CERT: the body stays, a date inside the text too",
      "The rope and the water line a" in text and "line d of August 2019" in text
      and text.count("Participant Manual") == 6 and "Page 1-" not in text, text[:300])

# "Are You Ready?": the page number and the site name, at the top, left on even pages and right on odd ones.
ready = []
for n in range(13, 19):
    head = f"{n} Ready.gov" if n % 2 == 0 else " " * 76 + f"{n} Ready.gov"
    ready.append(head + "\n" + body(n))
got = furniture(ready)
check("Ready.gov: '13 Ready.gov' goes, left or right", all(got[p] == [f"{p + 13} Ready.gov"] for p in range(6)), got)

# Army ATP 3-50.21: "Chapter 3" over even pages, the chapter's title over odd ones, and a footer with the page,
# the publication and the date. The chapter's opening page prints "Chapter 3" and "Water" larger and lower.
army = []
for n in range(2, 10):
    head = "Chapter 3" if n % 2 == 0 else " " * 100 + "Water"
    foot = (f"3-{n}" + " " * 40 + "ATP 3-50.21" + " " * 40 + "18 September 2018") if n % 2 == 0 else \
        ("18 September 2018" + " " * 40 + "ATP 3-50.21" + " " * 40 + f"3-{n}")
    army.append(head + "\n\n" + body(n) + "\n\n" + foot)
army.insert(0, "\n\nChapter 3\nWater\n\n" + body(0) + "\n\n" + "18 September 2018" + " " * 40 + "ATP 3-50.21" +
            " " * 40 + "3-1")
sizes = {(0, 2): 20.0, (0, 3): 20.0}
got = furniture(army, sizes)
check("Army: running header, page label, publication and date go (their first copy stays)",
      got[0] == ["3-1"] and all("ATP 3-50.21" in got[p] and "18 September 2018" in got[p] for p in range(1, 9))
      and all(("Chapter 3" if (n + 1) % 2 == 0 else "Water") in got[n] for n in range(1, 9)), got)
check("Army: the chapter's own title on its opening page stays",
      "Chapter 3" not in got[0] and "Water" not in got[0], got[0])

# A table's last row sits at the foot of every page of a long table, but its numbers change any old way.
cdc = []
for n, (a, b) in enumerate([("3,472", "26.2"), ("12,904", "4.1"), ("881", "17.0"), ("5,310", "2.9"),
                            ("2,007", "33.5"), ("640", "8.8")]):
    cdc.append(body(n) + "\n" + "All other causes (residual) . . . . . . . . . ." + " " * 10 + a + " " * 8 + b)
got = furniture(cdc)
check("CDC: a table's last row is not taken for a footer", all(got[p] == [] for p in range(6)), got)

# A box heading a manual prints now and then at the top of a page ("WARNING") is text, not furniture.
warn = [body(n) for n in range(40)]
for n in (2, 11, 20, 29):
    warn[n] = " " * 40 + "WARNING\n" + warn[n]
got = furniture(warn)
check("A heading repeated far apart ('WARNING') stays", all(got[p] == [] for p in range(40)), got)

# The Emergency Response Guidebook prints "GUIDE" over the guide's number and its title over "(Extreme Hazard)":
# the repeated half belongs to the page's own title block.
erg = []
for n, (guide, what) in enumerate([(117, "(Extreme Hazard)"), (117, "(Extreme Hazard)"), (118, "(Corrosive)"),
                                   (118, "(Corrosive)"), (119, "(Flammable)"), (119, "(Flammable)")]):
    erg.append("  GUIDE" + " " * 13 + "Gases - Toxic - Flammable\n" + f"  {guide}" + " " * 15 + what + "\n"
               + body(n) + "\n" + f"Page {164 + n}" + " " * 40 + "ERG 2024")
got = furniture(erg)
check("ERG: a guide's title block stays, its footer goes",
      got[0] == ["Page 164"] and all(got[p] == sorted([f"Page {164 + p}", "ERG 2024"]) for p in range(1, 6)), got)

# Its right-hand pages: the title, then "GUIDE" right-aligned over the guide's number.
erg_r = []
for n, (guide, what) in enumerate([(153, "(Combustible)"), (153, "(Combustible)"), (154, "(Non-Combustible)"),
                                   (154, "(Non-Combustible)"), (155, "(Flammable)"), (155, "(Flammable)")]):
    erg_r.append(" " * 22 + "Substances - Toxic and/or Corrosive" + " " * 34 + "GUIDE\n" + " " * 45 + what.ljust(48)
                 + f"{guide}\n" + body(n) + "\n" + " " * 44 + "ERG 2024" + " " * 46 + f"Page {237 + 2 * n}")
got = furniture(erg_r)
check("ERG right-hand pages: the guide's title and 'GUIDE' stay over its number",
      all(not any(t in got[p] for t in ("GUIDE", "Substances - Toxic and/or Corrosive")) for p in range(6)), got)

# A page number printed in another size on one page still goes when it counts with its neighbours, alone on its line
# at the page edge; a number elsewhere on the page that happens to equal it stays.
lab = []
for n in range(1, 8):
    lab.append(body(n) + "\n\n" + " " * 45 + f"{n}")
got = furniture(lab, sizes={(3, 9): 14.0})
check("A bare page number in another size goes when it counts with its neighbours",
      all(got[p] == [str(p + 1)] for p in range(7)), got)
mid = list(lab)
mid[3] = body(3) + "\n" + " " * 45 + "4\n" + body(13) + "\n\n\n\n\n\n\n\n"
got = furniture(mid)
check("A number equal to the page number away from the edge stays", got[3] == [], got[3])

# A count at the foot of a table page rises from page to page without being a page number.
cases = []
for n, c in enumerate([3, 9, 14, 26, 41, 52, 70, 88]):
    cases.append(body(n) + "\n" + f"Cumulative cases reported {c}")
got = furniture(cases)
check("A rising number that does not count with the page stays ('Cumulative cases reported 70')",
      all(got[p] == [] for p in range(8)), got)

# A table's header row repeated over its continuation pages, with its rows under it.
table = []
for n in range(6):
    table.append("Disease" + " " * 20 + "Carrier" + " " * 20 + "Region\n" + f"{WORDS[n]} fever" + " " * 16 +
                 f"{WORDS[n + 1]}s" + " " * 20 + f"{WORDS[n + 2]} lands\n" + body(n))
got = furniture(table)
check("A table's header row over its rows stays on every page", all(got[p] == [] for p in range(6)), got)

# A centred table header over continuation pages, the page's outermost line, with no page number: it stays.
centred = []
for n in range(6):
    centred.append("      Disease" + " " * 9 + "Carrier" + " " * 9 + "Transmission" + " " * 9 + "Region\n"
                   + body(n) + "\n" + " " * 40 + f"{n + 95}")
got = furniture(centred)
check("A repeated multi-cell line without a page number stays, even as the outermost line",
      all("Disease" not in got[p] and got[p] == [str(p + 95)] for p in range(6)), got)

# The page analysis has a time limit.
try:
    pdftext.find_furniture(cert, boxes_for(cert), deadline=0)
    timed = False
except pdftext.OutOfTime:
    timed = True
check("Past its time limit the furniture search stops", timed)

# ---------- columns and bullets ----------

# "Are You Ready?" page 15: two columns, each with a bulleted list; the right column's items hang under bullets.
KIT = """•   If you do not have a car, plan your           Your basic emergency supply kit should
    transportation in case of an evacuation.      include the following:
    Make plans with family, friends, or your
    local government.                             • Water. Keep one gallon of drinking water,
•   Take your emergency supply kit with you         per person, per day in your kit. Depending
    during an evacuation.                           on where you live, you may wish to keep 3,
•   Pay attention to your radio or                  7, or even 14 days of water on hand.
    smartphone/tablet and follow local            • Food. Keep as much non-perishable food,
    evacuation instructions.                        like cans and dry goods, as your family will
                                                    need to survive a major disaster.
If you have enough time before                    • Flashlight and extra batteries.
evacuating:                                       • First aid kit.
                                                  • Whistle."""
blocks = [b for col in pdftext.columns(KIT.split("\n")) for b in pdftext.paragraphs(col)]
items = [t for k, t in blocks if k == "li"]
check("Kit list: the left column is read before the right one",
      [t for _, t in blocks].index("If you have enough time before evacuating:") <
      [t for _, t in blocks].index("Your basic emergency supply kit should include the following:"), blocks)
check("Kit list: each bullet keeps its whole item",
      "Water. Keep one gallon of drinking water, per person, per day in your kit. Depending on where you live, you "
      "may wish to keep 3, 7, or even 14 days of water on hand." in items
      and "If you do not have a car, plan your transportation in case of an evacuation. Make plans with family, "
          "friends, or your local government." in items and "Whistle." in items and len(items) == 8, items)
text = pdftext.join_pages([blocks])
check("Kit list: items are Markdown list lines", "- Flashlight and extra batteries.\n- First aid kit.\n- Whistle."
      in text, text)

# CERT page 1-8: bullets indented under a paragraph, an item running on to a second line.
CERT_LIST = """   treat them first. In the same way, during or after a disaster, emergency services
   personnel must prioritize resources according to the highest-priority need.
       • Police will address incidents of grave public safety.
       • EMS personnel will handle life-threatening injuries. You should be aware,
           however, that CERTs may also handle life-threatening injuries until EMS units
           become available.
       • Lower-priority needs will be met in other ways.
   Keep in mind that age, type of construction, and type of disaster are major factors."""
blocks = pdftext.paragraphs(CERT_LIST.split("\n"))
check("CERT: a list between two paragraphs keeps its items and the text around it",
      [k for k, _ in blocks] == ["p", "li", "li", "li", "p"]
      and blocks[2][1] == "EMS personnel will handle life-threatening injuries. You should be aware, however, that "
                          "CERTs may also handle life-threatening injuries until EMS units become available.", blocks)

# CERT Table 1.1: row labels in the first column, the row's bulleted impacts in the second.
TABLE = """                                   •   Roads are closed and/or impassable
                                   •   Responders may be delayed in reaching areas of
   Transportation                      need

                                   •   Increased risk of fire or explosion from fuel line
   Fuel Supplies                       rupture
                                   •   Flow of fuel is interrupted by impassable roads

                                   •   Survivors unable to phone for help or reach service
                                       providers
   Communications Systems
                                   •   Coordination of services is hampered"""
blocks = pdftext.paragraphs(TABLE.split("\n"))
check("CERT table: each row's label comes before its items, and the items stay whole", blocks == [
    ("p", "Transportation"), ("li", "Roads are closed and/or impassable"),
    ("li", "Responders may be delayed in reaching areas of need"),
    ("p", "Fuel Supplies"), ("li", "Increased risk of fire or explosion from fuel line rupture"),
    ("li", "Flow of fuel is interrupted by impassable roads"),
    ("p", "Communications Systems"), ("li", "Survivors unable to phone for help or reach service providers"),
    ("li", "Coordination of services is hampered")], blocks)

# Rows printed without blank lines between them: the labels are left where they are, not gathered before the items.
TIGHT = """   Transportation                  •   Roads are closed and/or impassable
                                   •   Responders may be delayed
   Fuel Supplies                   •   Flow of fuel is interrupted by impassable roads"""
blocks = pdftext.paragraphs(TIGHT.split("\n"))
check("Rows without blank lines: no label is moved", not any(t == "Transportation Fuel Supplies" for _, t in blocks),
      blocks)

# A dash starting a wrapped line inside a sentence is no bullet.
DASH = """   The kit holds water, food and a radio, and for each person in the household
   – a blanket and a change of clothes. Store it in a cool place.
   You will need:
   – a whistle
   – a flashlight"""
blocks = pdftext.paragraphs(DASH.split("\n"))
check("A dash inside a sentence stays text; after a lead-in colon it starts items",
      "for each person in the household – a blanket and a change of clothes." in blocks[0][1]
      and [b for b in blocks if b[0] == "li"] == [("li", "a whistle"), ("li", "a flashlight")], blocks)

# Army manuals set bullets in ZapfDingbats, which pdftotext reads as "z".
Z = """            z   Drive the striker downward with evenly applied pressure through the strike to near the bottom of
                the stick.
            z   Terminate the downward strike prior to the striker disturbing the tinder."""
blocks = pdftext.paragraphs(Z.split("\n"), z_bullets=True)
check("Army: a 'z' bullet is a list item", blocks == [
    ("li", "Drive the striker downward with evenly applied pressure through the strike to near the bottom of the "
           "stick."), ("li", "Terminate the downward strike prior to the striker disturbing the tinder.")], blocks)
check("A 'z' is a bullet only in a document that uses it as one",
      pdftext.paragraphs(Z.split("\n"))[0][1].startswith("z "), pdftext.paragraphs(Z.split("\n")))

# ---------- words and pages ----------

check("A word split at a line end joins with its hyphen",
      pdftext._join_lines(["essential services with far-", "reaching consequences"], frozenset()) ==
      "essential services with far-reaching consequences")
check("A word the document spells whole joins without the hyphen",
      pdftext._join_lines(["older buildings are more suscep-", "tible to damage"], frozenset({"susceptible"})) ==
      "older buildings are more susceptible to damage")
check("'figure 3-' and '2).' join", pdftext._join_lines(["(see figure 3-", "2)."], frozenset()) == "(see figure 3-2).")
check("A sentence a page break cut is put back together",
      pdftext.join_pages([[("p", "Each instance of damage to infrastructure may severely restrict")],
                          [("p", "the abilities of police, fire, and emergency medical services.")]]) ==
      "Each instance of damage to infrastructure may severely restrict the abilities of police, fire, and emergency "
      "medical services.")
check("A finished paragraph is not joined to the next page's",
      pdftext.join_pages([[("p", "Lower-priority needs will be met in other ways.")], [("p", "Damage Related")]]) ==
      "Lower-priority needs will be met in other ways.\n\nDamage Related")
prose = [f"w{i}" for i in range(60)]
moved = prose[30:] + prose[:30]  # the right column read after the left one
cells = [f"w{i}" for j in range(3) for i in range(j, 60, 3)]  # a table read down its columns
check("The order check: a moved block of text passes, words read across a table's cells do not",
      pdftext.out_of_order(moved, prose) == 0 and not pdftext.reordered(moved, prose)
      and pdftext.reordered(cells, prose), (pdftext.out_of_order(moved, prose), pdftext.out_of_order(cells, prose)))
check("A page that would lose a letter is caught; bullet glyphs read as letters are not losses",
      pdftext._lost("water and fire", "water and fire 12", "12", 0) == {}
      and pdftext._lost("water fire", "water and fire", "", 0) != {}
      and pdftext._lost("- Forgetfulness.", "z Forgetfulness.", "", 1) == {})
blocks, gone = pdftext._old_page("Participant Manual\nWater is life.\n\nPage 1-7",
                                 [("Page 1-7", "bottom"), ("Not here", "top")])
check("The fallback text loses the page's furniture lines only where they are whole lines",
      blocks == [("p", "Participant Manual Water is life.")] and gone == [(3, "Page 1-7")], (blocks, gone))
# CDC report page 13 on the old text: a table value "13" in the middle, the page number "13" at the foot.
TABLE_PAGE = "\n".join(["Table 2. Deaths by cause", "Rank", "Cause", "Deaths", "1", "Heart disease", "702,880",
                        "13", "Influenza and pneumonia", "41,917", "Septicemia", "40,050", "See footnotes.", "13"])
blocks, gone = pdftext._old_page(TABLE_PAGE, [("13", "bottom")])
check("The fallback text takes a furniture line off the page edge only, never a table value in the middle",
      gone == [(13, "13")] and " 13 Influenza" in blocks[0][1], (blocks, gone))
check("Contents lines stay one per paragraph",
      [t for _, t in pdftext.paragraphs(["Food ........................ 4-1", "Water ....................... 3-1"])]
      == ["Food ........................ 4-1", "Water ....................... 3-1"])

print(f"{COUNT[0] - len(FAILS)}/{COUNT[0]} passed")
sys.exit(1 if FAILS else 0)
