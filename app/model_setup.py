"""The recommended-model table in Settings > Model, and its one-click download through Ollama.

The rows are data in recommended-models.json (memory size, Ollama model name, download size, whether this project has
tested it). The row for this Mac's memory is marked as recommended; a row that needs more memory than the Mac has is
"too large". A download asks the local Ollama to pull the model through core.ollama_request, which only ever reaches
127.0.0.1:11434, and reads its progress lines; Cancel closes that request, which stops Ollama's download (it keeps the
parts it has, so a later download goes on from there). One download runs at a time.

Updates: when the computer is online, each installed model in the table is compared with the Ollama registry
(REGISTRY, the only host it contacts): the registry's manifest digest for the same name and tag against the digest the
local Ollama reports (/api/tags). A different digest shows "Update available" and an Update button, which pulls the
same model in place. Offline, or when the registry doesn't answer, nothing is shown and no error reaches the person.
"""
import hashlib
import http.client
import json
import os
import re
import threading
import time

import core

ROWS_FILE = os.path.join(core.REPO, "recommended-models.json")
MAX_ROWS_FILE = 100_000  # bytes
MODEL_NAME_MAX = 120
PULL_READ_TIMEOUT = 120  # seconds with no progress line before a download is given up on
REGISTRY = "registry.ollama.ai"  # the only host the update check contacts
REGISTRY_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"
MAX_REGISTRY_ANSWER = 1_000_000  # bytes in the registry's answer
UPDATE_CHECK_EVERY = 6 * 3600  # seconds a registry answer is kept before the model is checked again
UPDATE_RETRY = 600  # seconds before a check that failed (offline) is tried again
REGISTRY_TIMEOUT = 10  # seconds
NAME_PART = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")


def load_rows(path=None):
    """The table's rows, smallest memory first: [{memory_gb, size, name, model, download_gb, tested, available}]. A row
    that isn't valid is left out; a missing or damaged file gives []. available: false marks a placeholder row, whose
    model name isn't settled yet: it offers no download (rows without the field are available)."""
    path = path or ROWS_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.loads(f.read(MAX_ROWS_FILE))
    except (OSError, ValueError):
        return []
    rows = []
    for row in data.get("rows", []) if isinstance(data, dict) else []:
        if not isinstance(row, dict):
            continue
        memory, name, size = row.get("memory_gb"), row.get("model"), row.get("download_gb")
        if not (isinstance(memory, int) and 0 < memory <= 4096 and isinstance(name, str)
                and 0 < len(name) <= MODEL_NAME_MAX and isinstance(size, (int, float)) and size > 0):
            continue
        rows.append({"memory_gb": memory, "size": str(row.get("size") or ""), "name": str(row.get("name") or ""),
                     "model": name, "download_gb": size, "tested": row.get("tested") is True,
                     "available": row.get("available") is not False})
    return sorted(rows, key=lambda r: r["memory_gb"])


