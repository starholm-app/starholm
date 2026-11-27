"""Download the Kiwix ZIM files the offline library is built from, verified against Kiwix's own checksums.

Usage: .venv/bin/python library/fetch_zims.py [--dry-run] [NAME ...]
Files land in data.noindex/zim/ in the checkout. Give Kiwix names (e.g. usda-2015_en) to fetch only those.
refresh.py runs this as its first step; running it alone only downloads, it never builds.

Every ZIM is checked against the size and SHA-256 in the .meta4 file on Kiwix's download server, fetched with
redirects off (fail closed). Without a checksum nothing is downloaded and nothing new is used; a download that
does not match is deleted. A verified file gets a "<file>.verified" record beside it. An interrupted download is
kept and resumed on a later run, even after a week in which the checksum could not be fetched, but it is never
used until it verifies.

Once a build is live, refresh.py deletes the ZIMs it was converted from (build_library.retire_originals()). A ZIM
the live build already holds, converted from this exact file with today's converter, is "built": nothing is
downloaded for it. It is downloaded again when the catalog offers another file, when its converter version
changes, or when --rebuild names its source.
"""
import json
import os
import re
import shutil
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import safe_fetch  # noqa: E402
from library.safe_fetch import FetchError  # noqa: E402

# (Kiwix name, flavour). Small, high-value sources first so conversion can start early.
SOURCES = [
    ("zimgit-water_en", ""),
    ("zimgit-food-preparation_en", ""),
    ("zimgit-medicine_en", ""),
    ("zimgit-knots_en", ""),
    ("zimgit-post-disaster_en", ""),
    ("irp.fas.org_en_military-medicine", ""),
    ("nhs.uk_en_medicines", ""),
    ("wikem_en_all", "nopic"),
    ("wwwnc.cdc.gov_en_all", ""),
    ("outdoors.stackexchange.com_en_all", ""),
    ("cooking.stackexchange.com_en_all", ""),
    ("lifehacks.stackexchange.com_en_all", ""),
    ("sustainability.stackexchange.com_en_all", ""),
    ("ham.stackexchange.com_en_all", ""),
    ("medicalsciences.stackexchange.com_en_all", ""),
    ("gardening.stackexchange.com_en_all", ""),
    ("diy.stackexchange.com_en_all", ""),
    ("appropedia_en_all", "maxi"),
    ("wikipedia_en_medicine", "nopic"),
    ("medlineplus.gov_en_all", ""),
    ("ready.gov_en_all", ""),
    ("ifixit_en_all", ""),
    ("usda-2015_en", ""),
    ("wikiciv_en_all", "maxi"),
    ("gardenology_en_all", ""),  # catalog name; its files are still named gardenology.org_en_all_*
    ("mechanics.stackexchange.com_en_all", ""),
    ("pets.stackexchange.com_en_all", ""),
    ("energypedia_en_all", "nopic"),
    ("wikipedia_en_top1m", "nopic"),
]

CATALOG_URL = "https://opds.library.kiwix.org/catalog/v2/entries?lang=eng&count=5000"
ZIM_HOST = "download.kiwix.org"        # where a ZIM download starts (it redirects to a mirror, https only)
META4_HOST = "lb.download.kiwix.org"   # where download.kiwix.org's .meta4 files are served without a redirect
FREE_SPACE_MARGIN = 10 << 30           # keep 10 GB free beyond a download
DATE_SUFFIX = r"_\d{4}-\d{2}[a-z]?\.zim$"  # what follows a source's prefix in a Kiwix file name


def file_prefix(filename):
    """The source prefix of a Kiwix file name (wikem_en_all_nopic_2026-10.zim -> wikem_en_all_nopic), or None."""
    m = re.search(DATE_SUFFIX, filename or "")
    return filename[:m.start()] if m else None


def zim_dir():
    return safe_fetch.ZIM_DIR


def parse_catalog(xml_text):
    """{(name, flavour): ZIM file name} for every entry whose download link is a ZIM on Kiwix's download server.
    Links anywhere else, and file names that are not plain "<name>.zim", are left out."""
    found = {}
    for e in re.findall(r"<entry>(.*?)</entry>", xml_text, re.S):
        name = re.search(r"<name>(.*?)</name>", e)
        flavour = re.search(r"<flavour>(.*?)</flavour>", e)
        link = re.search(r'rel="http://opds-spec.org/acquisition/open-access"[^>]*href="([^"]+)"', e) or \
            re.search(r'href="([^"]+\.zim\.meta4)"[^>]*rel="http://opds-spec.org/acquisition/open-access"', e)
        if not (name and link):
            continue
        url = link.group(1)
        try:
            safe_fetch.check_url(url, safe_fetch.KIWIX_DOWNLOAD_HOSTS)
        except FetchError:
            continue
        path = urllib.parse.urlsplit(url).path
        if not path.endswith(".zim.meta4") or not path.startswith("/zim/"):
            continue
        filename = path.rsplit("/", 1)[1].removesuffix(".meta4")
        if not safe_fetch.ZIM_NAME_RE.match(filename) or ".." in path:
            continue
        found[(name.group(1), flavour.group(1) if flavour else "")] = path.removesuffix(".meta4")
    return found


