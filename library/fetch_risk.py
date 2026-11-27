"""Gather references for estimating risks of death (micromorts) into data.noindex/raw/risk/.

Usage: .venv/bin/python library/fetch_risk.py
Writes the files plus manifest.json for build_library.py: U.S. government mortality statistics (public domain),
downloaded, and Wikipedia's articles on micromorts and risk, copied from the local, verified Wikipedia ZIM. Builds
that include Wikipedia skip those articles, so only a library without Wikipedia uses its own copies.
Sources checked on 2026-10-24.

Every downloaded table is checked against the hash lock, the same way as the manuals (see fetch_manuals.py): a
changed file waits in raw/.pending/ for review, and an Internet Archive copy counts only if it matches the lock.
"""
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import safe_fetch  # noqa: E402
from library.fetch_manuals import write_json_if_changed  # noqa: E402

US_GOV = "U.S. government work, public domain"
UA, FALLBACK_UA = safe_fetch.HONEST_UA, safe_fetch.SAFARI_UA  # the app's own name, then Safari if refused
WAYBACK = "https://web.archive.org/web/2026id_/"
MAX_BYTES = 128 << 20

DOCUMENTS = [  # (file, title, url)
    ("ssa-life-table.html", "Actuarial Life Table: chance of dying within a year at each age (Social Security, 2023)",
     "https://www.ssa.gov/oact/STATS/table4c6.html"),
    ("cdc-mortality-2024.pdf", "Mortality in the United States, 2024 (CDC NCHS Data Brief 548)",
     "https://www.cdc.gov/nchs/data/databriefs/db548.pdf"),
    ("cdc-leading-causes-2024.pdf", "Deaths: Leading Causes for 2024, death rates by cause and age (CDC NCHS)",
     "https://www.cdc.gov/nchs/data/nvsr/nvsr75/nvsr75-07.pdf"),
    ("nhtsa-crashes-2024.pdf", "Overview of Motor Vehicle Traffic Crashes in 2024 (NHTSA), deaths per mile driven",
     "https://crashstats.nhtsa.dot.gov/Api/Public/ViewPublication/813791"),
    ("nws-lightning-odds.html", "How Dangerous is Lightning? Odds of being struck (National Weather Service)",
     "https://www.weather.gov/safety/lightning-odds"),
]
WIKIPEDIA = ["Micromort", "Fermi problem", "Life table", "Relative risk", "Life expectancy", "Aviation safety",
             "Motor vehicle fatality rate in U.S. by year", "Expected value", "Order of magnitude",
             "Gompertz–Makeham law of mortality", "Risk", "Bayes' theorem", "Confidence interval"]


ALLOWED_HOSTS = safe_fetch.hosts_of([url for _, _, url in DOCUMENTS] + [WAYBACK])


def unwrap(path):
    """Undo the archive's gzip and NHTSA's form-data wrapper around its PDFs, in place, before hashing.
    CDC and SSA refuse scripted downloads (HTTP 403, checked 2026-10-24), so these often come from the archive."""
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if data[:2] == b"\x1f\x8b":
        with gzip.open(path, "rb") as g:
            data = g.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            data = b""  # a decompression bomb: fails the check below
    if data.startswith(b"------") and b"%PDF" in data and b"%%EOF" in data:
        data = data[data.index(b"%PDF"):data.rindex(b"%%EOF") + 5]
    with open(path, "wb") as f:
        f.write(data)


def page_text_key(path):
    """The SHA-256 of a web page's text as the build extracts it. Government pages reorder their navigation from
    fetch to fetch (the NWS lightning page swaps two menu links), so the raw bytes change while the content does
    not; comparing this key keeps such a page from being held for review every week."""
    from library.build_library import html_to_text
    with open(path, "rb") as f:
        return safe_fetch.sha256_bytes((html_to_text(f.read(), "web") or "").encode("utf-8"))


def looks_right(name):
    """A check that the download is the kind of file its name says, not an error page."""
    def check(path):
        if name.endswith(".pdf"):
            return safe_fetch.is_pdf(path)
        with open(path, "rb") as f:
            head = f.read(4096).lower()
        return b"<html" in head or b"<!doctype html" in head
    return check


def life_table_text(html):
    """The SSA life table as plain lines, with each yearly chance of dying also given in micromorts
    (one-in-a-million chances of death) per year and per day."""
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>\s*(\d+)</td>\s*<td>\s*([\d.]+)</td>\s*<td>[^<]*</td>\s*<td>\s*([\d.]+)</td>"
                      r"\s*<td>\s*([\d.]+)</td>\s*<td>[^<]*</td>\s*<td>\s*([\d.]+)</td>", html)
    lines = ["How likely an American is to die within the next year at each age, from the Social Security period "
             "life table for 2023 (2026 Trustees Report). A micromort is a one-in-a-million chance of death; "
             "micromorts per year = yearly chance x 1,000,000, and per day = per year / 365. Life expectancy is the "
             "average number of years left at that age.", ""]
    for age, pm, em, pf, ef in rows:
        m, f = float(pm), float(pf)
        lines.append(f"Age {age}: men {m:.6f} a year ({m * 1e6:,.0f} micromorts a year, {m * 1e6 / 365:,.1f} a day; "
                     f"life expectancy {em} years); women {f:.6f} a year ({f * 1e6:,.0f} micromorts a year, "
                     f"{f * 1e6 / 365:,.1f} a day; life expectancy {ef} years)")
    return "\n\n".join(lines) if rows else ""


