"""A guide listed in guides/index.json whose body is not on disk (the public copy ships three free guides; the rest
come with the upgrade) is listed locked, and opening it gives the same 423 "Included in the upgrade" preview a paid
guide gets without a key, built from its index entry. So the Help now tree and the links in the free guides land on
the locked preview, never on a 404. An entry is kept only when the index marks it "upgrade": true. With a valid key
(made here, with a key pair of the test's own) such a guide answers 409 {"download": true}: the dialog sends the
person to the Upgrade page's Download now. The model still gets the free guides' sections when the index lists
guides whose bodies are absent.

On a small shelf in a temporary folder always; on the real guides/ too when it lacks the paid bodies (the public
tree), where every Help now end point and every guide: link in the free guides is opened.

Usage: run through tests/run_all.py"""
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sandbox  # noqa: E402

sandbox.require()
os.environ.pop("OLLAMA_HOST", None)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import base64  # noqa: E402
import datetime  # noqa: E402
import subprocess  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

import core  # noqa: E402
import server  # noqa: E402
import upgrade  # noqa: E402
from upgrade import key as upgrade_key  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        FAILS.append(name)


httpd = server.make_server(0)
server.PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def get(path):
    conn = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=30)
    conn.request("GET", path, headers={"X-Token": server.TOKEN})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data or b"null")
    except ValueError:
        return resp.status, data


REAL_GUIDES = server.GUIDES_DIR
REAL_UNLOCKED = server.unlocked
REAL_PUBLIC = upgrade_key.PUBLIC_KEY_BASE64

# A key pair of this test's own stands in for the key service: its public key replaces the built-in one here only.
SIGNER = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(SIGNER.public_key().public_bytes(serialization.Encoding.Raw,
                                                           serialization.PublicFormat.Raw)).decode()


def make_key(licence=1, key_id=b"\x11" * 12, signer=SIGNER):
    days = (datetime.date(2026, 11, 20) - datetime.date(1970, 1, 1)).days
    payload = bytes([1, licence]) + key_id + days.to_bytes(2, "big")
    body = base64.b32encode(payload + signer.sign(payload)).decode()
    return upgrade_key.pretty(upgrade_key.PREFIX + body)
