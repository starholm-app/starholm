"""Download individual survival / first-aid manuals (PDFs) that are not in any Kiwix ZIM.

Usage: .venv/bin/python library/fetch_manuals.py
Files land in data.noindex/raw/manuals/<group>/ with a manifest.json of titles and licenses.
Groups: "us-gov" = U.S. government works (public domain); "nc" = free for non-commercial use only.
Sources and terms were checked on 2026-10-23; see SOURCES.md.

Every PDF is checked against the hash lock (data.noindex/hashes.lock.json). The first copy from the
official address is recorded. A copy whose hash differs is kept back in raw/.pending/ for review, and the
previous copy stays in use. A copy from the Internet Archive fallback counts only if it matches the lock.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import safe_fetch  # noqa: E402

ARMY = "https://armypubs.army.mil/epubs/DR_pubs/DR_a/"
WHO = "https://iris.who.int/server/api/core/bitstreams/"
MANUALS = [
    ("us-gov", "USAF AFH 10-644: SERE Operations (survival, evasion, field medicine)",
     "https://static.e-publishing.af.mil/production/1/af_a3/publication/afh10-644/afh10-644.pdf"),
    ("us-gov", "US Army ATP 3-50.21: Survival", ARMY + "pdf/web/ARN12086_ATP%203-50x21%20FINAL%20WEB%202.pdf"),
    ("us-gov", "US Army ATP 4-02.11: Casualty Response, Tactical Combat Casualty Care and First Aid (2026)",
     ARMY + "ARN46159-ATP_4-02.11-000-WEB-1.pdf"),
    ("us-gov", "US Army TC 21-3: Cold Weather Survival and Operations", ARMY + "ARN45217-TC_21-3-000-WEB-1.pdf"),
    ("us-gov", "US Army ATP 4-25.12: Unit Field Sanitation", ARMY + "ARN44577-ATP_4-25.12-002-WEB-3.pdf"),
    ("us-gov", "US Army TB MED 577: Sanitary Control and Surveillance of Field Water Supplies",
     ARMY + "pdf/web/tbmed577.pdf"),
    ("us-gov", "US Army TB MED 507: Heat Stress Control and Heat Casualty Management",
     ARMY + "ARN43608-TB_MED_507-001-WEB-2.pdf"),
    ("us-gov", "Nuclear War Survival Skills (Cresson Kearny, ORNL-5037, 1979)",
     # ORNL's own HTTPS copy. The OSTI record (www.osti.gov/servlets/purl/814213) redirects here over plain http,
     # which the fetcher refuses (checked 2026-11-12).
     "https://info.ornl.gov/sites/publications/Files/Pub57110.pdf"),
    ("us-gov", "FEMA: Are You Ready? An In-Depth Guide to Citizen Preparedness",
     "https://www.ready.gov/sites/default/files/2021-11/are-you-ready-guide.pdf"),
    ("us-gov", "FEMA CERT Basic Training Participant Manual",
     "https://www.ready.gov/sites/default/files/2019.CERT_.Basic_.PM_FINAL_508c.pdf"),
    ("us-gov", "Emergency Response Guidebook 2024 (hazardous materials)",
     "https://www.phmsa.dot.gov/sites/phmsa.dot.gov/files/2024-04/ERG2024-Eng-Web-a.pdf"),
    ("us-gov", "Joint Trauma System: Prolonged Casualty Care Guidelines (2021)",
     "https://jts.health.mil/assets/docs/cpgs/Prolonged_Casualty_Care_Guidelines_21_Dec_2021_ID91v1.1.pdf"),
    ("nc", "IFRC International First Aid, Resuscitation and Education Guidelines 2020",
     "https://www.ifrc.org/sites/default/files/2022-02/EN_GFARC_GUIDELINES_2020.pdf"),
    ("nc", "WHO-ICRC Basic Emergency Care (2018)", WHO + "63432b9f-8808-44c5-9692-cea717c0cbda/content"),
    ("nc", "WHO: Managing Complications in Pregnancy and Childbirth (2017)",
     WHO + "3bda1306-da5d-487d-b2ad-aff995112a7a/content"),
    ("nc", "WHO: Guidelines for Drinking-water Quality (4th ed., 2022)",
     WHO + "69c17edd-ee26-425b-9d34-33799377e886/content"),
]
# The official sites refuse scripted downloads (HTTP 403, checked 2026-10-23); the Internet Archive has the same files.
FALLBACK = {
    "https://static.e-publishing.af.mil/production/1/af_a3/publication/afh10-644/afh10-644.pdf":
        "https://web.archive.org/web/2025id_/https://static.e-publishing.af.mil/production/1/af_a3/publication/afh10-644/afh10-644.pdf",
    "https://www.phmsa.dot.gov/sites/phmsa.dot.gov/files/2024-04/ERG2024-Eng-Web-a.pdf":
        "https://web.archive.org/web/20240806051758id_/https://www.phmsa.dot.gov/sites/phmsa.dot.gov/files/2024-04/ERG2024-Eng-Web-a.pdf",
}
LICENSES = {"us-gov": "U.S. government work, public domain (17 U.S.C. 105)",
            "nc": "Free for non-commercial use with attribution (see SOURCES.md)"}
# The app's own name; a site that refuses it gets that one download as Safari (safe_fetch.HONEST_UA, SAFARI_UA).
UA, FALLBACK_UA = safe_fetch.HONEST_UA, safe_fetch.SAFARI_UA

ALLOWED_HOSTS = safe_fetch.hosts_of([url for _, _, url in MANUALS] + list(FALLBACK.values()))


def write_json_if_changed(path, data):
    """Write data as JSON, atomically, only when it differs from what is there (the build fingerprints it)."""
    text = json.dumps(data, indent=1)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
    except FileNotFoundError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def main(raw_dir=None, lock_path=None):
    """Fetch every manual and settle it against the hash lock. Returns {"pending": [...], "failed": [...]}:
    the files newly held back for review, and the titles nothing usable came back for."""
    raw_dir = raw_dir or safe_fetch.RAW_DIR
    root = os.path.join(raw_dir, "manuals")
    for group in LICENSES:
        os.makedirs(os.path.join(root, group), exist_ok=True)
    lock = safe_fetch.load_lock(lock_path)
    manifest = {g: [] for g in LICENSES}
    result = {"pending": [], "failed": []}
    for n, (group, title, url) in enumerate(MANUALS):
        rel = f"manuals/{group}/{n:02d}.pdf"
        outcome = safe_fetch.fetch_locked(lock, rel, [u for u in (url, FALLBACK.get(url)) if u], ALLOWED_HOSTS,
                                          user_agent=UA, fallback_user_agent=FALLBACK_UA,
                                          check=safe_fetch.is_pdf, raw_dir=raw_dir)
        safe_fetch.save_lock(lock, lock_path)
        print(f"{outcome:<13} {title}", flush=True)
        if outcome == "pending":
            result["pending"].append(rel)
        elif outcome == "failed":
            result["failed"].append(title)
        if safe_fetch.locked_ok(lock, rel, raw_dir):  # the reviewed copy, even when a newer one is held back
            manifest[group].append({"file": os.path.basename(rel), "title": title, "url": url,
                                    "license": LICENSES[group]})
    for group, items in manifest.items():
        write_json_if_changed(os.path.join(root, group, "manifest.json"), items)
    print({g: len(v) for g, v in manifest.items()}, flush=True)
    return result


if __name__ == "__main__":
    sys.exit(1 if main()["failed"] else 0)