def previous_wikipedia(out):
    """The Wikipedia entries of the risk folder's current manifest (articles extracted from an earlier ZIM)."""
    try:
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return []
    return [e for e in items if isinstance(e, dict) and e.get("wikipedia") and isinstance(e.get("file"), str)
            and os.path.basename(e["file"]) == e["file"] and e["file"].startswith("wikipedia-")]


def main(raw_dir=None, lock_path=None, zim_dir=None):
    """Fetch the risk tables against the hash lock and copy the Wikipedia articles from the verified ZIM.
    Returns {"pending": [...], "failed": [...]} like fetch_manuals.main()."""
    raw_dir = raw_dir or safe_fetch.RAW_DIR
    out = os.path.join(raw_dir, "risk")
    os.makedirs(out, exist_ok=True)
    lock = safe_fetch.load_lock(lock_path)
    manifest = []
    result = {"pending": [], "failed": []}
    for name, title, url in DOCUMENTS:
        rel = f"risk/{name}"
        outcome = safe_fetch.fetch_locked(lock, rel, [url, WAYBACK + url], ALLOWED_HOSTS, user_agent=UA,
                                          fallback_user_agent=FALLBACK_UA,
                                          max_bytes=MAX_BYTES, check=looks_right(name), normalize=unwrap,
                                          raw_dir=raw_dir,
                                          content_key=page_text_key if name.endswith((".html", ".htm")) else None)
        safe_fetch.save_lock(lock, lock_path)
        print(f"{outcome:<13} {title}", flush=True)
        if outcome == "pending":
            result["pending"].append(rel)
        elif outcome == "failed":
            result["failed"].append(title)
        if not safe_fetch.locked_ok(lock, rel, raw_dir):
            continue
        dest = os.path.join(out, name)
        entry = {"file": name, "title": title, "url": url, "license": US_GOV, "kind": "web"}
        if name == "ssa-life-table.html":  # a 120-row table reads far better for a model as lines with micromorts
            with open(dest, encoding="utf-8", errors="replace") as f:
                text = life_table_text(f.read())
            if not text:
                print(f"FAILED  {title} (table not found)", flush=True)
                result["failed"].append(title)
                continue
            with open(os.path.join(out, "ssa-life-table.md"), "w", encoding="utf-8") as f:
                f.write(text)
            entry.update(file="ssa-life-table.md", kind="text")
        manifest.append(entry)

    from library.fetch_zims import latest_verified
    zim = latest_verified("wikipedia_en_top1m_nopic", zim_dir)
    if not zim:
        # The Wikipedia ZIM is deleted once the live build holds it (build_library.retire_originals()). Keep the
        # articles extracted from it earlier, so the risk source does not change; they are taken again from the
        # next Wikipedia ZIM that is downloaded.
        kept = [e for e in previous_wikipedia(out) if os.path.isfile(os.path.join(out, e["file"]))]
        manifest += kept
        print(f"Wikipedia articles: kept the {len(kept)} extracted earlier (no Wikipedia ZIM on disk)", flush=True)
    else:
        from libzim.reader import Archive
        archive = Archive(zim)
        for title in WIKIPEDIA:
            path = title.replace(" ", "_")
            try:
                entry = archive.get_entry_by_path(path)
                hops = 0
                while entry.is_redirect and hops < 10:
                    entry = entry.get_redirect_entry()
                    hops += 1
                html = bytes(entry.get_item().content)
            except KeyError:
                print(f"missing {title}", flush=True)
                continue
            name = "wikipedia-" + "".join(c if c.isalnum() else "-" for c in title).strip("-").lower() + ".html"
            with open(os.path.join(out, name), "wb") as f:
                f.write(html)
            manifest.append({"file": name, "title": title, "url": "https://en.wikipedia.org/wiki/" + path,
                             "license": "CC BY-SA 4.0, Wikipedia contributors", "kind": "wiki", "wikipedia": True})
            print(f"ok            Wikipedia: {title}", flush=True)
    write_json_if_changed(os.path.join(out, "manifest.json"), manifest)
    print(f"{len(manifest)} documents", flush=True)
    return result


if __name__ == "__main__":
    sys.exit(1 if main()["failed"] else 0)
