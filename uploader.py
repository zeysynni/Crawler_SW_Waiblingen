"""Upload crawl output (`outputs/*.md`) to the knowledge-base API.

Runs after a crawl. For each topic it does a **replace**: delete the previously
uploaded file (by the `file_id` we stored last time) and upload the fresh `.md`,
then persist the new `file_id`. State lives in a keyed JSON map so the delete
target is always known:

    { "<topic>.md": {file_id, sha256, chunk_params, uploaded_at} }

Chunking params are chosen **per file** from its structure (the API's default
strategy accepts per-file params) so logical units — FAQ answers, tables, whole
`##` sections — aren't split mid-way.

Failure policy (per the deployment plan): retry each delete/upload once; if it
still fails, raise `UploadHold`. `main.py` persists state and exits non-zero so a
scheduler (GitLab) re-runs ~24h later and resumes only the still-pending topics
(unchanged, already-uploaded files are skipped via their sha256).
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline import OUTPUT_DIR

log = logging.getLogger("crawler")

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

def _section_lengths(md_text: str) -> list[int]:
    """Char lengths of the doc's logical units — split at Markdown headings and
    bold FAQ questions (the boundaries our `.md` uses)."""
    sections, cur = [], []
    for line in md_text.splitlines():
        s = line.strip()
        if re.match(r"^#{1,6}\s", line) or re.fullmatch(r"\*\*.+\*\*", s):
            if cur:
                sections.append("\n".join(cur))
                cur = []
        cur.append(line)
    if cur:
        sections.append("\n".join(cur))
    return [len(s) for s in sections if s.strip()]


def chunk_params_for(md_text: str) -> dict:
    """Per-file chunking sized to keep logical units (FAQ answers, tables, sections)
    whole. Units are mostly short with a long tail, so we size to the ~95th
    percentile unit — big enough to hold nearly all of them intact — clamped to
    [800, 2000] chars, with ~10% overlap to bridge the rare unit that still
    exceeds the cap. Gives genuine per-file variation (short contact pages ~800,
    prose/FAQ-heavy pages up to 2000)."""
    lengths = sorted(_section_lengths(md_text) or [len(md_text)])
    p95 = lengths[min(len(lengths) - 1, int(0.95 * len(lengths)))]
    max_chars = max(800, min(2000, round(p95 / 100) * 100 or 800))
    overlap = max(50, min(200, round(max_chars * 0.1 / 50) * 50))
    return {"max_characters": max_chars, "new_after_n_chars": max_chars, "overlap": overlap}


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


def replace_upload(topic: str, state: dict, output_dir: Path | str = OUTPUT_DIR) -> str:
    """Replace a topic's remote file: delete the old one (if any), upload the new
    `.md`, update `state` in place. Returns the new file_id."""
    md_path = Path(output_dir) / f"{topic}.md"
    md_bytes = md_path.read_bytes()
    params = chunk_params_for(md_bytes.decode("utf-8", "ignore"))

    old = state.get(md_path.name, {}).get("file_id")
    if old:
        _with_retry(_delete_remote, old)

    file_id = _with_retry(_upload_remote, md_path, params)
    state[md_path.name] = {"file_id": file_id, "sha256": _sha256(md_bytes),
                           "chunk_params": params, "uploaded_at": _now()}
    return file_id


def upload_topics(topics: list[str], output_dir: Path | str = OUTPUT_DIR,
                  state_path: Path = STATE_FILE) -> dict:
    """Upload each topic's `.md` (replace semantics), skipping ones whose content
    is unchanged since the last successful upload. State is saved after every
    change so a mid-run hold loses nothing. Raises UploadHold on a double failure
    (state already saved) — the caller should exit and let a scheduler resume.
    Returns a summary dict.
    """
    state = load_state(state_path)
    uploaded, skipped = [], []
    for topic in topics:
        md_path = Path(output_dir) / f"{topic}.md"
        if not md_path.exists():
            log.warning("upload: %s not found, skipping", md_path)
            continue
        sha = _sha256(md_path.read_bytes())
        if state.get(md_path.name, {}).get("sha256") == sha:
            skipped.append(topic)          # unchanged + already uploaded
            continue
        try:
            fid = replace_upload(topic, state, output_dir)
        except UploadHold:
            save_state(state, state_path)  # persist progress before holding
            raise
        save_state(state, state_path)      # persist after each success
        log.info("uploaded %s -> %s", md_path.name, fid)
        uploaded.append(topic)
    return {"uploaded": uploaded, "skipped": skipped}
