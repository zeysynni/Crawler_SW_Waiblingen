"""Tests for the clean/extract choice shared by the CLI and the UI."""

from types import SimpleNamespace

import pytest

from render import render_clean

URL = "https://www.ahk-heidekreis.de/service/abfall-abc.html"

ABC_HTML = (
    "<h1>Abfall ABC</h1>"
    "<div class=\"Abc-List\" data-items='"
    '{"A": [{"type": "Altpapier", "place": "Blaue Tonne", "info": "", "icons": []}]}'
    "'></div>"
)

PAGE_MD = """\
#  Gelbe Tonne
Verpackungen aus Kunststoff und Metall.
## Weitere Links
  * [ Impressum ](https://www.ahk-heidekreis.de/footer-menue/impressum.html)
"""


def page(**kw):
    """A PageResult-shaped stub — render_clean only reads four attributes."""
    base = dict(name="p", url=URL, raw_markdown=None, html=None, extract=None)
    return SimpleNamespace(**{**base, **kw})


def site(stop_at=()):
    """A Site-shaped stub — render_clean only reads `stop_at`."""
    return SimpleNamespace(stop_at=list(stop_at))


def test_without_extractor_goes_through_clean_markdown():
    out = render_clean(page(raw_markdown="#  Titel\nText mit [Link](https://example.de).\n"), site())
    assert "Link" in out                      # link text kept ...
    assert "https://example.de" not in out    # ... the URL flattened away


def test_stop_at_reaches_clean_markdown():
    """The point of threading the site through: the patterns must arrive."""
    assert "Impressum" in render_clean(page(raw_markdown=PAGE_MD), site())
    assert "Impressum" not in render_clean(
        page(raw_markdown=PAGE_MD), site([r"^##\s+Weitere Links\b"])
    )


def test_with_extractor_reads_the_html_not_the_markdown():
    # raw_markdown is deliberately wrong: the extractor must ignore it
    out = render_clean(page(html=ABC_HTML, raw_markdown="# falsch", extract="abfall_abc"), site())
    assert out.startswith("# Abfall ABC")
    assert "| Altpapier | Blaue Tonne |" in out


def test_extractor_ignores_stop_at():
    """Documented consequence: an extractor builds markdown from the HTML, so
    the site's stop_at patterns never apply to it."""
    with_stops = render_clean(page(html=ABC_HTML, extract="abfall_abc"), site(["Altpapier"]))
    assert "Altpapier" in with_stops


def test_broken_extractor_raises_for_the_caller_to_handle():
    # site relaunch: the component is gone. render_clean must not swallow it —
    # main.save_outputs turns this into a failed page, the UI into a message.
    with pytest.raises(ValueError):
        render_clean(page(html="<p>kein Abc-List</p>", extract="abfall_abc"), site())
