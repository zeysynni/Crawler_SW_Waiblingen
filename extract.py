"""HTML→markdown extractors for structured JS components — pure, no I/O.

Some sites embed a component's full data in the initial HTML (e.g. a JSON
`data-*` attribute) but render only a slice of it into the DOM. crawl4ai's
DOM→markdown conversion then misses the rest. An *extractor* builds the
clean markdown for such a page directly from the fetched HTML.

A section opts in via `extract: <name>` in its site YAML (validated against
`EXTRACTORS` at config load); `main.save_outputs` then uses the extractor
instead of `clean.clean_markdown` for that page. `outputs/raw/` keeps the
untouched crawl4ai conversion either way.
"""

import html
import json

from bs4 import BeautifulSoup

# Icon slugs of the AHK Abc-List component -> readable hint text
# (complete set as of 2026-07-16; unknown slugs pass through as-is).
_AHK_ICONS = {
    "aetzend": "ätzend",
    "explosiv": "explosiv",
    "info": "Info",
    "leicht_entzuendlich": "leicht entzündlich",
    "reizend": "reizend",
    "restmuelltonne_NO": "nicht in die Restmülltonne",
    "schadstoffe": "Schadstoffe",
    "toilette": "Toilette",
}


def _text(fragment: str) -> str:
    """Flatten an HTML fragment to single-line plain text (safe in a table cell)."""
    text = BeautifulSoup(html.unescape(fragment or ""), "html.parser").get_text(" ")
    return " ".join(text.split()).replace("|", "/")


def abfall_abc(page_html: str, url: str) -> str:
    """AHK Heidekreis Abfall-ABC: the complete A–Ö waste table.

    The `div.Abc-List` component ships all entries as JSON in its
    `data-items` attribute ({"A": [{type, place, info, icons}, ...], ...})
    but renders only one letter into the DOM. Emit every letter as a `##`
    section with a markdown table, in the site's own letter order.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    node = soup.find(class_="Abc-List")
    if node is None or not node.get("data-items"):
        raise ValueError(f"no Abc-List with data-items on {url}")
    data = json.loads(node["data-items"])

    h1 = soup.find("h1")
    title = " ".join(h1.get_text(" ").split()) if h1 else "Abfall ABC"
    lines = [f"# {title}"]
    for letter, items in data.items():
        lines += ["", f"## {letter}", "",
                  "| Abfallart | Wohin? | Hinweise |", "| --- | --- | --- |"]
        for item in items:
            hints = _text(item.get("info", ""))
            icons = ", ".join(_AHK_ICONS.get(i, i) for i in item.get("icons", []))
            if icons:
                hints = f"{hints} ({icons})" if hints else icons
            lines.append(f"| {_text(item.get('type', ''))} "
                         f"| {_text(item.get('place', ''))} | {hints} |")
    return "\n".join(lines) + "\n"


EXTRACTORS = {"abfall_abc": abfall_abc}
