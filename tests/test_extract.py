"""Tests for the pure HTML→markdown extractors (extract.py)."""

import html as H
import json

import pytest
from pydantic import ValidationError

from config import Section
from extract import EXTRACTORS, abfall_abc


def _page(items: dict) -> str:
    """Minimal AHK-shaped page: h1 + Abc-List div with escaped JSON attribute."""
    payload = H.escape(json.dumps(items), quote=True)
    return (f"<html><body><h1>Abfall  ABC</h1>"
            f'<div class="Abc-List" data-items="{payload}"></div></body></html>')


ITEMS = {
    "A": [
        {"type": "<strong>Altglas</strong> (alle Farben)<br>-Flaschen-",
         "place": "Altglascontainer", "info": ""},
        {"type": "<strong>Akkus</strong>", "place": "Wertstoffhof",
         "info": "Besser als Einweg!", "icons": ["restmuelltonne_NO"]},
    ],
    "Ö": [
        {"type": "<strong>Ölbindemittel</strong>", "place": "Schadstoffmobil",
         "info": "", "icons": ["unbekanntes_icon"]},
    ],
}


def test_abfall_abc_emits_all_letters_and_rows():
    md = abfall_abc(_page(ITEMS), "https://x.de/abc.html")
    assert md.splitlines()[0] == "# Abfall ABC"          # h1, whitespace collapsed
    assert "## A" in md and "## Ö" in md                 # every letter, incl. umlauts
    assert "| Altglas (alle Farben) -Flaschen- | Altglascontainer |" in md  # tags flattened
    assert "Besser als Einweg! (nicht in die Restmülltonne)" in md  # icon slug -> text
    assert "| Ölbindemittel | Schadstoffmobil | unbekanntes_icon |" in md  # unknown slug passes through
    assert md.index("## A") < md.index("## Ö")           # site's letter order kept


def test_abfall_abc_fails_loudly_without_component():
    with pytest.raises(ValueError, match="no Abc-List"):
        abfall_abc("<html><body><p>relaunched page</p></body></html>", "https://x.de")


def test_section_validates_extractor_name():
    assert Section(path="X", extract="abfall_abc").extract == "abfall_abc"
    assert Section(path="X").extract is None
    with pytest.raises(ValidationError, match="unknown extractor"):
        Section(path="X", extract="nope")
    assert "abfall_abc" in EXTRACTORS
