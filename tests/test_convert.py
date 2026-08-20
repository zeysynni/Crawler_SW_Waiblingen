"""Tests for the source converters (`PDFs/pdf2md.py`, `Excels/xlsx2md.py`).

These two scripts became part of the weekly automation (`.gitlab-ci.yml` runs
them before the crawl), so the properties that used to be guaranteed by a human
reading the output now need covering here:

  * `has_table` — the tariff-sheet degradation guard. A reissued PDF that lost
    its ruling lines yields prose instead of a priced table, which is the one
    failure that looks fine and is wrong (see `PDFs/README.md` §2.1).
  * `_clean_generated` — deletes only the output the script itself owns. If it
    over-reached it would delete hand-written pages; if it under-reached, a
    renamed document would be uploaded to the knowledge base for ever.
"""

import importlib

import pytest

pdf2md = importlib.import_module("PDFs.pdf2md")
xlsx2md = importlib.import_module("Excels.xlsx2md")


# --- has_table ---------------------------------------------------------------

def test_has_table_finds_a_generated_table():
    md = "# Privatkunden - Bäder - Tarifübersicht\n\n| Karte | Preis |\n|---|---|\n| Familie | 13,80€ |\n"
    assert pdf2md.has_table(md)


def test_has_table_false_for_prose():
    """The degradation case: the same prices as loose text, no ruling lines."""
    md = "# Privatkunden - Bäder - Tarifübersicht\n\nFamilie 2 Erw. + Kinder 6 - 16 Jahre 13,80€\n"
    assert not pdf2md.has_table(md)


def test_has_table_ignores_hierarchy_separators_and_dashes():
    """' - ' in the h1 and an en-dash paragraph must not read as a table."""
    md = "# Privatkunden - Bäder - Erläuterungen\n\nErmäßigung – gültig ab 2026.\n"
    assert not pdf2md.has_table(md)


@pytest.mark.parametrize("title, required", [
    ("Tarifübersicht Freibäder 2026", True),
    ("Tarifübersicht Hallenbad 2027", True),          # survives the yearly rename
    ("Erläuterungen zu den ermäßigten Eintrittspreisen", False),
    ("Nutzungsbedingungen Gäste-WLAN Bäder", False),
])
def test_table_required_matches_only_tariff_sheets(title, required):
    assert bool(pdf2md.TABLE_REQUIRED.search(title)) is required


# --- _clean_generated --------------------------------------------------------

@pytest.mark.parametrize("module, prefix", [
    (pdf2md, "Privatkunden_Baeder"),
    (xlsx2md, "Wissensdatenbank"),
])
def test_clean_generated_removes_own_output_only(module, prefix, tmp_path, monkeypatch):
    own_stale = tmp_path / f"{prefix}_Tarifuebersicht_Freibaeder_2026.md"
    hand_written = tmp_path / "Kundenportal.md"          # must survive
    other_converter = tmp_path / "Somethingelse_Foo.md"  # must survive
    for path in (own_stale, hand_written, other_converter):
        path.write_text("x", encoding="utf-8")

    monkeypatch.setattr(module, "STATIC_DIR", tmp_path)
    module._clean_generated(prefix)

    assert not own_stale.exists()
    assert hand_written.exists()
    assert other_converter.exists()


def test_clean_generated_is_a_noop_on_a_fresh_checkout(tmp_path, monkeypatch):
    """Nothing to delete must not raise — a CI runner starts with only the
    hand-written pages present."""
    (tmp_path / "Kundenportal.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(pdf2md, "STATIC_DIR", tmp_path)
    pdf2md._clean_generated("Privatkunden_Baeder")
    assert (tmp_path / "Kundenportal.md").exists()