def machine_memory_bytes():
    """This computer's installed memory in bytes, or None when the system won't say."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def memory_gb(total_bytes):
    """Installed memory in whole GB as the Mac reports it (32 GB is 32 GiB), or None."""
    return round(total_bytes / 2 ** 30) if total_bytes else None


def recommended(rows, gb):
    """The model name of the row for this much memory: the largest row that fits, or None."""
    fits = [r for r in rows if gb is not None and r["memory_gb"] <= gb]
    return fits[-1]["model"] if fits else None


def base(name):
    return name.removesuffix(":latest") if isinstance(name, str) else ""


class Pulls:
    """Downloads of models through Ollama, one at a time: {model: {state, completed, total, message}}.
    state: downloading, done, error or cancelled."""

    def __init__(self, request=None):
        self._request = request  # core.ollama_request unless a test passes its own
        self._lock = threading.Lock()
        self.jobs = {}
        self.on_done = None  # called with the model name after a download finishes

    def busy(self):
        with self._lock:
            return next((m for m, j in self.jobs.items() if j["state"] == "downloading"), None)

    def view(self):
        with self._lock:
            return {m: {k: j[k] for k in ("state", "completed", "total", "message")} for m, j in self.jobs.items()}

    def start(self, model):
        """Start downloading model. False when another download is running."""
        with self._lock:
            if any(j["state"] == "downloading" for j in self.jobs.values()):
                return False
            job = {"state": "downloading", "completed": 0, "total": 0, "message": "Starting…", "layers": {},
                   "cancel": threading.Event(), "resp": None}
            self.jobs[model] = job
        threading.Thread(target=self._run, args=(model, job), daemon=True).start()
        return True

    def cancel(self, model):
        """Stop a download. False when it isn't running."""
        with self._lock:
            job = self.jobs.get(model)
            if not job or job["state"] != "downloading":
                return False
            job["cancel"].set()
            job["state"], job["message"] = "cancelled", "Cancelled."
            resp = job["resp"]
        _close(resp)  # stops a read that is waiting for Ollama's next line
        return True

    def _run(self, model, job):
        request = self._request or core.ollama_request
        try:
            with request("/api/pull", {"model": model, "stream": True}, timeout=PULL_READ_TIMEOUT) as resp:
                with self._lock:
                    job["resp"] = resp
                if job["cancel"].is_set():
                    return
                for line in resp:
                    if job["cancel"].is_set():
                        return
                    self._progress(job, line)
                    if job["state"] != "downloading":
                        break
            with self._lock:
                if job["state"] == "downloading":  # the stream ended without "success"
                    job["state"], job["message"] = "error", "The download stopped before it finished. Try again."
        except Exception as exc:  # noqa: BLE001 - a failed download is reported on its row, never raised
            with self._lock:
                if job["state"] == "downloading":
                    job["state"], job["message"] = "error", pull_error(exc)
        finally:
            with self._lock:
                job["resp"] = None
            if job["state"] == "done" and self.on_done:
                try:
                    self.on_done(model)
                except Exception:  # noqa: BLE001
                    pass

    def _progress(self, job, line):
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        with self._lock:
            if event.get("error"):
                job["state"], job["message"] = "error", f"Ollama couldn't download it: {str(event['error'])[:200]}"
                return
            status = str(event.get("status") or "")
            digest, total, done = event.get("digest"), event.get("total"), event.get("completed")
            if isinstance(digest, str) and isinstance(total, int) and total > 0:
                job["layers"][digest] = (min(done, total) if isinstance(done, int) and done > 0 else 0, total)
                job["completed"] = sum(c for c, _ in job["layers"].values())
                job["total"] = sum(t for _, t in job["layers"].values())
            if status == "success":
                job["state"], job["message"] = "done", "Downloaded."
                if job["total"]:
                    job["completed"] = job["total"]
            elif status:
                job["message"] = "Downloading…" if status.startswith("pulling") else status[:80].capitalize()


def _close(resp):
    """Close a response another thread may be reading: shut its socket down first, so the read returns at once."""
    if resp is None:
        return
    try:
        sock = resp.fp.raw._sock  # http.client's socket file
        sock.shutdown(2)
    except (AttributeError, OSError, ValueError):
        pass
    try:
        resp.close()
    except (AttributeError, OSError, ValueError):
        pass


def pull_error(exc):
    text = str(getattr(exc, "reason", exc))
    if "refused" in text.lower():
        return "Ollama isn't running, so the download couldn't start. Start Ollama, then try again."
    if "timed out" in text.lower():
        return "Ollama stopped sending progress, so the download was given up on. Try again."
    return f"The download failed: {text[:200]}"


def remove_model(model, request=None):
    """Delete an installed model through the local Ollama (DELETE /api/delete). Raises OSError on failure (an HTTP
    error when Ollama refuses, 404 for a model it doesn't have)."""
    with (request or core.ollama_request)("/api/delete", {"model": model}, timeout=60, method="DELETE") as resp:
        resp.read()


def next_model(removed, installed, rows):
    """After removing `removed`: the model answers come from next, or None when no chat model is left. The largest
    other installed row of the table first, else the first other installed model."""
    others = [m for m in installed or [] if base(m) != base(removed)]
    have = {base(m) for m in others}
    table_rows = [r["model"] for r in sorted(rows, key=lambda r: -r["memory_gb"]) if base(r["model"]) in have]
    return base(table_rows[0]) if table_rows else (base(others[0]) if others else None)


def manifest_path(model):
    """The registry path for an Ollama model name: "gemma4:12b" -> "/v2/library/gemma4/manifests/12b" (no tag:
    "latest"; "user/name:tag" -> /v2/user/name/...). None for a name on another host or one that isn't plain."""
    name, _, tag = (model or "").partition(":")
    tag = tag or "latest"
    parts = name.split("/")
    if len(parts) == 1:
        parts = ["library"] + parts
    if len(parts) != 2 or not all(NAME_PART.fullmatch(p) for p in parts) or not TAG.fullmatch(tag):
        return None
    return f"/v2/{parts[0]}/{parts[1]}/manifests/{tag}"


