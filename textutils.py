"""Markdown text helpers shared by the crawler and the source converters.

These two functions are pure text transforms with no knowledge of the crawler,
the site, or the knowledge base. They live here rather than in `clean.py` so
that `PDFs/pdf2md.py` and `Excels/xlsx2md.py` — which convert source documents
offline and never crawl anything — do not have to import the crawler to reuse
them.

`clean.py` re-exports both names, so existing imports keep working.
"""

import re


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
