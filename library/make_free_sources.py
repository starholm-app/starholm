"""Write library/free-sources.json: the sources the free library downloads from their original hosts, with each
one's size, licence and checksum where the host publishes one. The Library page's source picker reads that file;
nothing in the interface lists sources itself.

Usage: .venv/bin/python library/make_free_sources.py [path/to/originals.json]
Reads data.noindex/originals.json by default (read-only): the ZIM files the live library was built from, with the
size and SHA-256 Kiwix published for each. Kiwix sources are found again through Kiwix's catalog when someone
downloads (the file name, size and checksum here are the last known ones; the download checks the size and
SHA-256 in Kiwix's own .meta4 for the file the catalog names then). The U.S. government manuals and the risk
tables come from their publishers' own addresses, which publish no checksum.

wikiHow comes from the dataset's own page on Hugging Face, the route library/fetch_wikihow.py uses: the reviewed
commit, size and SHA-256 pinned there. The IFRC and WHO guides come from ifrc.org and iris.who.int, as in
fetch_manuals.py, with no published checksum.
"""
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import build_library, fetch_manuals, fetch_risk, fetch_wikihow, fetch_zims, safe_fetch  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "free-sources.json")
LEFT_OUT = ()  # every source the live library has is offered (the owner's decision W136, 16 November 2026)

# Each source's name in the picker and its licence in a few words (the full text is build_library.LICENSES).
NAMES = {
    "wikipedia": ("Wikipedia", "CC BY-SA"),
    "se-diy": ("Home Improvement Q&A (Stack Exchange)", "CC BY-SA"),
    "ready-gov": ("Ready.gov (FEMA)", "Public domain"),
    "medlineplus": ("MedlinePlus (NIH)", "Public domain"),
    "wikimed": ("WikiMed (Wikipedia medicine)", "CC BY-SA"),
    "se-gardening": ("Gardening Q&A (Stack Exchange)", "CC BY-SA"),
    "energypedia": ("Energypedia (solar, batteries, stoves)", "CC BY-SA"),
    "post-disaster": ("Post Disaster Resource Library", "Not stated"),
    "appropedia": ("Appropedia", "CC BY-SA"),
    "se-mechanics": ("Motor Vehicle Repair Q&A (Stack Exchange)", "CC BY-SA"),
    "se-cooking": ("Cooking Q&A (Stack Exchange)", "CC BY-SA"),
    "cdc-travel": ("CDC Travelers' Health", "Public domain"),
    "se-outdoors": ("The Great Outdoors Q&A (Stack Exchange)", "CC BY-SA"),
    "gardenology": ("Gardenology plant encyclopedia", "Not stated"),
    "food-prep": ("Food for Preppers", "Not stated"),
    "se-pets": ("Pets Q&A (Stack Exchange)", "CC BY-SA"),
    "se-ham": ("Amateur Radio Q&A (Stack Exchange)", "CC BY-SA"),
    "medical-library": ("Medical Library", "Not stated"),
    "military-medicine": ("Military medicine manuals", "Public domain"),
    "se-medicalsciences": ("Medical Sciences Q&A (Stack Exchange)", "CC BY-SA"),
    "se-lifehacks": ("Lifehacks Q&A (Stack Exchange)", "CC BY-SA"),
    "knots": ("Library of Knots", "Not stated"),
    "se-sustainability": ("Sustainable Living Q&A (Stack Exchange)", "CC BY-SA"),
    "water": ("Water Treatment Library", "Not stated"),
    "usda-canning": ("USDA Complete Guide to Home Canning", "Public domain"),
    "nhs-medicines": ("UK Medicines A to Z (public sector information)", "Open Government Licence"),
    "wikiciv": ("WikiCiv (rebuilding technology)", "CC BY-SA"),
    "wikem": ("WikEM emergency medicine", "CC BY-SA"),
    "ifixit": ("iFixit repair guides", "CC BY-NC-SA"),
    "wikihow": ("wikiHow", "CC BY-NC-SA"),
    "first-aid-guides": ("IFRC and WHO first-aid and emergency care guides", "Non-commercial"),
    "risk": ("Risk and micromort references", "Public domain"),
    "survival-manuals": ("U.S. government survival and first-aid manuals", "Public domain"),
}
KIWIX_HOSTS = sorted({fetch_zims.ZIM_HOST, fetch_zims.META4_HOST, urllib.parse.urlsplit(fetch_zims.CATALOG_URL).hostname})


