"""Markdown cleaning for crawled pages — pure functions, no I/O.

crawl4ai converts the whole rendered page to markdown (`raw`). This module
cuts that down to the knowledge-base `clean` form:

  * keep the heading-led page content, drop the preamble noise before the
    first heading (Sprungmarken, Menü, breadcrumbs) and the tail from the
    footer quick-links / cookie banner onward
  * replace the page h1 with its site hierarchy ("# Privatkunden - Strom -
    Ökostromtarif", from the page's own breadcrumb nav); keep a differing
    (marketing) h1 as a `##` below
  * flatten links to plain text and drop images — the KB needs no hypertext

The footer/cookie sentinels are specific to the Stadtwerke Waiblingen CMS
template (one template for the whole site). Adjust them for a new site.
"""

import re
from urllib.parse import unquote, urlparse

# First line of the footer's quick-link list (identical on every page):
_FOOTER_START = re.compile(
    r"^\s*\*\s*\[\s*Kontakt\s*\]\(https://www\.stadtwerke-waiblingen\.de/kontakt\b"
)
# Cookie-consent overlay text (everything from here on is noise):
_COOKIE_START = "Wir nutzen Cookies und andere Technologien"


def slug(text: str) -> str:
    """Text -> safe filename chunk, e.g. 'Abschläge berechnen & verstehen' -> Abschläge_berechnen_verstehen"""
    return re.sub(r"[^\w\-]+", "_", text).strip("_")   # \w already matches ä ö ü ß


def strip_links(md: str) -> str:
    """Flatten markdown links to their text and drop images (KB needs no URLs)."""
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)        # images (incl. svg icons)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)    # [text](url) -> text
    md = "\n".join(line.rstrip() for line in md.splitlines())
    md = re.sub(r"^\s*\*\s*$", "", md, flags=re.MULTILINE)  # bullets left empty
    md = re.sub(r"\n{3,}", "\n\n", md)                  # collapse blank runs
    return md


def breadcrumb(preamble: list[str], url: str) -> str:
    """Site hierarchy for the page, e.g. 'Privatkunden - Strom - Ökostromtarif'.

    Preferred source: the page's own breadcrumb nav, which the raw markdown
    renders as a numbered list right above the h1 ('1. Startseite
    2. Privatkunden 3. Strom 4. Ökostromtarif') — it carries the human-readable
    section names even when the h1 is a marketing headline. Fallback: URL path.
    """
    crumbs = []
    for line in reversed(preamble):         # walk up from just above the h1
        m = re.match(r"^\s*\d+\.\s+(.*\S)", line)
        if m:
            crumbs.append(strip_links(m.group(1)).strip())
        elif crumbs and line.strip():       # numbered block ended
            break
    crumbs.reverse()
    crumbs = [c for c in crumbs if c and c != "Startseite"]
    if crumbs:
        return " - ".join(crumbs)
    return " - ".join(unquote(s) for s in urlparse(url).path.split("/") if s)


def clean_markdown(md: str, url: str) -> str:
    """Keep the heading-led page content, drop preamble + footer/cookie tail.

    Raw page layout is always: [Sprungmarken/Menü/breadcrumb noise]
    -> '# <title>' -> ##/### sections -> [footer links] -> [cookie banner].
    The h1 becomes the page's site hierarchy (from the breadcrumb nav); a
    marketing h1 that differs from it is kept as a '##' below. Links are
    flattened to plain text, images dropped.
    """
    lines = md.splitlines()

    # start: first markdown heading (the page's own '# <title>')
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s", line)),
        0,
    )

    # end: footer quick-links or cookie banner, whichever comes first
    end = len(lines)
    for i in range(start, len(lines)):
        if _FOOTER_START.match(lines[i]) or _COOKIE_START in lines[i]:
            end = i
            break

    kept = lines[start:end]

    # h1 <- site hierarchy; keep a differing (marketing) title as '##' below
    title_match = re.match(r"^#\s+(.*\S)", kept[0]) if kept else None
    if title_match:
        crumb = breadcrumb(lines[:start], url)
        title = strip_links(title_match.group(1)).strip()
        kept[0] = f"# {crumb}"
        if title != crumb.split(" - ")[-1]:
            kept.insert(1, f"## {title}")

    return strip_links("\n".join(kept)).rstrip() + "\n"
