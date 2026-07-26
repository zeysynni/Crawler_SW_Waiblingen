"""Upload crawl output (`outputs/clean/*.md`) to the knowledge-base API.

Runs after a crawl. The knowledge base itself is the source of truth: before
uploading, we **list** what is already there (`GET .../files`) and key it by
filename. For each page we do a **replace** — delete every remote file with
that filename, then upload the fresh `.md`. Pages whose filename is no longer
produced locally (removed/renamed in the site YAML) are pruned.

This is deliberately **stateless**: there is no local `file_id` registry to
keep in sync (an earlier design cached one in `upload_state.json`; when CI lost
that cache the deletes stopped happening and the KB accumulated duplicates).
Reconciling against the live list each run is self-healing — a lost cache, a
manual KB edit, or an interrupted previous run all get corrected on the next
run. The cost is that every page is re-uploaded every run (no content-diff
skip); at this crawl's size/cadence that is cheap and worth the robustness.

Failure policy (per the deployment plan): retry each list/delete/upload once;
if it still fails, raise `UploadHold`. `main.py` exits non-zero so a scheduler
(GitLab) re-runs ~24h later — and because we reconcile against the live list,
the resumed run simply finishes whatever was left.
"""

import logging
import os
from collections import defaultdict
from pathlib import Path

import requests

log = logging.getLogger("crawler")

CLEAN_DIR = Path("outputs/clean")

# --- API config (IDs are not secret; the key is, and comes from the env) ---
KNOWLEDGE_BASE_ID = os.getenv("AIGATEWAY_KB_ID", "eb1137ce-8fda-4048-818f-a7dc0edcc9f3")
IMPORT_STRATEGY_ID = os.getenv("AIGATEWAY_IMPORT_STRATEGY_ID", "df561ba3-7001-4eb7-8f94-b50872c9f9fa")
_BASE = "https://aigateway.eu/api/knowledge/base"
FILES_URL = f"{_BASE}/v2/knowledgebases/{KNOWLEDGE_BASE_ID}/files"   # GET (list) / POST (upload)
DELETE_URL = f"{_BASE}/v1/knowledgebases/{KNOWLEDGE_BASE_ID}/files"  # + /{file_id}

REQUEST_TIMEOUT = 120
LIST_PAGE_SIZE = 100


class UploadHold(Exception):
    """A list/delete/upload failed twice — hold the run so a scheduler resumes later."""


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


# --- API calls ---------------------------------------------------------------

def _headers() -> dict:
    key = os.getenv("AIGATEWAY_KEY")
    if not key:
        raise UploadHold("AIGATEWAY_KEY not set — cannot upload")
    return {"Authorization": f"Bearer {key}"}


def _list_remote_page(page: int) -> dict:
    resp = requests.get(FILES_URL, headers=_headers(),
                        params={"page": page, "size": LIST_PAGE_SIZE}, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"list files (page {page}) failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def list_remote_files() -> dict[str, list[str]]:
    """Return `{filename: [file_id, ...]}` for every file in the KB right now.

    Paginated (0-indexed, `size` per page). A filename maps to a *list* because
    a broken past run may have left duplicates — replace/prune delete them all.
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    page, total = 0, None
    while True:
        body = _with_retry(_list_remote_page, page)
        for f in body.get("files", []):
            fid = f["file_id"]
            if fid not in seen:                 # guard against page-overlap dupes
                seen.add(fid)
                by_name[f["filename"]].append(fid)
        if total is None:
            total = (body.get("pagination") or {}).get("total_items")
        if not body.get("files") or (total is not None and len(seen) >= total):
            break
        page += 1
    return dict(by_name)


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
        resp = requests.post(FILES_URL, headers=_headers(), files=files, data=data,
                             timeout=REQUEST_TIMEOUT)
    if resp.status_code != 201:
        raise RuntimeError(f"upload {md_path.name} failed: {resp.status_code} {resp.text[:200]}")
    uploaded = resp.json().get("uploaded_files", [])
    if not uploaded:
        raise RuntimeError(f"upload {md_path.name}: no file_id in response {resp.text[:200]}")
    return uploaded[0]["file_id"]


def _with_retry(fn, *args):
    """Run `fn(*args)`; on failure retry once, then raise UploadHold."""
    name = getattr(fn, "__name__", fn)
    try:
        return fn(*args)
    except UploadHold:
        raise                                  # missing key etc. — no point retrying
    except Exception as e:
        log.warning("%s failed (%s); retrying once", name, e)
        try:
            return fn(*args)
        except Exception as e2:
            raise UploadHold(f"{name} failed twice: {e2}") from e2


# --- orchestration -----------------------------------------------------------

def _delete_all(name: str, remote: dict[str, list[str]]) -> None:
    """Delete every remote file currently registered under `name`, and forget
    them in `remote` so a later hold never re-deletes a gone file."""
    for fid in remote.get(name, []):
        _with_retry(_delete_remote, fid)
    remote[name] = []


def replace_upload(page: str, remote: dict[str, list[str]],
                   output_dir: Path | str = CLEAN_DIR) -> str:
    """Replace a page's remote file(s): delete every existing copy by filename,
    upload the fresh `.md`, update `remote` in place. Returns the new file_id."""
    md_path = Path(output_dir) / f"{page}.md"
    params = chunk_params_for(md_path.read_text(encoding="utf-8", errors="ignore"))
    _delete_all(md_path.name, remote)
    file_id = _with_retry(_upload_remote, md_path, params)
    remote[md_path.name] = [file_id]
    return file_id


def prune_stale(pages: list[str], remote: dict[str, list[str]]) -> list[str]:
    """Delete remote files whose filename is no longer produced locally
    (renamed/removed in the site YAML). Mutates `remote`; returns pruned names."""
    current = {f"{page}.md" for page in pages}
    pruned = []
    for name in [n for n in remote if n not in current]:
        _delete_all(name, remote)
        del remote[name]
        pruned.append(name)
        log.info("pruned stale remote file %s", name)
    return pruned


def upload_pages(pages: list[str], output_dir: Path | str = CLEAN_DIR,
                 prune: bool = True) -> dict:
    """Reconcile the KB with the local clean output. Lists the live KB once,
    prunes remote files that no longer exist locally, then replaces every
    page's file. Pass `prune=False` for partial runs (a section subset) —
    otherwise every page absent from the subset would be deleted remotely.
    Raises UploadHold on a double failure — the caller should exit and let a
    scheduler resume (a resumed run simply reconciles again). Returns a summary.
    """
    remote = list_remote_files()
    uploaded, pruned = [], []
    if prune:
        pruned = prune_stale(pages, remote)
    for page in pages:
        md_path = Path(output_dir) / f"{page}.md"
        if not md_path.exists():
            log.warning("upload: %s not found, skipping", md_path)
            continue
        fid = replace_upload(page, remote, output_dir)
        log.info("uploaded %s -> %s", md_path.name, fid)
        uploaded.append(page)
    return {"uploaded": uploaded, "pruned": pruned}
