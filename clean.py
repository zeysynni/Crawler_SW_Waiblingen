"""Markdown cleaning for crawled pages — pure functions, no I/O.

crawl4ai converts the whole rendered page to markdown (`raw`). This module
cuts that down to the knowledge-base `clean` form:

  * keep the heading-led page content, drop the preamble noise before the
    first heading (Sprungmarken, Menü, breadcrumbs) and the tail from the
    tail from the first `stop_at` match onward
  * replace the page h1 with its site hierarchy ("# Service - Abfall ABC",
    from the page's own breadcrumb nav); keep a differing (marketing) h1
    as a `##` below
  * flatten links to plain text and drop images — the KB needs no hypertext

"""

import re
from urllib.parse import unquote, urlparse


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
    """Site hierarchy for the page, e.g. 'Service - Abfall ABC'.

    Preferred source: the page's own breadcrumb nav, which the raw markdown
    renders as a numbered list right above the h1 ('1. Startseite 2. Service
    3. Abfall ABC') — it carries the human-readable section names even when
    the h1 is a marketing headline. Fallback: URL path.
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


def content_span(lines: list[str], stop_at: list[str] | None = None) -> tuple[int, int]:
    """Line range `[start, end)` of the real page content.

    `start` is the page's first markdown heading — everything above it is CMS
    preamble (Sprungmarken, Menü, breadcrumb nav). `end` is the first line at
    or after it matching any `stop_at` regex (`re.search`, so `^` anchors and
    a bare phrase matches anywhere in the line); with no patterns it is the end
    of the document, i.e. nothing is cut.

    Exposed separately from `clean_markdown` so the UI can tell a person *where*
    their patterns cut without re-deriving the rule and drifting from it.
    """
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s", line)),
        0,
    )
    stops = [re.compile(p) for p in stop_at or []]
    for i in range(start, len(lines)):
        if any(s.search(lines[i]) for s in stops):
            return start, i
    return start, len(lines)


def clean_markdown(md: str, url: str, stop_at: list[str] | None = None) -> str:
    """Keep the heading-led page content, cut at the first `stop_at` match.

    `stop_at` holds regexes (matched with `re.search` against each line) that
    mark where a site's noise begins — footer link block, cookie banner. None
    given: nothing is cut, which is the safe default for an unknown site.

    Typical raw page layout: [Sprungmarken/Menü/breadcrumb noise] -> '# <title>'
    -> ##/### sections -> [footer links] -> [cookie banner]. The h1 becomes the
    page's site hierarchy (from the breadcrumb nav); a marketing h1 that differs
    from it is kept as a '##' below. Links are flattened, images dropped.
    """
    lines = md.splitlines()
    start, end = content_span(lines, stop_at)
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
