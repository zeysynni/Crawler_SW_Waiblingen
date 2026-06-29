"""Tests for deterministic FAQ/file extraction (the pure HTML parsers)."""

from enrich import extract_expandable_qas, extract_pdf_files

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


def test_extract_pdf_files_uses_link_text_and_dedupes():
    html = """
    <a href="/files/Preisblatt%202024.pdf">Preisblatt 2024 (PDF | 92 KB)</a>
    <a href="/files/AGB.pdf"></a>
    <a href="/files/Preisblatt%202024.pdf">Preisblatt 2024 (PDF | 92 KB)</a>
    <a href="/page">not a pdf</a>
    """
    files = extract_pdf_files(html)
    assert "Preisblatt 2024 (PDF | 92 KB)" in files
    assert "AGB.pdf" in files           # falls back to filename when link text is empty
    assert len(files) == 2              # deduped, non-pdf ignored
