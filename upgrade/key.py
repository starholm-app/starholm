"""The upgrade key: checked offline against one built-in Ed25519 public key, kept in the app's own folder.

A key is what the buyer pastes, for example "SH1-AEA4-C4J4-...". The format is in keyservice/README.md, "Key format":
"SH1", then 80 bytes in RFC 4648 base32 (no padding) written in groups of four joined by "-". Bytes 0-15 are the
payload (version, licence, 12-byte key id, issue date in days since 1970); bytes 16-79 are the Ed25519 signature of
those 16 bytes. The check needs no network, no account and sends nothing anywhere.

THE BUILT-IN PUBLIC KEY is PUBLIC_KEY_BASE64 below: the production public key (keyservice/README.md, "Production
keypair"), so the test vectors' keys don't unlock a release. The tests swap in the TEST key from
keyservice/test-vectors.json themselves. ops/make_public_tree.py --check fails if the test key or the test revoked id
comes back here.

REVOCATION BY THE SERVICE. The check stays offline. But whenever the app talks to the key service anyway (asking for
pack download links, or a later pack update check), it passes the answer to note_service_answer(). An answer of
HTTP 403 {"error": "revoked"} (the key was refunded, charged back or withdrawn) records the key id in
SERVICE_REVOKED_FILE, and from then on check(..., app_dir) and unlocked() treat that key as revoked on this
computer, which locks the paid features again. Any successful answer about the key clears the record, so a key that
is reinstated (a dispute won) unlocks again on the next connection. keyservice/README.md, "Revocation", has the contract.
"""
import json
import base64
import datetime
import os
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# ---- the one place the release step changes ----
PUBLIC_KEY_BASE64 = "AWDroLikw1+nNVKfhG3Y4ciSgQmMxjWhJB8ecVcGVNI="  # PRODUCTION key, made 25 November 2026
REVOKED_KEY_IDS = frozenset()  # production: none revoked in the app; the service holds the revocation list
# ---- end ----

PREFIX = "SH1"
BODY_CHARS = 128  # 80 bytes in base32
LICENCES = {1: ("individual", 3), 2: ("organisation", 25)}
KEY_FILE = "upgrade-key.txt"  # in the app's folder (app-data/ or $SURVIVAL_LLM_APP_DIR)
SERVICE_REVOKED_FILE = "upgrade-key-revoked.json"  # key ids the key service answered "revoked" for, same folder
B32 = re.compile(r"[A-Z2-7]{%d}" % BODY_CHARS)


def _public_key():
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_BASE64))


def normalise(text):
    """The key as typed or pasted, without whitespace and dashes, in upper case."""
    return re.sub(r"[\s-]+", "", text if isinstance(text, str) else "").upper()


def pretty(text):
    """A key in its written form: SH1 and the body in groups of four, joined by "-"."""
    flat = normalise(text)
    body = flat[len(PREFIX):]
    return "-".join([PREFIX] + [body[i:i + 4] for i in range(0, len(body), 4)])


def masked(text):
    """The saved key as Settings shows it: the first two groups and the last, the rest as dots."""
    groups = pretty(text).split("-")
    if len(groups) < 5:
        return pretty(text)
    return "-".join(groups[:3] + ["••••", "••••"] + groups[-1:])


def check(text, public_key_base64=None, app_dir=None):
    """{"verdict": "valid" | "invalid" | "revoked", "reason": ... (invalid only), "payload": {...} (valid and revoked)}.
    reason: "malformed", "bad_signature", "unknown_version" or "unknown_licence", the key service's names.
    With app_dir, a key the key service called revoked on this computer (service_revoked()) is revoked too."""
    flat = normalise(text)
    if not flat.startswith(PREFIX) or not B32.fullmatch(flat[len(PREFIX):]):
        return {"verdict": "invalid", "reason": "malformed"}
    try:
        raw = base64.b32decode(flat[len(PREFIX):])
    except (ValueError, TypeError):
        return {"verdict": "invalid", "reason": "malformed"}
    if len(raw) != 80:
        return {"verdict": "invalid", "reason": "malformed"}
    payload, signature = raw[:16], raw[16:]
    try:
        key = (Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_base64))
               if public_key_base64 else _public_key())
        key.verify(signature, payload)
    except InvalidSignature:
        return {"verdict": "invalid", "reason": "bad_signature"}
    if payload[0] != 1:
        return {"verdict": "invalid", "reason": "unknown_version"}
    if payload[1] not in LICENCES:
        return {"verdict": "invalid", "reason": "unknown_licence"}
    licence, computers = LICENCES[payload[1]]
    days = int.from_bytes(payload[14:16], "big")
    out = {"version": 1, "licence": licence, "computers": computers, "key_id": payload[2:14].hex(),
           "issued_on": (datetime.date(1970, 1, 1) + datetime.timedelta(days=days)).isoformat()}
    revoked = out["key_id"] in REVOKED_KEY_IDS or (app_dir is not None and out["key_id"] in service_revoked(app_dir))
    return {"verdict": "revoked" if revoked else "valid", "payload": out}


# ---------- the saved key ----------

def key_path(app_dir):
    return os.path.join(app_dir, KEY_FILE)


def load(app_dir):
    """The saved key's text, or None."""
    try:
        with open(key_path(app_dir), encoding="utf-8") as f:
            text = f.read(2000).strip()
    except OSError:
        return None
    return text or None


def save(app_dir, text):
    """Save a key (only after check() said valid), readable by this user only. Written whole, then renamed in."""
    os.makedirs(app_dir, mode=0o700, exist_ok=True)
    tmp = key_path(app_dir) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(pretty(text) + "\n")
    os.replace(tmp, key_path(app_dir))


def remove(app_dir):
    try:
        os.remove(key_path(app_dir))
    except FileNotFoundError:
        pass


def saved_check(app_dir):
    """check() of the saved key, or None when there is none."""
    text = load(app_dir)
    return check(text, app_dir=app_dir) if text else None


# ---------- revocation the key service reported ----------

def service_revoked(app_dir):
    """The key ids the key service answered "revoked" for on this computer."""
    try:
        with open(os.path.join(app_dir, SERVICE_REVOKED_FILE), encoding="utf-8") as f:
            ids = json.loads(f.read(100_000))
    except (OSError, ValueError):
        return frozenset()
    return frozenset(i for i in ids if isinstance(i, str) and re.fullmatch(r"[0-9a-f]{24}", i)) \
        if isinstance(ids, list) else frozenset()


def _write_revoked(app_dir, ids):
    os.makedirs(app_dir, mode=0o700, exist_ok=True)
    path = os.path.join(app_dir, SERVICE_REVOKED_FILE)
    if not ids:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f)
    os.replace(tmp, path)


def note_service_answer(app_dir, text, error_code):
    """Call after every key service answer about this key: error_code is the answer's "error" ("revoked", ...) or
    None for a successful answer. "revoked" locks this key on this computer; success unlocks it again. Other
    refusals (install_limit_reached, invalid_key, a network error) change nothing. Returns the key's new verdict."""
    result = check(text)
    if not result.get("payload"):
        return result["verdict"]
    key_id = result["payload"]["key_id"]
    ids = set(service_revoked(app_dir))
    if error_code == "revoked" and key_id not in ids:
        _write_revoked(app_dir, ids | {key_id})
    elif error_code is None and key_id in ids:
        _write_revoked(app_dir, ids - {key_id})
    return check(text, app_dir=app_dir)["verdict"]


def unlocked(app_dir):
    """Whether the paid features are unlocked here: a saved key that checks out and is not revoked."""
    result = saved_check(app_dir)
    return bool(result and result["verdict"] == "valid")
