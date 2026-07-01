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


SUBTOPIC_HTML = """
<a href="/Privatkunden/Strom/oekostrom">Ökostromtarif</a>
<a href="/Privatkunden/Strom/Waermestrom">Wärmestrom Info</a>
<a href="https://external.example/other">Etwas anderes</a>
"""


def test_resolve_subtopics_matches_exact_and_substring(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: SUBTOPIC_HTML)
    out = enrich.resolve_subtopics("https://host.de/Privatkunden/Strom",
                                   ["Ökostromtarif", "Wärmestrom"])
    assert out == [
        {"label": "Ökostromtarif", "url": "https://host.de/Privatkunden/Strom/oekostrom"},
        {"label": "Wärmestrom", "url": "https://host.de/Privatkunden/Strom/Waermestrom"},
    ]


def test_resolve_subtopics_prefers_content_link_over_short_nav(monkeypatch):
    # A short "Wärme" nav link points at the base page; the real "Fernwärme"
    # content link must win, not the substring "Wärme"⊂"Fernwärme" match.
    html = """
    <a href="/Privatkunden/Waerme">Wärme</a>
    <a href="/Privatkunden/Waerme/Fernwaerme">Fernwärme Bedarfsgerecht und günstig</a>
    """
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: html)
    out = enrich.resolve_subtopics("https://host.de/Privatkunden/Waerme", ["Fernwärme"])
    assert out == [{"label": "Fernwärme",
                    "url": "https://host.de/Privatkunden/Waerme/Fernwaerme"}]


def test_resolve_subtopics_skips_unmatched(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: SUBTOPIC_HTML)
    out = enrich.resolve_subtopics("https://host.de/x", ["Ökostromtarif", "Gibtsnicht"])
    assert [s["label"] for s in out] == ["Ökostromtarif"]


def test_resolve_subtopics_empty_on_fetch_failure(monkeypatch):
    def boom(url, timeout=30):
        raise OSError("network down")
    monkeypatch.setattr(enrich, "fetch_html", boom)
    assert enrich.resolve_subtopics("https://host.de/x", ["A"]) == []


FILE_GROUPS_HTML = """
<h2>Downloads Strom Grundversorgung</h2>
<a href="/x/Preisblatt%202025.pdf">Preisblatt 2025 (PDF)</a>
<a href="/x/AGB.pdf">AGB (PDF)</a>
<h3>Ersatzversorgung</h3>
<a href="/x/Ersatz%202026.pdf">Ersatz 2026 (PDF)</a>
"""


def test_extract_file_groups_groups_by_heading():
    groups = enrich.extract_file_groups(FILE_GROUPS_HTML)
    assert groups == [
        {"heading": "Downloads Strom Grundversorgung",
         "files": ["Preisblatt 2025 (PDF)", "AGB (PDF)"]},
        {"heading": "Ersatzversorgung", "files": ["Ersatz 2026 (PDF)"]},
    ]


def test_enrich_topic_keeps_files_under_their_heading(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: FILE_GROUPS_HTML)
    # Agent captured the "Ersatzversorgung" subtitle but not the Downloads section.
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Strom", "segments": [{"subheading": "Ersatzversorgung", "text": "info"}]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    page = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["pages"][0]

    # Ersatzversorgung PDF sits in the existing block, right after its subheading.
    strom = page["blocks"][0]
    assert any(s.get("files") == "Ersatz 2026 (PDF)" for s in strom["segments"])
    # The Downloads section became its own block under its own heading.
    dl = next(b for b in page["blocks"] if b["heading"] == "Downloads Strom Grundversorgung")
    assert dl["segments"][0]["files"] == "Preisblatt 2025 (PDF)\nAGB (PDF)"


def test_enrich_topic_keeps_specific_download_subtitle(tmp_path, monkeypatch):
    # Files under "Downloads zur Grundversorgung" attach to the matching
    # "Grundversorgung" block, but the more specific real title is preserved as a
    # subheading (not silently collapsed into the block heading).
    html = ('<h2>Grundversorgung</h2><p>prose</p>'
            '<h3>Downloads zur Grundversorgung</h3>'
            '<a href="/x/Tarif.pdf">Tarif (PDF)</a>')
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: html)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Grundversorgung", "segments": [{"text": "prose"}]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    segs = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["pages"][0]["blocks"][0]["segments"]
    # the download subtitle survives, immediately before its files
    subs = [s.get("subheading") for s in segs]
    assert "Downloads zur Grundversorgung" in subs
    dl_i = subs.index("Downloads zur Grundversorgung")
    assert segs[dl_i + 1].get("files") == "Tarif (PDF)"


