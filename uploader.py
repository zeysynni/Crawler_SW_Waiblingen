"""Upload crawl output (`outputs/clean/*.md`) to the knowledge-base API.

Runs after a crawl. For each page it does a **replace**: delete the previously
uploaded file (by the `file_id` we stored last time) and upload the fresh `.md`,
then persist the new `file_id`. State lives in a keyed JSON map so the delete
target is always known:

    { "<page>.md": {file_id, sha256, chunk_params, uploaded_at} }

Chunking: **one chunk per file, no overlap** — each clean `.md` is one
retrieval unit (one page of the site), so `max_characters` is simply the file
length. Pages whose state key vanished locally (renamed/removed in the site
YAML) are pruned from the KB so the remote mirror never accumulates stale files.

Failure policy (per the deployment plan): retry each delete/upload once; if it
still fails, raise `UploadHold`. `main.py` persists state and exits non-zero so a
scheduler (GitLab) re-runs ~24h later and resumes only the still-pending pages
(unchanged, already-uploaded files are skipped via their sha256).
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger("crawler")

CLEAN_DIR = Path("outputs/clean")

# --- API config (IDs are not secret; the key is, and comes from the env) ---
KNOWLEDGE_BASE_ID = os.getenv("AIGATEWAY_KB_ID", "eb1137ce-8fda-4048-818f-a7dc0edcc9f3")
IMPORT_STRATEGY_ID = os.getenv("AIGATEWAY_IMPORT_STRATEGY_ID", "df561ba3-7001-4eb7-8f94-b50872c9f9fa")
_BASE = "https://aigateway.eu/api/knowledge/base"
UPLOAD_URL = f"{_BASE}/v2/knowledgebases/{KNOWLEDGE_BASE_ID}/files"
DELETE_URL = f"{_BASE}/v1/knowledgebases/{KNOWLEDGE_BASE_ID}/files"  # + /{file_id}

STATE_FILE = Path(os.getenv("UPLOAD_STATE_FILE", "upload_state.json"))
REQUEST_TIMEOUT = 120


class UploadHold(Exception):
    """A delete/upload failed twice — hold the run so a scheduler resumes later."""


# --- per-file chunking -------------------------------------------------------

MAX_CHUNK = 8192      # hard API limit (422 above it) — verified 2026-07-07
SPLIT_OVERLAP = 1000  # overlap between chunks of files that must split


def chunk_params_for(md_text: str) -> dict:
    """One chunk per file: each clean `.md` is one page of the site and stays
    whole as a single retrieval unit (chunk size = file length, floor 1,
    overlap 0). Files above the API's MAX_CHUNK cap can't stay whole — the
    API splits them at structural boundaries (~4 of 62 pages), so those get
    SPLIT_OVERLAP so context bridges the cut."""
    if len(md_text) <= MAX_CHUNK:
        n = max(1, len(md_text))
        return {"max_characters": n, "new_after_n_chars": n, "overlap": 0}
    return {"max_characters": MAX_CHUNK, "new_after_n_chars": MAX_CHUNK,
            "overlap": SPLIT_OVERLAP}


# --- state -------------------------------------------------------------------

def load_state(path: Path = STATE_FILE) -> dict:
    if Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    Path(path).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- API calls ---------------------------------------------------------------

def _headers() -> dict:
    key = os.getenv("AIGATEWAY_KEY")
    if not key:
        raise UploadHold("AIGATEWAY_KEY not set — cannot upload")
    return {"Authorization": f"Bearer {key}"}


def _delete_remote(file_id: str) -> None:
    """Delete a remote file. A 404 (already gone) is treated as success."""
    resp = requests.delete(f"{DELETE_URL}/{file_id}", headers=_headers(), timeout=REQUEST_TIMEOUT)
    if resp.status_code not in (200, 204, 404):
        raise RuntimeError(f"delete {file_id} failed: {resp.status_code} {resp.text[:200]}")


def _upload_remote(md_path: Path, params: dict) -> str:
    """Upload one `.md`; return its new remote file_id."""
    with open(md_path, "rb") as fh:
        files = {"uploaded_files": (md_path.name, fh, "text/markdown")}
        data = {"import_strategy_id": IMPORT_STRATEGY_ID, **params}
        resp = requests.post(UPLOAD_URL, headers=_headers(), files=files, data=data,
                             timeout=REQUEST_TIMEOUT)
    if resp.status_code != 201:
        raise RuntimeError(f"upload {md_path.name} failed: {resp.status_code} {resp.text[:200]}")
    uploaded = resp.json().get("uploaded_files", [])
    if not uploaded:
        raise RuntimeError(f"upload {md_path.name}: no file_id in response {resp.text[:200]}")
    return uploaded[0]["file_id"]


def _with_retry(fn, *args):
    """Run `fn(*args)`; on failure retry once, then raise UploadHold."""
    try:
        return fn(*args)
    except Exception as e:
        log.warning("%s failed (%s); retrying once", getattr(fn, "__name__", fn), e)
        try:
            return fn(*args)
        except Exception as e2:
            raise UploadHold(f"{getattr(fn, '__name__', fn)} failed twice: {e2}") from e2


# --- orchestration -----------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def replace_upload(page: str, state: dict, output_dir: Path | str = CLEAN_DIR) -> str:
    """Replace a page's remote file: delete the old one (if any), upload the new
    `.md`, update `state` in place. Returns the new file_id."""
    md_path = Path(output_dir) / f"{page}.md"
    md_bytes = md_path.read_bytes()
    params = chunk_params_for(md_bytes.decode("utf-8", "ignore"))

    old = state.get(md_path.name, {}).get("file_id")
    if old:
        _with_retry(_delete_remote, old)

    file_id = _with_retry(_upload_remote, md_path, params)
    state[md_path.name] = {"file_id": file_id, "sha256": _sha256(md_bytes),
                           "chunk_params": params, "uploaded_at": _now()}
    return file_id


def prune_stale(pages: list[str], state: dict) -> list[str]:
    """Delete remote files whose local page no longer exists (renamed/removed
    in the site YAML). Mutates `state`; returns the pruned names."""
    current = {f"{page}.md" for page in pages}
    pruned = []
    for key in [k for k in state if k not in current]:
        old = state[key].get("file_id")
        if old:
            _with_retry(_delete_remote, old)
        del state[key]
        pruned.append(key)
        log.info("pruned stale remote file %s", key)
    return pruned


def upload_pages(pages: list[str], output_dir: Path | str = CLEAN_DIR,
                 state_path: Path = STATE_FILE, prune: bool = True) -> dict:
    """Upload each page's `.md` (replace semantics), skipping ones whose content
    is unchanged since the last successful upload, and pruning remote files
    that no longer exist locally. Pass `prune=False` for partial runs (a
    section subset) — otherwise every page absent from the subset would be
    deleted remotely. State is saved after every change so a mid-run hold
    loses nothing. Raises UploadHold on a double failure (state already
    saved) — the caller should exit and let a scheduler resume.
    Returns a summary dict.
    """
    state = load_state(state_path)
    uploaded, skipped, pruned = [], [], []
    if prune:
        try:
            pruned = prune_stale(pages, state)
        finally:
            save_state(state, state_path)
    for page in pages:
        md_path = Path(output_dir) / f"{page}.md"
        if not md_path.exists():
            log.warning("upload: %s not found, skipping", md_path)
            continue
        md_bytes = md_path.read_bytes()
        prev = state.get(md_path.name, {})
        # skip only if the content AND the chunking are what's already uploaded
        if (prev.get("sha256") == _sha256(md_bytes)
                and prev.get("chunk_params") == chunk_params_for(md_bytes.decode("utf-8", "ignore"))):
            skipped.append(page)           # unchanged + already uploaded
            continue
        try:
            fid = replace_upload(page, state, output_dir)
        except UploadHold:
            save_state(state, state_path)  # persist progress before holding
            raise
        save_state(state, state_path)      # persist after each success
        log.info("uploaded %s -> %s", md_path.name, fid)
        uploaded.append(page)
    return {"uploaded": uploaded, "skipped": skipped, "pruned": pruned}
