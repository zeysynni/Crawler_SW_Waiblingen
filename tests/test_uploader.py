"""Tests for the knowledge-base uploader — pure logic + mocked HTTP.

No real network: `requests` is monkeypatched. The uploader is stateless — it
reconciles against the live KB listing each run — so the fakes here model a
mutable in-memory "remote KB": a GET lists it, POST adds a file, DELETE removes
one. Covers one-chunk-per-file params, replace (delete-by-name-then-upload),
duplicate cleanup, pruning of stale remote files, and the retry→hold policy.
"""

import json

import pytest

import uploader
from uploader import UploadHold, chunk_params_for


def test_chunk_params_one_chunk_no_overlap():
    text = "# H\n\n" + "x" * 5000
    params = chunk_params_for(text)
    assert params["max_characters"] == len(text)     # whole file = one chunk
    assert params["new_after_n_chars"] == len(text)
    assert params["overlap"] == 0

    assert chunk_params_for("")["max_characters"] == 1   # floor for empty files

    big = chunk_params_for("y" * 20_000)                 # API caps at 8192 (422 above)
    assert big["max_characters"] == uploader.MAX_CHUNK   # must split ...
    assert big["overlap"] == uploader.SPLIT_OVERLAP      # ... then bridge the cuts


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _FakeKB:
    """A tiny in-memory stand-in for the remote KB, wired to requests.{get,post,delete}."""

    def __init__(self, initial=None):
        # {file_id: filename}
        self.files = dict(initial or {})
        self._next = 0
        self.calls = []   # ("get"|"post"|"delete", detail)

    def install(self, monkeypatch):
        monkeypatch.setenv("AIGATEWAY_KEY", "test-key")
        monkeypatch.setattr(uploader.requests, "get", self._get)
        monkeypatch.setattr(uploader.requests, "post", self._post)
        monkeypatch.setattr(uploader.requests, "delete", self._delete)

    def _get(self, url, headers, params, timeout):
        self.calls.append(("get", params.get("page")))
        items = [{"file_id": fid, "filename": fn} for fid, fn in self.files.items()]
        size = params["size"]
        page = params["page"]
        chunk = items[page * size:(page + 1) * size]
        return _Resp(200, {"files": chunk, "pagination": {"total_items": len(items)}})

    def _post(self, url, headers, files, data, timeout):
        name = files["uploaded_files"][0]
        self.calls.append(("post", name))
        self._next += 1
        fid = f"F{self._next}"
        self.files[fid] = name
        return _Resp(201, {"uploaded_files": [{"file_id": fid, "filename": name}]})

    def _delete(self, url, headers, timeout):
        fid = url.rsplit("/", 1)[-1]
        self.calls.append(("delete", fid))
        self.files.pop(fid, None)
        return _Resp(204)

    def names(self):
        return sorted(self.files.values())


def test_first_upload_no_delete(tmp_path, monkeypatch):
    kb = _FakeKB()
    kb.install(monkeypatch)
    (tmp_path / "t.md").write_text("## Sektion\n\nInhalt hier.", encoding="utf-8")

    res = uploader.upload_pages(["t"], output_dir=tmp_path)
    assert res["uploaded"] == ["t"] and res["pruned"] == []
    assert [c[0] for c in kb.calls if c[0] != "get"] == ["post"]  # nothing to delete first
    assert kb.names() == ["t.md"]


def test_replace_deletes_old_by_name_first(tmp_path, monkeypatch):
    kb = _FakeKB(initial={"OLD": "t.md"})   # a copy already in the KB
    kb.install(monkeypatch)
    (tmp_path / "t.md").write_text("## Sektion\n\nNeuer Inhalt.", encoding="utf-8")

    uploader.upload_pages(["t"], output_dir=tmp_path)
    writes = [c for c in kb.calls if c[0] in ("delete", "post")]
    assert writes[0] == ("delete", "OLD")     # old copy deleted first ...
    assert writes[1][0] == "post"             # ... then the fresh upload
    assert kb.names() == ["t.md"]             # exactly one copy remains


def test_replace_cleans_up_duplicates(tmp_path, monkeypatch):
    kb = _FakeKB(initial={"A": "t.md", "B": "t.md", "C": "t.md"})   # 3 stale copies
    kb.install(monkeypatch)
    (tmp_path / "t.md").write_text("Inhalt.", encoding="utf-8")

    uploader.upload_pages(["t"], output_dir=tmp_path)
    deleted = {c[1] for c in kb.calls if c[0] == "delete"}
    assert deleted == {"A", "B", "C"}         # every prior copy removed
    assert kb.names() == ["t.md"]             # collapsed to a single fresh file


def test_prune_removes_stale_remote_files(tmp_path, monkeypatch):
    kb = _FakeKB(initial={"K": "keep.md", "G": "gone.md"})
    kb.install(monkeypatch)
    (tmp_path / "keep.md").write_text("bleibt", encoding="utf-8")

    res = uploader.upload_pages(["keep"], output_dir=tmp_path)
    assert res["pruned"] == ["gone.md"]
    assert ("delete", "G") in kb.calls        # stale remote file deleted
    assert kb.names() == ["keep.md"]


def test_partial_run_does_not_prune(tmp_path, monkeypatch):
    kb = _FakeKB(initial={"O": "other.md"})
    kb.install(monkeypatch)
    (tmp_path / "keep.md").write_text("bleibt", encoding="utf-8")

    res = uploader.upload_pages(["keep"], output_dir=tmp_path, prune=False)
    assert res["pruned"] == []
    assert "other.md" in kb.names()           # untouched on a subset run


def test_list_remote_files_paginates(monkeypatch):
    kb = _FakeKB(initial={f"F{i}": f"p{i}.md" for i in range(250)})
    kb.install(monkeypatch)
    remote = uploader.list_remote_files()
    assert len(remote) == 250                 # all pages walked, not just the first
    assert all(len(ids) == 1 for ids in remote.values())


def test_upload_retries_once_then_holds(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGATEWAY_KEY", "test-key")
    (tmp_path / "t.md").write_text("## S\n\nInhalt.", encoding="utf-8")
    # empty KB so listing succeeds; the upload POST always fails
    monkeypatch.setattr(uploader.requests, "get",
                        lambda url, headers, params, timeout: _Resp(200, {"files": [], "pagination": {"total_items": 0}}))
    attempts = {"n": 0}

    def always_500(url, headers, files, data, timeout):
        attempts["n"] += 1
        return _Resp(500, {"error": "boom"})

    monkeypatch.setattr(uploader.requests, "post", always_500)
    with pytest.raises(UploadHold):
        uploader.upload_pages(["t"], output_dir=tmp_path)
    assert attempts["n"] == 2   # initial try + one retry, then hold


def test_upload_holds_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("AIGATEWAY_KEY", raising=False)
    (tmp_path / "t.md").write_text("## S\n\nInhalt.", encoding="utf-8")
    with pytest.raises(UploadHold):
        uploader.upload_pages(["t"], output_dir=tmp_path)