PHONE_HTML = """
<h3>Kunden-Center</h3>
<a href="tel:+4971511310170">+49 7151 131-170</a>
<h3>So erreichen Sie uns</h3>
<a href="tel:07151131-0">07151 131-0</a>
"""


def test_extract_phone_contacts_labels_and_digits():
    phones = enrich.extract_phone_contacts(PHONE_HTML)
    assert [(p["label"], p["phone"]) for p in phones] == [
        ("Kunden-Center", "+49 7151 131-170"),
        ("So erreichen Sie uns", "07151 131-0"),
    ]


def test_enrich_topic_injects_missing_phones(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: PHONE_HTML)
    # Agent already captured one phone; the other must be recovered, not duplicated.
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Kontakt", "segments": [{"contacts": "Kunden-Center: +49 7151 131-170"}]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blob = (tmp_path / "t.json").read_text(encoding="utf-8")
    assert blob.count("131-170") == 1        # already-present phone not duplicated
    assert "07151 131-0" in blob             # missing phone recovered


TWO_PRODUCT_ACCORDIONS = """
<h3>MHZ Anhänger bis 200 kW</h3>
<div class="accordion-item"><h2 class="accordion-header"><button class="accordion-button">Technische Daten</button></h2>
  <div class="accordion-collapse"><p>Leistung: 200 kW</p></div></div>
<h3>MHZ Container bis 455 kW</h3>
<div class="accordion-item"><h2 class="accordion-header"><button class="accordion-button">Technische Daten</button></h2>
  <div class="accordion-collapse"><p>Leistung: 455 kW</p></div></div>
"""


def test_extract_qa_groups_keeps_repeated_labels_per_section():
    groups = enrich.extract_expandable_qa_groups(TWO_PRODUCT_ACCORDIONS)
    # same label "Technische Daten" under two products -> two groups, both kept
    assert [g["heading"] for g in groups] == ["MHZ Anhänger bis 200 kW", "MHZ Container bis 455 kW"]
    assert groups[0]["qas"][0]["answer"] == "Leistung: 200 kW"
    assert groups[1]["qas"][0]["answer"] == "Leistung: 455 kW"


def test_enrich_topic_fallback_attaches_panels_in_context(tmp_path, monkeypatch):
    # No FAQ section flagged by the agent: each spec panel is attached to its own
    # product block (in context), and BOTH "Technische Daten" survive.
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: TWO_PRODUCT_ACCORDIONS)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "MHZ Anhänger bis 200 kW", "segments": [{"text": "Anhänger."}]},
        {"heading": "MHZ Container bis 455 kW", "segments": [{"text": "Container."}]},
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blocks = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["pages"][0]["blocks"]
    assert not any(b["heading"] == "FAQ" for b in blocks)   # no misleading standalone block

    def specs(block):
        return [qa["answer"] for s in block["segments"] if s.get("faqs")
                for qa in s["faqs"]["QAs"]]
    anh = next(b for b in blocks if b["heading"].startswith("MHZ Anhänger"))
    con = next(b for b in blocks if b["heading"].startswith("MHZ Container"))
    assert specs(anh) == ["Leistung: 200 kW"]   # each product keeps its own specs
    assert specs(con) == ["Leistung: 455 kW"]


def test_strip_redundant_faq_content_label_is_not_an_anchor():
    # A content-panel label the agent wrote as a subheading is stripped, but must
    # NOT be treated as a FAQ anchor (else all panels get dumped into one block).
    page = {"blocks": [
        {"heading": "MHZ Anhänger", "segments": [{"subheading": "Technische Daten", "text": "x"}]},
        {"heading": "MHZ Container", "segments": [{"text": "y"}]},
    ]}
    anchor = enrich._strip_redundant_faq(page, {"technische daten"})
    assert anchor is None                                   # not a FAQ section
    assert page["blocks"][0]["segments"][0]["subheading"] is None  # label stripped


def test_strip_redundant_faq_real_fragen_section_is_anchor():
    page = {"blocks": [
        {"heading": "Sie haben Fragen?", "segments": [{"text": "z"}]},
    ]}
    assert enrich._strip_redundant_faq(page, set()) == 0    # genuine FAQ section


def test_enrich_topic_content_panels_attach_per_product(tmp_path, monkeypatch):
    # No genuine FAQ section + repeated content-panel label across products:
    # every product must get its OWN panels (not all dumped in the first block).
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: TWO_PRODUCT_ACCORDIONS)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "MHZ Anhänger bis 200 kW", "segments": [{"text": "Anhänger intro."}]},
        {"heading": "MHZ Container bis 455 kW", "segments": [{"text": "Container intro."}]},
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blocks = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["pages"][0]["blocks"]

    def ans(name):
        b = next(b for b in blocks if b["heading"].startswith(name))
        return [qa["answer"] for s in b["segments"] if s.get("faqs") for qa in s["faqs"]["QAs"]]
    assert ans("MHZ Anhänger") == ["Leistung: 200 kW"]
    assert ans("MHZ Container") == ["Leistung: 455 kW"]


