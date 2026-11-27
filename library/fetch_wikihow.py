"""Download the wikiHow text dump (wikihowAll.csv, about 620 MB) from Hugging Face, checked against a pin kept in
this repo.

Usage: .venv/bin/python library/fetch_wikihow.py [--dry-run]
The file lands in data.noindex/raw/wikihowAll.csv. refresh.py runs this after the ZIMs.

The dataset is Lajvi/wikihowAll (Koupaee and Wang, 2018; CC BY-NC-SA 3.0, see SOURCES.md): one CSV with the
columns headline, title and text, plain text only. PIN below is the reviewed version: the dataset commit, the
file's size and its SHA-256 (the Git LFS object id Hugging Face published for it on 12 November 2026). Only a copy
that matches the pin, or one a person accepted with refresh.py --accept, goes into a build.

Each run still asks Hugging Face's API for the dataset's current commit and hash, but only to compare. If they
differ from the copy in use, the new file is downloaded into raw/.pending/ and held for review, like a changed
PDF, and the copy in use stays. Whoever controls the dataset therefore cannot change what gets built.

Once a build is live, refresh.py deletes the CSV (build_library.retire_originals()). The trusted copy (the pin, or
the one a person accepted, by its recorded commit URL, size and hash) is downloaded again only when it is needed:
its converter version changed, --rebuild wikihow, or the live build does not hold it.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import fetch_zims, safe_fetch  # noqa: E402
from library.safe_fetch import FetchError  # noqa: E402

DATASET = "Lajvi/wikihowAll"
FILE = "wikihowAll.csv"
REL = FILE  # its path under raw/, also its key in the hash lock
PIN = {"commit": "5e44fed0e204c27ad985e6292e55e311504d4fe5", "size": 619172672,
       "sha256": "8ebf5bd485275af895aa83ea3e55c416fe76aa4fefc58e0b9e6d344852bde5aa"}
HF_HOSTS = frozenset({"huggingface.co"})  # the API and the start of the download (which redirects to Hugging
                                           # Face's CDN, https only; the bytes are trusted only once they match)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def dest(raw_dir=None):
    return os.path.join(raw_dir or safe_fetch.RAW_DIR, FILE)


def revision_url():
    return f"https://huggingface.co/api/datasets/{DATASET}/revision/main"


def tree_url(commit):
    return f"https://huggingface.co/api/datasets/{DATASET}/tree/{commit}"


def download_url(commit):
    return f"https://huggingface.co/datasets/{DATASET}/resolve/{commit}/{FILE}"


def published():
    """(commit, size, sha256) the dataset offers now, from Hugging Face's API with redirects off. Used only to
    compare with the copy in use. FetchError if any part is missing."""
    try:
        commit = json.loads(safe_fetch.fetch_small(revision_url(), HF_HOSTS, max_bytes=1 << 20)).get("sha", "")
    except (ValueError, AttributeError):
        raise FetchError("Hugging Face's revision answer is not JSON") from None
    if not isinstance(commit, str) or not COMMIT_RE.match(commit):
        raise FetchError("no commit id for the wikiHow dataset")
    try:
        files = json.loads(safe_fetch.fetch_small(tree_url(commit), HF_HOSTS, max_bytes=1 << 20))
    except ValueError:
        raise FetchError("Hugging Face's file list is not JSON") from None
    entry = next((f for f in files if isinstance(f, dict) and f.get("path") == FILE), None) \
        if isinstance(files, list) else None
    lfs = entry.get("lfs") if entry else None
    if not isinstance(lfs, dict):
        raise FetchError(f"no published hash for {FILE}")
    sha256, size = str(lfs.get("oid", "")).lower(), lfs.get("size")
    if not safe_fetch.SHA256_RE.match(sha256) or not isinstance(size, int) or size <= 0:
        raise FetchError(f"no usable published hash or size for {FILE}")
    return commit, size, sha256


def trusted_sha(lock):
    """The hash the build may use: one a person accepted (in the hash lock), else the pin."""
    accepted = lock["files"].get(REL)
    return accepted["sha256"] if accepted else PIN["sha256"]


def ready(lock, raw_dir=None):
    """(path, fingerprint) of a wikiHow file the build may use, or None: the pinned copy (verified on download), or
    one a person accepted."""
    path = dest(raw_dir)
    rec = fetch_zims.verified_record(path)
    if rec and rec["sha256"] == trusted_sha(lock):
        return path, rec["sha256"]
    if safe_fetch.locked_ok(lock, REL, raw_dir):
        return path, lock["files"][REL]["sha256"]
    return None


def trusted_copy(lock):
    """(url, size, sha256) of the file the build may use: the accepted copy recorded in the hash lock (fetched by its
    commit URL), else the pin."""
    accepted = lock["files"].get(REL)
    if accepted:
        return accepted.get("url"), accepted.get("size"), accepted.get("sha256")
    return download_url(PIN["commit"]), PIN["size"], PIN["sha256"]


def plan(lock=None, force=(), manifest=None):
    """What a fetch would do, without downloading the file. A dict:
      status    "current" (the trusted copy is in place), "built" (the live build holds it, converted with today's
                converter, and the CSV was deleted), or "download" (fetch the trusted copy: the pin, or the
                accepted one by its commit URL), or "off" (switched off in library/source-switch.json). force: build keys to convert again (refresh.py --rebuild).
      upstream  "same", "changed" (the dataset offers another file: it would be held for review), "held" (that
                change is already waiting in raw/.pending/), or "unknown" (the API did not answer)
      plus upstream_commit, upstream_size, upstream_sha256 and error."""
    lock = lock if lock is not None else safe_fetch.load_lock()
    from library import build_library
    if manifest is None:
        manifest = build_library.read_manifest(build_library.previous_build())
    from library import titles
    if "wikihow" in titles.cleanup().sources_off():  # switched off in library/source-switch.json: nothing is fetched
        return {"status": "off", "copy": None, "size": None, "upstream": "same", "upstream_commit": None,
                "upstream_size": None, "upstream_sha256": None, "error": None}
    if ready(lock):
        status = "current"
    elif "wikihow" not in force and build_library.is_built(manifest, "wikihow", FILE, trusted_sha(lock)):
        status = "built"
    else:
        status = "download"
    item = {"status": status, "copy": "accepted" if REL in lock["files"] else "pinned",
            "size": trusted_copy(lock)[1],
            "upstream": "unknown", "upstream_commit": None, "upstream_size": None, "upstream_sha256": None,
            "error": None}
    try:
        commit, size, sha256 = published()
    except FetchError as exc:
        item["error"] = str(exc)
        return item
    item.update(upstream_commit=commit, upstream_size=size, upstream_sha256=sha256)
    if sha256 == trusted_sha(lock):
        item["upstream"] = "same"
    elif lock["pending"].get(REL, {}).get("sha256") == sha256 and os.path.exists(
            os.path.join(safe_fetch.PENDING_DIR, REL)):
        item["upstream"] = "held"
    else:
        item["upstream"] = "changed"
    return item


def describe(item):
    if item["status"] == "off":
        return f"off       {FILE} (switched off in library/source-switch.json)"
    if item["status"] == "current":
        line = f"current   {FILE}"
    elif item["status"] == "built":
        line = f"built     {FILE} (in the live build; the original was deleted)"
    else:
        line = f"download  {FILE} ({(item.get('size') or 0) / 1e9:.2f} GB, the {item.get('copy', 'pinned')} copy)"
    if item["upstream"] == "changed":
        line += (f"; the dataset now offers commit {item['upstream_commit'][:7]} "
                 f"({item['upstream_size'] / 1e9:.2f} GB): it would be held for review")
    elif item["upstream"] == "held":
        line += "; a newer version is waiting for review"
    elif item["upstream"] == "unknown":
        line += f"; could not compare with Hugging Face ({item['error']})"
    return line


def fetch(item, lock, lock_path=None):
    """Carry out a planned fetch. Returns {"pending": [...], "failed": [...]} like fetch_manuals.main()."""
    result = {"pending": [], "failed": []}
    os.makedirs(safe_fetch.RAW_DIR, exist_ok=True)
    if item["status"] == "download" and not fetch_trusted(lock):
        result["failed"].append("wikiHow (the " + ("accepted" if REL in lock["files"] else "pinned") + " copy)")
    if item["upstream"] == "changed":
        # A different file upstream: fetch it into raw/.pending/ for a person to review. The hash lock decides:
        # with an accepted copy recorded, a different file is "pending"; with only the pin, untrusted=True holds it.
        size = item["upstream_size"]
        outcome = safe_fetch.fetch_locked(
            lock, REL, [download_url(item["upstream_commit"])], HF_HOSTS, max_bytes=size,
            max_time=fetch_zims.max_download_seconds(size), untrusted=REL not in lock["files"],
            check=lambda path: os.path.getsize(path) == size,  # exactly what Hugging Face says it published
            reason=f"the wikiHow dataset on Hugging Face changed (commit {item['upstream_commit'][:7]}, "
                   f"{size:,} bytes); the {'accepted' if REL in lock['files'] else 'pinned'} copy stays in use")
        safe_fetch.save_lock(lock, lock_path)
        print(f"{outcome:<13} wikiHow: the dataset changed (commit {item['upstream_commit'][:7]})", flush=True)
        if outcome in ("pending",):
            result["pending"].append(REL)
        elif outcome == "failed":
            result["failed"].append("wikiHow (the changed version)")
    return result


def fetch_trusted(lock):
    """Download the copy the build may use (trusted_copy()) and keep it only if it matches."""
    url, size, sha256 = trusted_copy(lock)
    if not url or not isinstance(size, int) or not safe_fetch.SHA256_RE.match(str(sha256 or "")):
        print(f"FAILED: {FILE}: the accepted copy's URL, size or hash is not recorded", flush=True)
        return False
    return fetch_exact(url, size, sha256)


def fetch_exact(url, size, sha256):
    """Download url (resuming a partial copy) and keep it only if it has exactly this size and SHA-256."""
    part = dest() + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    if have < size:
        print(f"downloading {FILE} ({size / 1e9:.2f} GB) from {url}", flush=True)
        try:
            ok = safe_fetch.download(url, part, HF_HOSTS, follow=True, resume=True,
                                     max_bytes=size, max_time=fetch_zims.max_download_seconds(size - have))
        except FetchError as exc:
            print(f"    {exc}", flush=True)
            ok = False
        if not ok:
            print(f"FAILED: {FILE} (the next run resumes it)", flush=True)
            return False
    if not fetch_zims.verify_zim(part, size, sha256):  # deletes a copy that does not match
        return False
    os.replace(part, dest())
    os.replace(part + ".verified", dest() + ".verified")
    print(f"verified      {FILE} (sha256 {sha256[:16]})", flush=True)
    return True


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    lock = safe_fetch.load_lock()
    item = plan(lock)
    print(describe(item), flush=True)
    if "--dry-run" in args:
        return 0
    return 1 if fetch(item, lock)["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