def zim_url(path):
    return f"https://{ZIM_HOST}{path}"


def meta4_url(path):
    return f"https://{META4_HOST}{path}.meta4"


def fetch_catalog():
    data = safe_fetch.fetch_small(CATALOG_URL, safe_fetch.KIWIX_CATALOG_HOSTS, max_bytes=64 << 20, max_time=300)
    return parse_catalog(data.decode("utf-8", "replace"))


def fetch_checksum(path):
    """(size, sha256) of the ZIM at path on the download server, from its .meta4. FetchError if unavailable."""
    data = safe_fetch.fetch_small(meta4_url(path), safe_fetch.KIWIX_DOWNLOAD_HOSTS, max_bytes=2 << 20, follow=False)
    return safe_fetch.parse_meta4(data, path.rsplit("/", 1)[1])


# ---------- verified files ----------

def _record_path(dest):
    return dest + ".verified"


def verified_record(dest):
    """The checksum a file verified against, if its record is there and the file has not changed since."""
    try:
        with open(_record_path(dest), encoding="utf-8") as f:
            rec = json.load(f)
        st = os.stat(dest)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("size") != st.st_size or rec.get("mtime") != int(st.st_mtime):
        return None
    if not safe_fetch.SHA256_RE.match(str(rec.get("sha256", ""))):
        return None
    return rec


def is_verified(dest, size, sha256):
    rec = verified_record(dest)
    return bool(rec) and rec["size"] == size and rec["sha256"] == sha256


def remove_zim(dest):
    for p in (dest, _record_path(dest)):
        if os.path.lexists(p):
            os.remove(p)


def verify_zim(dest, size, sha256):
    """Check a finished download against the size and SHA-256 from the .meta4. A file that does not match is
    deleted. A match gets its .verified record. Returns True only for a match."""
    if size is None or not sha256 or not safe_fetch.SHA256_RE.match(sha256):
        print(f"NO CHECKSUM, deleting {os.path.basename(dest)}", flush=True)
        remove_zim(dest)
        return False
    if not os.path.exists(dest) or os.path.getsize(dest) != size:
        print(f"WRONG SIZE, deleting {os.path.basename(dest)}", flush=True)
        remove_zim(dest)
        return False
    if safe_fetch.sha256_file(dest) != sha256:
        print(f"CHECKSUM MISMATCH, deleting {os.path.basename(dest)}", flush=True)
        remove_zim(dest)
        return False
    st = os.stat(dest)
    with open(_record_path(dest), "w", encoding="utf-8") as f:
        json.dump({"sha256": sha256, "size": size, "mtime": int(st.st_mtime)}, f)
    return True


def latest_verified(prefix, folder=None):
    """The newest verified ZIM for a source prefix (e.g. "wikem_en_all_nopic"), or None. Kiwix names files
    <prefix>_YYYY-MM.zim, sometimes with a letter for a re-release (nhs.uk_en_medicines_2026-09b.zim), so the
    newest sorts last."""
    folder = folder or zim_dir()
    rx = re.compile(re.escape(prefix) + DATE_SUFFIX)
    try:
        names = sorted(n for n in os.listdir(folder) if rx.match(n))
    except FileNotFoundError:
        return None
    for name in reversed(names):
        path = os.path.join(folder, name)
        if verified_record(path):
            return path
    return None


# ---------- planning and downloading ----------