def test_enrich_topic_skips_panel_content_captured_without_label(tmp_path, monkeypatch):
    # The agent wrote the panel's DATA into the text but omitted the label (as a
    # Markdown table vs the agent's tab-separated prose). Content-level dedup must
    # still recognize it and NOT add a duplicate panel.
    html = ('<h3>MHZ Anhänger</h3><div class="accordion-item">'
            '<button class="accordion-button">Technische Daten</button>'
            '<div class="accordion-collapse"><table><tr><td>Leistung:</td><td>200 kW</td></tr></table></div></div>')
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: html)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "MHZ Anhänger", "segments": [{"text": "Intro.\nLeistung: 200 kW"}]}   # data, no label
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    block = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["pages"][0]["blocks"][0]
    faqs = [s for s in block["segments"] if s.get("faqs")]
    assert faqs == []   # content already present (label-less) -> no duplicate panel


def test_enrich_topic_fallback_skips_panels_agent_already_captured(tmp_path, monkeypatch):
    # The agent already wrote the panel as a Markdown heading + prose in the
    # block's text -> the deterministic panel must NOT be added again (no dup).
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30: TWO_PRODUCT_ACCORDIONS)
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "MHZ Anhänger bis 200 kW",
         "segments": [{"text": "Intro.\n### Technische Daten\nLeistung: 200 kW"}]},
        {"heading": "MHZ Container bis 455 kW",
         "segments": [{"text": "Nur Intro, keine Daten."}]},
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blocks = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["pages"][0]["blocks"]

    def faq_qs(block):
        return [qa["question"] for s in block["segments"] if s.get("faqs")
                for qa in s["faqs"]["QAs"]]
    anh = next(b for b in blocks if b["heading"].startswith("MHZ Anhänger"))
    con = next(b for b in blocks if b["heading"].startswith("MHZ Container"))
    assert faq_qs(anh) == []                       # agent already had it -> not duplicated
    assert faq_qs(con) == ["Technische Daten"]     # agent missed it -> recovered


def test_is_blank_text_detects_empty_list_markers():
    assert enrich._is_blank_text("- \n- \n- ")
    assert enrich._is_blank_text("  ")
    assert enrich._is_blank_text(None)
    assert not enrich._is_blank_text("- real item\n- another")


def test_enrich_topic_prunes_empty_placeholder_segment(tmp_path, monkeypatch):
    # Agent rendered the moved-away PDFs as empty bullets; after files are
    # re-attached under their heading, the empty placeholder must be gone.
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30:
                        '<h2>Ersatzversorgung</h2><a href="/x/P.pdf">P (PDF)</a>')
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Ersatzversorgung", "segments": [
            {"text": "Real prose here."},
            {"text": "- \n- \n- ", "files": "P (PDF)"},
        ]},
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    segs = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["pages"][0]["blocks"][0]["segments"]
    assert not any(s.get("text") == "- \n- \n- " for s in segs)   # placeholder pruned
    assert any(s.get("files") == "P (PDF)" for s in segs)         # file kept
    assert any(s.get("text") == "Real prose here." for s in segs) # real prose kept


def test_phone_key_treats_international_and_national_as_equal():
    # +49 7151 131-0 and 07151 131-0 are the same number in different formats.
    assert enrich._phone_key("+49 7151 131-0") == enrich._phone_key("07151 131-0")
    assert enrich._phone_key("+49 7151 131-170") == enrich._phone_key("07151 131-170")


def test_enrich_topic_skips_phone_captured_in_other_format(tmp_path, monkeypatch):
    # Page has only the national-format tel: link; agent already captured the
    # SAME number in international format -> nothing added (no near-duplicate).
    monkeypatch.setattr(enrich, "fetch_html", lambda url, timeout=30:
                        '<a href="tel:07151131-0">07151 131-0</a>')
    data = {"pages": [{"url": "http://x", "blocks": [
        {"heading": "Kontakt", "segments": [{"contacts": "Zentrale: +49 7151 131-0"}]}
    ]}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")

    enrich.enrich_topic("t", tmp_path)
    blob = (tmp_path / "t.json").read_text(encoding="utf-8")
    assert "07151 131-0" not in blob         # national-format duplicate NOT added
    assert blob.count("131-0") == 1
