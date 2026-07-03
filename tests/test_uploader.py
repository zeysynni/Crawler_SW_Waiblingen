"""Tests for the knowledge-base uploader — pure logic + mocked HTTP.

No real network: `requests` is monkeypatched. Covers per-file chunk sizing, the
sha-based skip, replace (delete-then-upload) ordering, and the retry→hold policy.
"""

import json

import pytest

import uploader
from uploader import UploadHold, chunk_params_for, _section_lengths


def test_chunk_params_clamped_and_shaped():
    small = chunk_params_for("# H\n\nkurz.")
    assert small["max_characters"] == 800                       # clamped up
    assert small["new_after_n_chars"] == small["max_characters"]
    assert 50 <= small["overlap"] <= 200

    big = chunk_params_for("## H\n\n" + "x" * 5000)              # one huge section
    assert big["max_characters"] == 2000                        # clamped down


def test_section_lengths_splits_on_headings_and_bold_faq():
    md = "## A\nprose a\n**Frage?**\nantwort\n### B\nprose b"
    assert len(_section_lengths(md)) == 3   # A-section, the FAQ Q&A, B-section


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
    res = uploader.upload_topics(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert res["uploaded"] == ["t"]
    assert [c[0] for c in calls] == ["post"]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["t.md"]["file_id"] == "NEW"

    # unchanged content -> skipped, no HTTP
    calls.clear()
    res = uploader.upload_topics(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert res["skipped"] == ["t"] and calls == []

    # changed content -> delete old THEN post new
    calls.clear()
    (tmp_path / "t.md").write_text("## Sektion\n\nAnderer Inhalt jetzt.", encoding="utf-8")
    uploader.upload_topics(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert [c[0] for c in calls] == ["delete", "post"]
    assert calls[0][1] == "NEW"   # deleted the previously stored file_id


def test_upload_retries_once_then_holds(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGATEWAY_KEY", "test-key")
    (tmp_path / "t.md").write_text("## S\n\nInhalt.", encoding="utf-8")
    attempts = {"n": 0}

    def always_500(url, headers, files, data, timeout):
        attempts["n"] += 1
        return _Resp(500, {"error": "boom"})

    monkeypatch.setattr(uploader.requests, "post", always_500)
    with pytest.raises(UploadHold):
        uploader.upload_topics(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
    assert attempts["n"] == 2   # initial try + one retry, then hold


def test_upload_holds_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("AIGATEWAY_KEY", raising=False)
    (tmp_path / "t.md").write_text("## S\n\nInhalt.", encoding="utf-8")
    with pytest.raises(UploadHold):
        uploader.upload_topics(["t"], output_dir=tmp_path, state_path=tmp_path / "state.json")
