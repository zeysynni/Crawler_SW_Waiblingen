"""Tests for the knowledge-base uploader — pure logic + mocked HTTP.

No real network: `requests` is monkeypatched. Covers one-chunk-per-file
params, the sha-based skip, replace (delete-then-upload) ordering, pruning of
stale remote files, and the retry→hold policy.
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


def test_changed_chunk_params_trigger_reupload(tmp_path, monkeypatch):
    calls = []
    _setup(monkeypatch, tmp_path, calls)
    (tmp_path / "t.md").write_text("Inhalt.", encoding="utf-8")
    state_path = tmp_path / "state.json"

    uploader.upload_pages(["t"], output_dir=tmp_path, state_path=state_path)
    state = json.loads(state_path.read_text())
    state["t.md"]["chunk_params"]["overlap"] = 99        # stale params from an old run
    state_path.write_text(json.dumps(state), encoding="utf-8")

    calls.clear()
    res = uploader.upload_pages(["t"], output_dir=tmp_path, state_path=state_path)
    assert res["uploaded"] == ["t"]                      # same sha, but re-uploaded


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def _setup(monkeypatch, tmp_path, calls):
    monkeypatch.setenv("AIGATEWAY_KEY", "test-key")
    monkeypatch.setattr(uploader, "STATE_FILE", tmp_path / "state.json")

    def fake_post(url, headers, files, data, timeout):
        calls.append(("post", data.get("max_characters")))
        return _Resp(201, {"uploaded_files": [{"file_id": "NEW", "filename": "t.md"}]})

    def fake_delete(url, headers, timeout):
        calls.append(("delete", url.rsplit("/", 1)[-1]))
        return _Resp(204)

    monkeypatch.setattr(uploader.requests, "post", fake_post)
    monkeypatch.setattr(uploader.requests, "delete", fake_delete)


def test_upload_then_replace_deletes_old_first(tmp_path, monkeypatch):
    calls = []
    _setup(monkeypatch, tmp_path, calls)
    (tmp_path / "t.md").write_text("## Sektion\n\nInhalt hier.", encoding="utf-8")

    # first upload: no prior file_id -> just a post, state records NEW
    res = uploader.upload_pages(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert res["uploaded"] == ["t"]
    assert [c[0] for c in calls] == ["post"]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["t.md"]["file_id"] == "NEW"
    assert state["t.md"]["chunk_params"]["overlap"] == 0

    # unchanged content -> skipped, no HTTP
    calls.clear()
    res = uploader.upload_pages(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert res["skipped"] == ["t"] and calls == []

    # changed content -> delete old THEN post new
    calls.clear()
    (tmp_path / "t.md").write_text("## Sektion\n\nAnderer Inhalt jetzt.", encoding="utf-8")
    uploader.upload_pages(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert [c[0] for c in calls] == ["delete", "post"]
    assert calls[0][1] == "NEW"   # deleted the previously stored file_id


def test_prune_removes_stale_remote_files(tmp_path, monkeypatch):
    calls = []
    _setup(monkeypatch, tmp_path, calls)
    (tmp_path / "keep.md").write_text("bleibt", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "keep.md": {"file_id": "K", "sha256": "stale"},
        "gone.md": {"file_id": "G", "sha256": "x"},
    }), encoding="utf-8")

    res = uploader.upload_pages(["keep"], output_dir=tmp_path, state_path=state_path)
    assert res["pruned"] == ["gone.md"]
    assert ("delete", "G") in calls                       # stale remote file deleted
    state = json.loads(state_path.read_text())
    assert "gone.md" not in state and "keep.md" in state


def test_partial_run_does_not_prune(tmp_path, monkeypatch):
    calls = []
    _setup(monkeypatch, tmp_path, calls)
    (tmp_path / "keep.md").write_text("bleibt", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"other.md": {"file_id": "O", "sha256": "x"}}),
                          encoding="utf-8")

    res = uploader.upload_pages(["keep"], output_dir=tmp_path, state_path=state_path,
                                prune=False)
    assert res["pruned"] == []
    assert "other.md" in json.loads(state_path.read_text())


def test_upload_retries_once_then_holds(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGATEWAY_KEY", "test-key")
    (tmp_path / "t.md").write_text("## S\n\nInhalt.", encoding="utf-8")
    attempts = {"n": 0}

    def always_500(url, headers, files, data, timeout):
        attempts["n"] += 1
        return _Resp(500, {"error": "boom"})

    monkeypatch.setattr(uploader.requests, "post", always_500)
    with pytest.raises(UploadHold):
        uploader.upload_pages(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert attempts["n"] == 2   # initial try + one retry, then hold


def test_upload_holds_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("AIGATEWAY_KEY", raising=False)
    (tmp_path / "t.md").write_text("## S\n\nInhalt.", encoding="utf-8")
    with pytest.raises(UploadHold):
        uploader.upload_pages(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