def plan(wanted=None, force=(), manifest=None):
    """What a fetch would do, without downloading any ZIM. One dict per source: name, flavour, file, path, size,
    sha256, key (the build key), status and error. status is "current" (a verified copy is on disk), "built" (the
    live build holds this file converted with today's converter, and its original was deleted), "download",
    "missing" (not in the catalog), "off" (switched off in library/source-switch.json) or "error". force: build keys whose ZIM must be on disk (refresh.py --rebuild).
    manifest: the live build's manifest (default: read from the data folder)."""
    from library import build_library
    if manifest is None:
        manifest = build_library.read_manifest(build_library.previous_build())
    chosen = [(n, f) for n, f in SOURCES if not wanted or n in wanted]
    try:
        catalog = fetch_catalog()
    except FetchError as exc:
        return [{"name": n, "flavour": f, "status": "error", "error": f"catalog: {exc}"} for n, f in chosen]
    absent = sum((n, f) not in catalog for n, f in chosen)
    if not catalog or (len(chosen) >= 4 and absent * 2 > len(chosen)):
        # A truncated or wrong catalog reply, not sources Kiwix dropped: an error, so nothing is pruned or warned.
        why = "catalog: the reply listed no entries" if not catalog else \
            f"catalog: {absent} of {len(chosen)} sources missing, the reply looks incomplete"
        return [{"name": n, "flavour": f, "status": "error", "error": why} for n, f in chosen]
    from library import titles
    off = titles.cleanup().sources_off()
    out = []
    for name, flavour in chosen:
        item = {"name": name, "flavour": flavour, "status": "missing", "error": None}
        path = catalog.get((name, flavour))
        if not path:
            item["error"] = "not in the Kiwix catalog"
            out.append(item)
            continue
        item.update(path=path, file=path.rsplit("/", 1)[1])
        entry = build_library.ZIM_SOURCES.get(file_prefix(item["file"]))
        item["key"] = entry[0] if entry else None
        if item["key"] in off:  # switched off in library/source-switch.json: never downloaded, never built
            item.update(status="off", error="switched off in library/source-switch.json")
            out.append(item)
            continue
        try:
            item["size"], item["sha256"] = fetch_checksum(path)
        except FetchError as exc:
            item.update(status="error", error=f"checksum: {exc}")
            out.append(item)
            continue
        dest = os.path.join(zim_dir(), item["file"])
        if is_verified(dest, item["size"], item["sha256"]):
            item["status"] = "current"
        elif item["key"] not in force and build_library.is_built(manifest, item["key"], item["file"], item["sha256"]):
            item["status"] = "built"
        else:
            item["status"] = "download"
        out.append(item)
    return out


def max_download_seconds(remaining):
    """Time allowed for one download call: an hour plus the remaining bytes at 250 KB/s, generous for a slow line.
    A transfer cut off by it resumes on the next run."""
    return 3600 + remaining // 250_000


def download_zim(item):
    """Download one planned ZIM and verify it. True if a verified file is in place afterwards."""
    if item.get("status") in ("current", "built"):
        return True
    dest = os.path.join(zim_dir(), item.get("file") or "")
    if item.get("status") != "download":
        # No checksum means no download this run. Whatever is on disk stays as it is: a copy that verified earlier
        # (the live build may have come from it) and a partial download, which resumes on a later run and is
        # never used until it verifies.
        return False
    os.makedirs(zim_dir(), exist_ok=True)
    size, sha256 = item["size"], item["sha256"]
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    if have > size:
        remove_zim(dest)
        have = 0
    if have < size:
        if shutil.disk_usage(zim_dir()).free < size - have + FREE_SPACE_MARGIN:
            print(f"NOT ENOUGH DISK SPACE for {item['file']}", flush=True)
            return False
        print(f"downloading {item['file']} ({size / 1e9:.2f} GB)", flush=True)
        # One connection per file: Kiwix mirrors answer parallel range requests with HTTP 429. --max-filesize is the
        # size from the .meta4, so a mirror cannot send more than that (curl 8.4+ stops mid-transfer too; a resumed
        # transfer's remaining bytes are always below it). See max_download_seconds() for the time limit.
        try:
            ok = safe_fetch.download(zim_url(item["path"]), dest, frozenset({ZIM_HOST}), follow=True, resume=True,
                                     max_bytes=size, max_time=max_download_seconds(size - have))
        except FetchError as exc:
            print(f"    {exc}", flush=True)
            ok = False
        if not ok:
            if os.path.exists(dest) and os.path.getsize(dest) > size:
                remove_zim(dest)
            print(f"FAILED: {item['file']} (the next run resumes it)", flush=True)
            return False
    return verify_zim(dest, size, sha256)


def describe(item):
    if item["status"] == "download":
        return f"download  {item['file']} ({item['size'] / 1e9:.2f} GB)"
    if item["status"] == "current":
        return f"current   {item['file']}"
    if item["status"] == "built":
        return f"built     {item['file']} (in the live build; the original was deleted, {item['size'] / 1e9:.2f} GB)"
    return f"{item['status']:<9} {item['name']} {item.get('flavour') or ''}: {item.get('error')}".rstrip()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in args
    wanted = {a for a in args if not a.startswith("--")}
    unknown = wanted - {n for n, _ in SOURCES}
    if unknown:
        sys.exit("unknown source name(s): " + ", ".join(sorted(unknown)))
    items = plan(wanted or None)
    failed = 0
    for item in items:
        print(describe(item), flush=True)
        if not dry and item["status"] not in ("current", "built", "missing", "off"):  # missing from the catalog: a warning
            failed += not download_zim(item)
    print("dry run: nothing downloaded" if dry else f"finished, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