TMP = tempfile.mkdtemp(prefix="guides-locked-")
try:
    # ---- a small shelf: one free guide, one paid guide with its body, one marked for the upgrade without ----
    shelf = os.path.join(TMP, "guides")
    body = "# {t}\n\nIntro.\n\n## First step\n\nDo this.\n\n## Second step\n\nThen that.\n"
    entries = [
        {"slug": "cpr-and-choking", "title": "CPR", "summary": "Free.", "group": "First aid", "order": 1},
        {"slug": "fire-at-home", "title": "Fire at home", "summary": "Paid, here.", "group": "Home", "order": 1},
        {"slug": "nuclear-incident", "title": "Nuclear incident", "summary": "Get inside, stay inside.",
         "group": "Disasters", "order": 2, "upgrade": True, "related": ["cpr-and-choking"]},
        {"slug": "orphan", "title": "Orphan", "summary": "No body, not marked.", "group": "Home", "order": 2},
        {"slug": "bleeding-burns-and-heat-stroke", "title": "Bleeding", "summary": "Free, missing.",
         "group": "First aid", "order": 2, "upgrade": True},
    ]
    for slug in ("cpr-and-choking", "fire-at-home"):
        os.makedirs(os.path.join(shelf, slug))
        with open(os.path.join(shelf, slug, "guide.md"), "w", encoding="utf-8") as f:
            f.write(body.format(t=slug))
    with open(os.path.join(shelf, "index.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f)
    server.GUIDES_DIR = shelf
    server.unlocked = lambda: False

    code, got = get("/api/guides")
    listed = {g["slug"]: g for g in got["guides"]}
    check("the shelf lists the marked guide without a body, locked, and leaves out an unmarked one",
          code == 200 and listed.get("nuclear-incident", {}).get("locked") is True and "orphan" not in listed
          and "bleeding-burns-and-heat-stroke" not in listed, listed)
    check("a free guide stays unlocked", listed.get("cpr-and-choking", {}).get("locked") is None, listed)

    code_paid, paid = get("/api/guides/fire-at-home")
    code_absent, absent = get("/api/guides/nuclear-incident")
    check("a paid guide without a key: 423 Included in the upgrade, with its preview",
          code_paid == 423 and paid.get("error") == server.LOCKED and paid.get("locked") is True, (code_paid, paid))
    check("a listed guide without its body: the same 423 and the same response shape",
          code_absent == 423 and absent.get("error") == server.LOCKED and absent.get("locked") is True
          and set(absent) == set(paid) and set(absent["preview"]) == set(paid["preview"]), (code_absent, absent))
    check("its preview comes from the index entry: title, summary, group, no headings",
          absent["preview"] == {"slug": "nuclear-incident", "title": "Nuclear incident",
                                "summary": "Get inside, stay inside.", "group": "Disasters", "headings": []},
          absent.get("preview"))
    check("a guide in no index entry is still 404", get("/api/guides/orphan")[0] == 404)
    check("the absent guide's figure is not served", get("/guides/nuclear-incident/blast-zones.svg")[0] in (404, 423))
    check("booklets and printing only see guides with a body",
          [g["slug"] for g in server.guides_on_disk()] == ["cpr-and-choking", "fire-at-home"],
          [g["slug"] for g in server.guides_on_disk()])

    # ---- a valid key, checked offline against the public key, unlocks; a guide not downloaded yet says so ----
    upgrade_key.PUBLIC_KEY_BASE64 = PUBLIC
    server.unlocked = REAL_UNLOCKED
    good = make_key()
    verdict = upgrade_key.check(good)
    check("a key signed by the matching private key is valid: an individual key for three computers",
          verdict["verdict"] == "valid" and verdict["payload"]["licence"] == "individual"
          and verdict["payload"]["computers"] == 3 and verdict["payload"]["issued_on"] == "2026-11-20", verdict)
    tampered = good[:-1] + ("A" if good[-1] != "A" else "B")
    check("a changed character fails the signature",
          upgrade_key.check(tampered)["verdict"] == "invalid", upgrade_key.check(tampered))
    check("a key signed by another private key is invalid",
          upgrade_key.check(make_key(signer=Ed25519PrivateKey.generate())) == {"verdict": "invalid",
                                                                                 "reason": "bad_signature"})
    check("an organisation key covers 25 computers", upgrade_key.check(make_key(licence=2))["payload"]["computers"] == 25)
    check("no key saved: locked", not server.unlocked())
    upgrade_key.save(server.APP_DIR, good)
    check("the saved valid key unlocks", server.unlocked())
    code, got = get("/api/guides")
    listed = {g["slug"]: g for g in got["guides"]}
    check("with the key, the shelf is unlocked; a guide not downloaded yet is marked for the download, not locked",
          code == 200 and "locked" not in got and listed["nuclear-incident"].get("download") is True
          and listed["nuclear-incident"].get("locked") is None and listed["fire-at-home"].get("locked") is None, got)
    check("with the key, a paid guide whose body is here opens", get("/api/guides/fire-at-home")[0] == 200)
    code, got = get("/api/guides/nuclear-incident")
    check("with the key, a guide not downloaded yet answers 409 download, never the 423 lock",
          code == 409 and got.get("download") is True and got.get("error") == server.NOT_DOWNLOADED
          and "locked" not in got and got["preview"]["title"] == "Nuclear incident", (code, got))
    upgrade_key.remove(server.APP_DIR)
    check("the key removed: locked again", not server.unlocked() and get("/api/guides/fire-at-home")[0] == 423)
    server.unlocked = lambda: False

    # ---- the interface: the download state is drawn as its own dialog, with Download now ----
    node = shutil.which("node")
    if not node:
        print("PASS (skipped: node isn't on the PATH, so the dialog isn't drawn)")
    else:
        r = subprocess.run([node, os.path.join(HERE, "guides_download_check.js"),
                            os.path.join(ROOT, "app", "static", "upgrade.js")], capture_output=True, text=True,
                           timeout=120)
        print((r.stdout + r.stderr).strip())
        check("the download dialog checks pass", r.returncode == 0)
    with open(os.path.join(ROOT, "app", "static", "app.js"), encoding="utf-8") as f:
        app = f.read()
    check("app.js opens the download dialog on a 409 download answer",
          "err.status === 409 && err.data && err.data.download) showLock(UPG.downloadGuide(err.data.preview, lockCtx()))"
          in app)

    # ---- the model's guide sections: 18 entries listed, three bodies on disk ----
    sections_dir = os.path.join(TMP, "three")
    os.makedirs(sections_dir)
    shutil.copy(os.path.join(REAL_GUIDES, "index.json"), sections_dir)
    for slug in upgrade.FREE_GUIDES:
        shutil.copytree(os.path.join(REAL_GUIDES, slug), os.path.join(sections_dir, slug))
    with open(os.path.join(sections_dir, "index.json"), encoding="utf-8") as f:
        listed_slugs = [g["slug"] for g in json.load(f)]
    sections = core.guide_sections(sections_dir)
    from_free = {s["id"].split(":", 1)[1].split("#")[0] for s in sections}
    check(f"with {len(listed_slugs)} entries and three bodies, the free guides' sections are all read",
          len(listed_slugs) > 3 and from_free == set(upgrade.FREE_GUIDES), from_free)
    lead = core.guide_lead("My dad collapsed and isn't breathing, what do I do?", ["cpr not breathing"], sections_dir)
    check("a safety question still gets sections from a free guide",
          lead and all(s["id"].startswith("guide:cpr-and-choking#") for s in lead[:1]), [s["id"] for s in lead])

    # ---- the real guides/: run only where the paid bodies are absent (the public tree) ----
    server.GUIDES_DIR = REAL_GUIDES
    with open(os.path.join(REAL_GUIDES, "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    paid = [g["slug"] for g in index if g["slug"] not in upgrade.FREE_GUIDES]
    bodies = [s for s in paid if os.path.exists(os.path.join(REAL_GUIDES, s))]
    if bodies:
        print(f"PASS (skipped: this copy has the paid guides' bodies, {len(bodies)} of {len(paid)})")
    else:
        check("no paid guide's body or figure is on disk", not bodies)
        check("every paid entry is marked for the upgrade", all(g.get("upgrade") is True for g in index
                                                                 if g["slug"] in paid))
        with open(os.path.join(ROOT, "app", "static", "guided-tree.js"), encoding="utf-8") as f:
            tree_slugs = set(re.findall(r'guide:\s*"([a-z0-9-]+)"', f.read()))
        links = set()
        for slug in upgrade.FREE_GUIDES:
            with open(os.path.join(REAL_GUIDES, slug, "guide.md"), encoding="utf-8") as f:
                links |= set(re.findall(r"\(guide:([a-z0-9-]+)", f.read()))
        tree_paid, link_paid = sorted(tree_slugs - set(upgrade.FREE_GUIDES)), sorted(links - set(upgrade.FREE_GUIDES))
        check("the Help now tree has end points in paid guides, and the free guides link to paid ones",
              tree_paid and link_paid, (tree_paid, link_paid))
        reached = {s: get(f"/api/guides/{s}") for s in sorted(set(tree_paid) | set(link_paid))}  # no key
        wrong = {s: r[0] for s, r in reached.items() if r[0] != 423 or r[1].get("error") != server.LOCKED}
        check(f"every one of them ({len(reached)}) reaches the locked state, none a 404", not wrong, wrong)
        listed = {g["slug"] for g in get("/api/guides")[1]["guides"]}
        check("the shelf lists all the index's guides", listed == {g["slug"] for g in index},
              sorted({g["slug"] for g in index} - listed))
        free_ok = all(get(f"/api/guides/{s}")[0] == 200 for s in upgrade.FREE_GUIDES)
        check("the three free guides open", free_ok)
        server.unlocked = lambda: True
        downloads = {s: get(f"/api/guides/{s}")[0] for s in sorted(reached)}
        check("with a key, each of them answers 409 download until the guides pack is downloaded",
              set(downloads.values()) == {409}, downloads)
        server.unlocked = lambda: False
        real = core.guide_sections(REAL_GUIDES)
        check("the model gets the free guides' sections from this copy's guides/",
              {s["id"].split(":", 1)[1].split("#")[0] for s in real} == set(upgrade.FREE_GUIDES))
finally:
    server.GUIDES_DIR = REAL_GUIDES
    server.unlocked = REAL_UNLOCKED
    upgrade_key.PUBLIC_KEY_BASE64 = REAL_PUBLIC
    upgrade_key.remove(server.APP_DIR)
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"{'FAILED' if FAILS else 'passed'}: guides locked")
sys.exit(1 if FAILS else 0)
