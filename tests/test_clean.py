"""Tests for the pure markdown-cleaning functions."""

from clean import breadcrumb, clean_markdown, content_span, slug, strip_links

URL = "https://www.ahk-heidekreis.de/service/gelbe-tonne.html"

# What the AHK site file puts in `stop_at` — regexes, matched per line.
STOP_AT = [r"^##\s+Weitere Links\b", "Wir nutzen Cookies und andere Technologien"]

RAW = """\
[Zum Inhalt springen](https://example.de/#content)
  * [ Service ](https://example.de/service)
  * [Gelbe Tonne](https://example.de/service/gelbe-tonne.html)

  1. [ Startseite ](https://example.de)
  2. [ Service ](https://example.de/service)
  3. Gelbe Tonne

#  Gut sortiert: die Gelbe Tonne
Intro-Absatz mit **Fett**.
##  Downloads
  * [ Merkblatt 2026 (PDF | 89 KB) ](https://example.de/resources/merkblatt.pdf)
###  Was gehört hinein?
Verpackungen aus Kunststoff und Metall.
## Weitere Links
  * [ Impressum ](https://www.ahk-heidekreis.de/footer-menue/impressum.html)
  * [ Datenschutzerklärung ](https://example.de/footer-menue/datenschutzerklaerung.html)
Wir nutzen Cookies und andere Technologien.
Cookie-Banner-Prosa, die nicht in den Output gehört.
"""


def test_slug():
    assert slug("Abschläge berechnen & verstehen") == "Abschläge_berechnen_verstehen"
    assert slug("Service/Abfall-ABC".replace("/", "_")) == "Service_Abfall-ABC"


def test_strip_links_flattens_and_drops_images():
    md = "See [Abfall ABC](https://x.de/abc) and ![icon](https://x.de/i.svg) done"
    out = strip_links(md)
    assert "Abfall ABC" in out
    assert "https://" not in out and "![" not in out


def test_strip_links_removes_empty_link_bullets():
    md = "  * [](https://x.de/ghost.pdf)\n  * [Real](https://x.de/real.pdf)"
    out = strip_links(md)
    assert "Real" in out and "ghost" not in out


def test_breadcrumb_prefers_nav_and_drops_startseite():
    preamble = RAW.splitlines()[:8]
    assert breadcrumb(preamble, URL) == "Service - Gelbe Tonne"


def test_breadcrumb_falls_back_to_url_path():
    assert breadcrumb([], URL) == "service - gelbe-tonne.html"


def test_clean_markdown_cuts_preamble_footer_and_cookies():
    out = clean_markdown(RAW, URL, STOP_AT)
    assert out.startswith("# Service - Gelbe Tonne\n")
    assert "Zum Inhalt springen" not in out
    assert "Impressum" not in out               # footer block cut
    assert "Cookies" not in out                 # cookie banner cut
    assert "Merkblatt 2026 (PDF | 89 KB)" in out    # content kept, link flattened
    assert "resources/merkblatt.pdf" not in out
    assert "Verpackungen aus Kunststoff und Metall." in out


def test_clean_markdown_without_stop_at_cuts_nothing():
    """The general-crawler default: an unknown site keeps its whole tail.

    Showing too much is recoverable (the person adds a pattern); silently
    dropping content is not.
    """
    out = clean_markdown(RAW, URL)
    assert "Impressum" in out                   # footer block survives
    assert "Cookie-Banner-Prosa" in out         # cookie banner survives
    assert out.startswith("# Service - Gelbe Tonne\n")   # the rest still applies


def test_clean_markdown_stop_at_is_searched_not_matched():
    """`re.search`, so an unanchored pattern cuts on a line it appears in ...

    ... while `^` still anchors. That is what lets one list replace both the
    old line-anchored footer regex and the old plain cookie substring.
    """
    assert "Verpackungen" not in clean_markdown(RAW, URL, ["Verpackungen"])
    assert "Verpackungen" in clean_markdown(RAW, URL, [r"^Verpackungen$"])


def test_clean_markdown_keeps_marketing_h1_as_h2():
    out = clean_markdown(RAW, URL, STOP_AT)
    assert "## Gut sortiert: die Gelbe Tonne" in out
    assert out.count("\n# ") == 0               # exactly one h1 (the first line)


def test_clean_markdown_no_duplicate_title_when_h1_matches_crumb():
    raw = RAW.replace("#  Gut sortiert: die Gelbe Tonne", "#  Gelbe Tonne")
    out = clean_markdown(raw, URL, STOP_AT)
    assert out.startswith("# Service - Gelbe Tonne\n")
    assert "## Gelbe Tonne" not in out


def test_content_span_reports_where_the_cut_lands():
    """The UI shows these numbers, so they must mean what clean_markdown does."""
    lines = RAW.splitlines()
    start, end = content_span(lines, STOP_AT)
    assert lines[start].startswith("#  Gut sortiert")     # first heading
    assert lines[end] == "## Weitere Links"               # first stop match
    assert content_span(lines)[1] == len(lines)           # no patterns -> no cut