def _registry_connection(host, port):
    import ssl
    try:
        from library.downloader import tls_context
        ctx = tls_context()
    except ImportError:
        ctx = ssl.create_default_context()
    return http.client.HTTPSConnection(host, port or 443, timeout=REGISTRY_TIMEOUT, context=ctx)


class Updates:
    """Which installed models have a newer version in the Ollama registry. check() runs in a background thread and
    never raises; available() only reads what it found. connect(host, port): an http.client connection (tests pass a
    local one)."""

    def __init__(self, connect=None):
        self.connect = connect or _registry_connection
        self._lock = threading.Lock()
        self.remote = {}  # model -> registry digest (hex)
        self.asked = {}  # model -> (time of the last attempt, whether it got an answer)
        self.thread = None

    def remote_digest(self, model):
        """The registry's digest for model (hex), or None: offline, no answer, or no such model. Never raises."""
        path = manifest_path(model)
        if not path:
            return None
        try:
            conn = self.connect(REGISTRY, 443)
            try:
                conn.request("GET", path, headers={"Host": REGISTRY, "Accept": REGISTRY_ACCEPT,
                                                   "User-Agent": "starholm-models/1.0"})
                resp = conn.getresponse()
                body = resp.read(MAX_REGISTRY_ANSWER + 1)
                header = resp.getheader("Docker-Content-Digest") or ""
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - offline or a broken answer: no update shown, nothing reported
            return None
        if resp.status != 200 or len(body) > MAX_REGISTRY_ANSWER:
            return None
        if re.fullmatch(r"sha256:[0-9a-f]{64}", header):
            return header[7:]
        return hashlib.sha256(body).hexdigest()

    def _due(self, model, now):
        last = self.asked.get(model)
        return not last or now - last[0] >= (UPDATE_CHECK_EVERY if last[1] else UPDATE_RETRY)

    def check(self, models, wait=False):
        """Ask the registry about the models not asked recently, in a background thread (one at a time)."""
        with self._lock:
            now = time.time()
            todo = [m for m in models if self._due(m, now)]
            if not todo or (self.thread and self.thread.is_alive()):
                return
            for m in todo:
                self.asked[m] = (now, False)
            self.thread = threading.Thread(target=self._run, args=(todo,), daemon=True, name="model-updates")
            self.thread.start()
        if wait:
            self.thread.join(60)

    def _run(self, models):
        for m in models:
            digest = self.remote_digest(m)
            with self._lock:
                if digest:
                    self.remote[m] = digest
                    self.asked[m] = (time.time(), True)

    def available(self, model, local_digest):
        """True when the registry gave a digest for model and it differs from the local one."""
        local = str(local_digest or "").removeprefix("sha256:").lower()
        with self._lock:
            remote = self.remote.get(model)
        return bool(remote and local and remote != local)


def table(rows, gb, installed, chosen, pulls, ollama, updates=()):
    """What Settings > Model shows. installed: the chat models Ollama has, or None when it isn't answering. chosen: the
    model answers come from. pulls: Pulls.view(). ollama: missing, stopped, starting or running. updates: the models
    with a newer version in the registry (Updates.available); an installed row among them gets "update": true.
    Each row's state: in_use, installed, downloading, too_large, not_available (a placeholder row) or not_installed."""
    have = {base(m) for m in installed or []}
    rec = recommended(rows, gb)
    out = []
    for row in rows:
        job = pulls.get(row["model"]) or {}
        if job.get("state") == "downloading":
            state = "downloading"
        elif base(row["model"]) in have:
            state = "in_use" if base(row["model"]) == base(chosen) else "installed"
        elif gb is not None and row["memory_gb"] > gb:
            state = "too_large"
        elif not row["available"]:
            state = "not_available"
        else:
            state = "not_installed"
        out.append({**row, "recommended": row["model"] == rec, "state": state,
                    "update": state in ("in_use", "installed") and row["model"] in updates,
                    "download": {k: job[k] for k in ("state", "completed", "total", "message")} if job else None})
    return {"memory_gb": gb, "ollama": ollama, "rows": out, "chosen": chosen,
            "model_available": bool(chosen) and base(chosen) in have}
