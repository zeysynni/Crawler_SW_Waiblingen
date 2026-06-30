"""Tests for deterministic FAQ/file extraction (the pure HTML parsers)."""

import json

import enrich
from enrich import extract_expandable_qas, extract_pdf_files, extract_prose_sections

ACCORDION_HTML = """
<div class="accordion accordion-flush">
  <div class="accordion-item">
    <h3 class="accordion-header"><button class="accordion-button collapsed">Was ist eine kWh?</button></h3>
    <div class="accordion-collapse collapse"><div class="accordion-body">Eine Kilowattstunde ist eine Einheit der Energie.</div></div>
  </div>
  <div class="accordion-item">
    <h3 class="accordion-header"><button class="accordion-button collapsed">Was ist die Stromsteuer?</button></h3>
    <div class="accordion-collapse collapse"><div class="accordion-body">Eine Abgabe an den Staat.</div></div>
  </div>
</div>
"""


def test_extract_expandable_qas_gets_every_pair():
    qas = extract_expandable_qas(ACCORDION_HTML)
    assert len(qas) == 2
    assert qas[0] == {"question": "Was ist eine kWh?",
                      "answer": "Eine Kilowattstunde ist eine Einheit der Energie."}
    assert qas[1]["question"] == "Was ist die Stromsteuer?"


def test_extract_expandable_qas_skips_empty_or_echoed():
    html = """
    <div class="accordion-item">
      <h3><button class="accordion-button">Nur Titel</button></h3>
      <div class="accordion-collapse"></div>
    </div>
    <div class="accordion-item">
      <h3><button class="accordion-button">Echo</button></h3>
      <div class="accordion-collapse">Echo</div>
    </div>
    """
    assert extract_expandable_qas(html) == []


def test_extract_expandable_qas_empty_when_no_accordion():
    assert extract_expandable_qas("<p>no accordions here</p>") == []


def test_extract_expandable_qas_handles_native_details():
    html = """
    <details><summary>Wie kündige ich?</summary><p>Schriftlich per Brief oder E-Mail.</p></details>
    """
    qas = extract_expandable_qas(html)
    assert qas == [{"question": "Wie kündige ich?", "answer": "Schriftlich per Brief oder E-Mail."}]


def test_extract_pdf_files_skips_empty_text_and_dedupes():
    html = """
    <a href="/files/Preisblatt%202024.pdf">Preisblatt 2024 (PDF | 92 KB)</a>
    <a href="/files/Ghost.pdf"></a>
    <a href="/files/Preisblatt%202024.pdf">Preisblatt 2024 (PDF | 92 KB)</a>
    <a href="/page">not a pdf</a>
    """
    # empty-text ghost link + duplicate + non-pdf all excluded
    assert extract_pdf_files(html) == ["Preisblatt 2024 (PDF | 92 KB)"]


def test_enrich_topic_dedups_bare_question_lines(tmp_path, monkeypatch):
    # The agent listed a question as bare text (no answer); the deterministic
    # accordion has it with an answer. After enrichment the question must appear
    # ONCE (in the FAQ block), not duplicated in the bare text.
    html = (
        '<div class="accordion-item">'
        '<button class="accordion-button">Warum ist das wichtig?</button>'
        '<div class="accordion-collapse">Weil es so ist.</div></div>'
    )
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: html)

    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Info", "segments": [
            {"subheading": "FAQ", "text": "Warum ist das wichtig?\nEin anderer Satz."}
        ]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blob = (tmp_path / "t.json").read_text(encoding="utf-8")

    assert blob.count("Warum ist das wichtig?") == 1   # deduped
    assert "Weil es so ist." in blob                   # answer captured
    assert "Ein anderer Satz." in blob                 # unrelated text preserved


def test_extract_prose_sections_skips_accordion_sections():
    html = """
    <h2>Intro</h2><p>Willkommen text.</p>
    <h2>FAQ</h2><div class="accordion-item">
      <button class="accordion-button">Q?</button>
      <div class="accordion-collapse">A.</div></div>
    <h2>Trotz Umzug Kunde bleiben</h2><p>Bleiben Sie Kunde.</p>
    """
    titles = [s["heading"] for s in extract_prose_sections(html)]
    assert "Intro" in titles
    assert "Trotz Umzug Kunde bleiben" in titles
    assert "FAQ" not in titles   # accordion section is left to FAQ extraction


def test_enrich_topic_recovers_a_missed_section(tmp_path, monkeypatch):
    html = (
        "<h2>Intro</h2><p>hi</p>"
        "<h2>Trotz Umzug Kunde bleiben</h2>"
        "<p>Bleiben Sie unser Kunde, es lohnt sich sehr fuer Sie.</p>"
    )
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: html)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Intro", "segments": [{"text": "hi"}]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blob = (tmp_path / "t.json").read_text(encoding="utf-8")
    assert "Trotz Umzug Kunde bleiben" in blob          # missed section recovered
    assert "Bleiben Sie unser Kunde" in blob


def test_enrich_topic_skips_link_only_and_empty_sections(tmp_path, monkeypatch):
    html = (
        "<h2>Intro</h2><p>hi</p>"
        "<h2>Echte Info</h2><p>Dies ist ein erklaerender Satz mit genug Inhalt fuer uns.</p>"
        "<h2>Weiterfuehrende Informationen</h2><p>Anmeldung/Einzug Abmeldung/Auszug</p>"
        "<h2>Downloadable files</h2>"
    )
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: html)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Intro", "segments": [{"text": "hi"}]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blob = (tmp_path / "t.json").read_text(encoding="utf-8")
    assert "Echte Info" in blob                         # real prose recovered
    assert "Weiterfuehrende Informationen" not in blob   # link-label list skipped
    assert "Downloadable files" not in blob              # empty section skipped


def test_extract_expandable_qas_renders_table_in_answer():
    html = """
    <div class="accordion-item">
      <button class="accordion-button">Tarife Freibäder</button>
      <div class="accordion-collapse">
        <table>
          <tr><th>Leistung</th><th>Preis</th></tr>
          <tr><td>Erwachsener (ab 17 Jahre)</td><td>5,00 Euro</td></tr>
          <tr><td>Kind (6 bis 16 Jahre)</td><td>2,00 Euro</td></tr>
        </table>
      </div>
    </div>
    """
    qas = extract_expandable_qas(html)
    assert len(qas) == 1
    answer = qas[0]["answer"]
    assert "| Leistung | Preis |" in answer
    assert "| --- | --- |" in answer
    assert "| Erwachsener (ab 17 Jahre) | 5,00 Euro |" in answer


def test_page_title_strips_site_suffix():
    assert enrich._page_title("<title>Öko-Stromtarif | Stadtwerke Waiblingen</title>") == "Öko-Stromtarif"
    assert enrich._page_title("<h1>Nur Überschrift</h1>") == "Nur Überschrift"


def test_enrich_topic_files_are_deterministic_only(tmp_path, monkeypatch):
    # Agent listed a (possibly wrong) file inline; deterministic extraction is
    # authoritative -> agent files dropped, one Downloads block with real files.
    html = '<h2>Wärmestrom</h2><a href="/x/Echt%202026.pdf">Echt 2026 (PDF)</a>'
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: html)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Wärmestrom", "segments": [{"text": "info", "files": "Ghost 2025.pdf"}]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blob = (tmp_path / "t.json").read_text(encoding="utf-8")
    assert "Ghost 2025.pdf" not in blob       # agent's file stripped
    assert "Echt 2026 (PDF)" in blob          # deterministic file present
