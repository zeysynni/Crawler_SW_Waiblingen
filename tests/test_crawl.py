"""Tests for sub-page label resolution (pure part of crawl.py)."""

from crawl import resolve_subpages

PAGE = "https://www.example.de/Privatkunden/Waerme"

LINKS = [
    {"href": "https://x.de/fernwaerme", "text": "Fernwärme  Bedarfsgerecht und günstig"},
    {"href": "https://x.de/fernwaerme", "text": "mehr"},                    # same target, 2nd anchor
    {"href": "https://x.de/heizzentralen", "text": "Mobile Heizzentralen mieten"},
    {"href": "https://x.de/strom-a", "text": "Strom für Zuhause"},
    {"href": "https://x.de/strom-b", "text": "Strom für Unternehmen"},
    {"href": "https://x.de/leer", "text": ""},
]


def test_exact_match_wins():
    resolved, problems = resolve_subpages(LINKS, ["Mobile Heizzentralen mieten"], PAGE)
    assert resolved == [("Mobile Heizzentralen mieten", "https://x.de/heizzentralen")]
    assert problems == []


def test_prefix_match_for_teaser_cards():
    resolved, problems = resolve_subpages(LINKS, ["Fernwärme"], PAGE)
    assert resolved == [("Fernwärme", "https://x.de/fernwaerme")]
    assert problems == []


def test_match_is_case_and_whitespace_insensitive():
    resolved, _ = resolve_subpages(LINKS, ["mobile  heizzentralen MIETEN"], PAGE)
    assert resolved and resolved[0][1] == "https://x.de/heizzentralen"


def test_ambiguous_label_is_skipped_with_problem():
    resolved, problems = resolve_subpages(LINKS, ["Strom"], PAGE)
    assert resolved == []
    assert problems and "ambiguous" in problems[0]


def test_missing_label_reports_problem():
    resolved, problems = resolve_subpages(LINKS, ["Gibt es nicht"], PAGE)
    assert resolved == []
    assert problems == [f"no link with text 'Gibt es nicht' on {PAGE}"]


def test_same_target_twice_is_not_ambiguous():
    # two anchors to ONE url (teaser image + title) must not trigger ambiguity
    links = [
        {"href": "https://x.de/f", "text": "Fernwärme im Detail"},
        {"href": "https://x.de/f", "text": "Fernwärme jetzt entdecken"},
    ]
    resolved, problems = resolve_subpages(links, ["Fernwärme"], PAGE)
    assert resolved == [("Fernwärme", "https://x.de/f")]
    assert problems == []