def catalog_entry(prefix):
    """(Kiwix catalog name, flavour) of a ZIM file prefix, from fetch_zims.SOURCES."""
    for name, flavour in fetch_zims.SOURCES:
        files = build_library.CATALOG_FILE_PREFIX.get(name, name)
        if (files + ("_" + flavour if flavour else "")) == prefix:
            return name, flavour
    raise SystemExit(f"no catalog entry for {prefix}")


def zim_source(key, record):
    prefix = fetch_zims.file_prefix(record["file"])
    if build_library.ZIM_KEYS.get(key) != prefix:
        raise SystemExit(f"{key}: {record['file']} is not that source's file")
    if not safe_fetch.SHA256_RE.match(record.get("sha256", "")) or not int(record.get("size") or 0) > 0:
        raise SystemExit(f"{key}: no size or checksum in originals.json")
    name, flavour = catalog_entry(prefix)
    title, licence = NAMES[key]
    return {"key": key, "title": title, "from": "Kiwix", "kind": "zim", "licence": licence,
            "licence_text": build_library.LICENSES.get(key, licence),
            "kiwix": {"name": name, "flavour": flavour, "file": record["file"]},
            "size": int(record["size"]), "sha256": record["sha256"], "checksum": "Kiwix .meta4 (SHA-256)",
            "hosts": KIWIX_HOSTS}


def wikihow_source(record):
    """The wikiHow CSV at the reviewed commit fetch_wikihow.py pins (originals.json must agree with the pin)."""
    pin = fetch_wikihow.PIN
    if record.get("sha256") != pin["sha256"] or int(record.get("size") or 0) != pin["size"]:
        raise SystemExit("wikihow: originals.json does not match fetch_wikihow.PIN")
    url = fetch_wikihow.download_url(pin["commit"])
    title, licence = NAMES["wikihow"]
    return {"key": "wikihow", "title": title, "from": "Hugging Face", "kind": "file", "licence": licence,
            "licence_text": build_library.LICENSES["wikihow"], "file": fetch_wikihow.FILE, "url": url,
            "size": pin["size"], "sha256": pin["sha256"], "checksum": "pinned SHA-256 (fetch_wikihow.py)",
            "hosts": sorted(fetch_wikihow.HF_HOSTS)}


FROM = {"survival-manuals": "government sites", "first-aid-guides": "IFRC and WHO"}


def file_source(key, files):
    title, licence = NAMES[key]
    return {"key": key, "title": title, "from": FROM.get(key, "original site"),
            "kind": "files", "licence": licence, "licence_text": build_library.LICENSES.get(key, licence),
            "files": files, "size": None, "sha256": None, "checksum": None,
            "hosts": sorted({safe_fetch.host_of(f["url"]) for f in files})}


def make(originals):
    sources = []
    for key, record in originals.items():
        if key in LEFT_OUT:
            continue
        if key == "wikihow":
            sources.append(wikihow_source(record))
        elif key in build_library.ZIM_KEYS:
            sources.append(zim_source(key, record))
    sources.sort(key=lambda s: (-s["size"], s["key"]))

    def manuals(group):
        return [{"file": f"{n:02d}.pdf", "title": title, "url": url}
                for n, (g, title, url) in enumerate(fetch_manuals.MANUALS) if g == group]
    risk = [{"file": name, "title": title, "url": url} for name, title, url in fetch_risk.DOCUMENTS]
    sources += [file_source(k, f) for k, f in (("risk", risk), ("survival-manuals", manuals("us-gov")),
                                               ("first-aid-guides", manuals("nc"))) if k not in LEFT_OUT]
    return {"about": "Generated by library/make_free_sources.py from data.noindex/originals.json; do not edit by hand. "
                     "The free library's source picker (library/free_library.py). Sizes and checksums of Kiwix files "
                     "are the last known ones; a download checks Kiwix's .meta4 for the file its catalog names then. "
                     "Manuals and risk tables publish no checksum; their sizes are not known until downloaded.",
            "catalog_url": fetch_zims.CATALOG_URL,
            "zim_url": f"https://{fetch_zims.ZIM_HOST}" + "{catalog path}",
            "meta4_url": f"https://{fetch_zims.META4_HOST}" + "{catalog path}.meta4",
            "sources": sources}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else safe_fetch.ORIGINALS
    with open(path, encoding="utf-8") as f:
        originals = json.load(f)["sources"]
    data = make(originals)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    total = sum(s["size"] or 0 for s in data["sources"])
    print(f"{len(data['sources'])} sources, {total / 1e9:.1f} GB of known size -> {OUT}")


if __name__ == "__main__":
    main()
