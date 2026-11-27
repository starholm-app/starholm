"""The key service's "revoked" answer locks the paid features on this computer (upgrade/key.py, upgrade/packs.py).

Checked: a key the service answers 403 {"error": "revoked"} for (refunded, charged back or withdrawn) is recorded in
the app's folder, so unlocked() is False and the Upgrade status shows it as revoked, and saving it again is refused;
a later successful answer about the same key (a dispute won) unlocks it again; other refusals (install_limit_reached,
invalid_key) change nothing; another key is not affected; the pack download job records the answer it gets.

The test signs its own keys with a throwaway keypair, so it needs no private test vectors. No network.

Usage: .venv/bin/python tests/upgrade_revocation_test.py"""
import base64
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="upgrade-revocation-")
os.environ.update(SURVIVAL_LLM_APP_DIR=os.path.join(TMP, "app"), SURVIVAL_LLM_LIBRARY=os.path.join(TMP, "library"),
                  SURVIVAL_LLM_FILES=os.path.join(TMP, "files"), SURVIVAL_LLM_DATA=os.path.join(TMP, "data"))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
sandbox.require()
sys.path.insert(0, ROOT)

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from upgrade import key as upgrade_key, packs  # noqa: E402

FAILS = []
APP = os.environ["SURVIVAL_LLM_APP_DIR"]


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"), flush=True)
    if not ok:
        FAILS.append(name)


PRIVATE = Ed25519PrivateKey.generate()
upgrade_key.PUBLIC_KEY_BASE64 = base64.b64encode(PRIVATE.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
upgrade_key.REVOKED_KEY_IDS = frozenset()


def make_key(key_id_byte, licence=1):
    payload = bytes([1, licence]) + bytes([key_id_byte]) * 12 + (20_000).to_bytes(2, "big")
    raw = payload + PRIVATE.sign(payload)
    return upgrade_key.pretty("SH1" + base64.b32encode(raw).decode())


A, B = make_key(0xA1), make_key(0xB2)
upgrade_key.save(APP, A)
check("a valid saved key unlocks", upgrade_key.unlocked(APP))

for code in ("install_limit_reached", "invalid_key", "bad_install_id"):
    upgrade_key.note_service_answer(APP, A, code)
check("other refusals change nothing", upgrade_key.unlocked(APP)
      and not os.path.exists(os.path.join(APP, upgrade_key.SERVICE_REVOKED_FILE)))

verdict = upgrade_key.note_service_answer(APP, A, "revoked")
check("a revoked answer locks the paid features on this computer", verdict == "revoked" and not upgrade_key.unlocked(APP),
      verdict)
check("the saved check says revoked, the plain offline check still says valid",
      upgrade_key.saved_check(APP)["verdict"] == "revoked" and upgrade_key.check(A)["verdict"] == "valid")
path = os.path.join(APP, upgrade_key.SERVICE_REVOKED_FILE)
check("the record holds the key id only, readable by this user only",
      open(path, encoding="utf-8").read() == '["' + "a1" * 12 + '"]' and (sandbox.WINDOWS or oct(os.stat(path).st_mode & 0o777) == "0o600"))
check("another key is not affected", upgrade_key.check(B, app_dir=APP)["verdict"] == "valid")

upgrade_key.note_service_answer(APP, A, None)
check("a later successful answer unlocks it again and removes the record",
      upgrade_key.unlocked(APP) and not os.path.exists(path))

# The download job passes the service's answer on.
answers = []


def refuse(*a, **kw):
    raise packs.Refused("revoked")


def allow(*a, **kw):
    answers.append("ok")
    return {"expires": 0, "packs": []}


job = packs.Job(APP)
job.state = {"phase": "asking"}  # as start() sets it
saved_ask = packs.ask_links
packs.ask_links = refuse
try:
    job._ask(A)
    check("the job raises the refusal", False)
except packs.Refused as exc:
    check("the job raises the refusal", exc.code == "revoked")
check("the job's revoked answer locks the key", not upgrade_key.unlocked(APP))
check("the Upgrade page says why", packs.refusal_text("revoked").startswith("This key has been withdrawn"))
packs.ask_links = allow
job._ask(A)
check("the job's successful answer unlocks it again", upgrade_key.unlocked(APP) and answers == ["ok"])
packs.ask_links = saved_ask

# A damaged record is ignored rather than locking anything.
with open(path, "w", encoding="utf-8") as f:
    f.write("{not json")
check("a damaged record locks nothing", upgrade_key.unlocked(APP))

shutil.rmtree(TMP, ignore_errors=True)
if FAILS:
    print(f"FAIL {len(FAILS)} checks: {', '.join(FAILS)}")
    sys.exit(1)
print("all upgrade revocation checks passed")
