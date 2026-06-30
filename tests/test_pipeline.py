"""Tests for the output pipeline: json_to_markdown (pure) and the keep-newest file writers."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline
from pipeline import json_to_markdown, save_json, to_pdf, write_markdown

SAMPLE = {
    "base_url": "https://example.com",
    "pages": [
        {
            "url": "https://example.com/strom",
            "blocks": [
                {
                    "heading": "Stromtarife",
                    "segments": [
                        {"subheading": "Ökostrom", "text": "Grüner Strom für alle."},
                        {"files": "Preisblatt_2024.pdf"},
                        {"contacts": "Hotline: 07151 131-0"},
                        {
                            "faqs": {
                                "title": "Häufige Fragen",
                                "QAs": [
                                    {"question": "Was kostet es?", "answer": "29 ct/kWh."}
                                ],
                            }
                        },
                    ],
                }
            ],
        }
    ],
}


def test_json_to_markdown_renders_all_segment_types():
    md = json_to_markdown(SAMPLE)

    assert "*URL: https://example.com/strom*" in md
    assert "## Stromtarife" in md
    assert "### Ökostrom" in md
    assert "Grüner Strom für alle." in md
    # Content renders without injected labels/titles (no **Dateien:**,
    # **Kontakt:**, or a faqs title — the block heading carries the section).
    assert "Preisblatt_2024.pdf" in md
    assert "Hotline: 07151 131-0" in md
    assert "**Was kostet es?**" in md and "29 ct/kWh." in md
    assert "**Dateien:**" not in md and "**Kontakt:**" not in md


def test_json_to_markdown_handles_empty_input():
    assert json_to_markdown({}) == ""
    assert json_to_markdown({"pages": []}) == ""


def test_json_to_markdown_strips_leading_hashes_no_double_prefix():
    # The model sometimes puts "##"/"###" inside heading/subheading fields;
    # the converter must not produce doubled prefixes like "## ## Service".
    data = {
        "pages": [
            {
                "url": "u",
                "blocks": [
                    {
                        "heading": "## Service",
                        "segments": [{"subheading": "### Kunden-Center", "text": "x"}],
                    }
                ],
            }
        ]
    }
    md = json_to_markdown(data)
    assert "## Service" in md and "## ## Service" not in md
    assert "### Kunden-Center" in md and "## ### Kunden-Center" not in md


def test_save_json_writes_stable_path_and_overwrites(tmp_path):
    # A minimal stand-in for the agent result: result.final_output.model_dump().
    def fake_result(payload):
        return SimpleNamespace(final_output=SimpleNamespace(model_dump=lambda: payload))

    p1 = save_json(fake_result({"pages": [{"url": "v1", "blocks": []}]}), "strom", tmp_path)
    assert p1 == tmp_path / "strom.json"

    # Second crawl of the same topic overwrites — keep only the newest.
    save_json(fake_result({"pages": [{"url": "v2", "blocks": []}]}), "strom", tmp_path)
    assert list(tmp_path.glob("strom*.json")) == [p1]          # exactly one file
    assert json.loads(p1.read_text())["pages"][0]["url"] == "v2"


def test_write_markdown_reads_json_and_overwrites(tmp_path):
    (tmp_path / "strom.json").write_text(json.dumps(SAMPLE), encoding="utf-8")

    md_path = write_markdown("strom", tmp_path)

    assert md_path == tmp_path / "strom.md"
    assert "## Stromtarife" in md_path.read_text(encoding="utf-8")
    # No timestamped clutter — exactly one .md for the topic.
    assert list(tmp_path.glob("strom*.md")) == [md_path]


def test_write_markdown_missing_json_returns_none(tmp_path):
    assert write_markdown("does_not_exist", tmp_path) is None


def test_to_pdf_builds_path_and_invokes_pandoc(tmp_path, monkeypatch):
    # Mock out pandoc: we test our path/argument logic, not the LaTeX toolchain.
    md = tmp_path / "strom.md"
    md.write_text("# Strom\n", encoding="utf-8")

    calls = {}

    def fake_convert_file(source, to, outputfile, extra_args):
        calls.update(source=source, to=to, outputfile=outputfile, extra_args=extra_args)
        Path(outputfile).write_bytes(b"%PDF-1.4 fake")   # pretend pandoc wrote a PDF

    monkeypatch.setattr(pipeline.pypandoc, "convert_file", fake_convert_file)

    out_dir = tmp_path / "pdfs"
    pdf_path = to_pdf(md, out_dir)

    # to_pdf computed the right destination and created the (previously missing) dir.
    assert pdf_path == out_dir / "strom.pdf"
    assert pdf_path.exists()
    # ...and handed pandoc exactly the arguments we expect.
    assert calls["source"] == str(md)
    assert calls["to"] == "pdf"
    assert calls["outputfile"] == str(pdf_path)
    assert calls["extra_args"] == ["--pdf-engine=xelatex"]


def test_to_pdf_missing_markdown_raises(tmp_path):
    # Fails fast before pandoc is ever called — so no mock is needed.
    with pytest.raises(FileNotFoundError, match="nope.md"):
        to_pdf(tmp_path / "nope.md")
