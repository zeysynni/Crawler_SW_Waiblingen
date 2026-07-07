"""Tests for the pure markdown-cleaning functions."""

from clean import breadcrumb, clean_markdown, slug, strip_links

URL = "https://www.stadtwerke-waiblingen.de/Privatkunden/Strom/oekostrom"

RAW = """\
**Sprungmarken**
Zum Inhalt springen
  * [ Privatkunden ](https://example.de/Privatkunden/Strom)
  * [Strom](https://example.de/Privatkunden/Strom)

  1. [ Startseite ](https://example.de)
  2. [ Privatkunden ](https://example.de/Privatkunden/Strom)
  3. [ Strom ](https://example.de/Privatkunden/Strom)
  4. Ökostromtarif

#  Unser bestes Angebot: toptarif-KLIMA plus
Intro-Absatz mit **Fett**.
##  Downloads zur Grundversorgung
  * [ Preisblatt 2026 (PDF | 89 KB) ](https://example.de/resources/preisblatt.pdf)
###  Was ist eine Kilowattstunde?
Die Einheit für Energie.
  * [ Kontakt ](https://www.stadtwerke-waiblingen.de/kontakt "Kontakt")
  * [ Notfallnummern ](https://example.de/notfallnummern)
Wir nutzen Cookies und andere Technologien.
Cookie-Banner-Prosa, die nicht in den Output gehört.
"""


def test_slug():
    assert slug("Abschläge berechnen & verstehen") == "Abschläge_berechnen_verstehen"
    assert slug("Privatkunden/Strom".replace("/", "_")) == "Privatkunden_Strom"


def test_strip_links_flattens_and_drops_images():
    md = "See [Kunden-Center](https://x.de/kc) and ![icon](https://x.de/i.svg) done"
    out = strip_links(md)
    assert "Kunden-Center" in out
    assert "https://" not in out and "![" not in out


def test_strip_links_removes_empty_link_bullets():
    md = "  * [](https://x.de/ghost.pdf)\n  * [Real](https://x.de/real.pdf)"
    out = strip_links(md)
    assert "Real" in out and "ghost" not in out


def test_breadcrumb_prefers_nav_and_drops_startseite():
    preamble = RAW.splitlines()[:10]
    assert breadcrumb(preamble, URL) == "Privatkunden - Strom - Ökostromtarif"


def test_breadcrumb_falls_back_to_url_path():
    assert breadcrumb([], URL) == "Privatkunden - Strom - oekostrom"


def test_clean_markdown_cuts_preamble_footer_and_cookies():
    out = clean_markdown(RAW, URL)
    assert out.startswith("# Privatkunden - Strom - Ökostromtarif\n")
    assert "Sprungmarken" not in out
    assert "Notfallnummern" not in out          # footer quick-links cut
    assert "Cookies" not in out                 # cookie banner cut
    assert "Preisblatt 2026 (PDF | 89 KB)" in out   # content kept, link flattened
    assert "resources/preisblatt.pdf" not in out
    assert "Die Einheit für Energie." in out


def test_clean_markdown_keeps_marketing_h1_as_h2():
    out = clean_markdown(RAW, URL)
    assert "## Unser bestes Angebot: toptarif-KLIMA plus" in out
    assert out.count("\n# ") == 0               # exactly one h1 (the first line)


def test_clean_markdown_no_duplicate_title_when_h1_matches_crumb():
    raw = RAW.replace("#  Unser bestes Angebot: toptarif-KLIMA plus", "#  Ökostromtarif")
    out = clean_markdown(raw, URL)
    assert out.startswith("# Privatkunden - Strom - Ökostromtarif\n")
    assert "## Ökostromtarif" not in out
