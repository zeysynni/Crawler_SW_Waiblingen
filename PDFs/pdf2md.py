"""One-off conversion of the Bäder PDFs in this folder to knowledge-base markdown.

    uv run --with pdfplumber python PDFs/pdf2md.py

Writes one `.md` per PDF into `static/`, from where the normal pipeline picks it
up: `main.py:copy_static()` copies `static/*.md` into `outputs/clean/` and
`--upload` pushes them like any crawled page. (Writing straight to
`outputs/clean/` would *not* work: that directory is gitignored and rebuilt each
run, and `main.py` builds the upload list from crawled pages + `static/*.md`
only — so such a file would never be uploaded, and `prune_stale` would delete it
from the KB if it ever got there.)

`pdfplumber` is deliberately **not** a project dependency — this is a one-time
job, so it is pulled in per-invocation with `uv run --with` and `pyproject.toml`
stays minimal. Its `lines` table strategy reads the PDF's own ruling lines,
which keeps each tariff row intact (`Familie 2 Erw. + Kinder | 6 - 16 Jahre |
13,80€`). Do not swap in `pymupdf4llm`: it drags in a layout extension that runs
Tesseract OCR over these text-layer PDFs and corrupts the prices (`13,80€` came
out as `1380€`).

Output shape follows the crawled pages (see `clean.clean_markdown`): the h1 is
the page's place in the site hierarchy, sections are `##`, no hyperlinks.
"""

import logging
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clean import slug, strip_links  # noqa: E402

log = logging.getLogger("crawler")

PDF_DIR = Path(__file__).resolve().parent
STATIC_DIR = PDF_DIR.parent / "static"

# These four PDFs are the downloads of the Privatkunden/Baeder page, so they
# inherit its hierarchy; the document title becomes the last crumb.
HIERARCHY = "Privatkunden - Bäder"

MAX_CHUNK = 8192   # uploader.MAX_CHUNK — above this the KB API splits the file

# A line starting a new numbered clause ("(1) …", "1. …") always begins a
# paragraph, even when it sits tight under the previous line.
_CLAUSE_START = re.compile(r"^\s*(\(\d+\)|\d+\.)\s")


def _encloses(outer: tuple, inner: tuple) -> bool:
    """True if bbox `outer` strictly contains bbox `inner` (x0, top, x1, bottom)."""
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3]
            and (outer[2] - outer[0]) * (outer[3] - outer[1])
            > (inner[2] - inner[0]) * (inner[3] - inner[1]))


def _line_style(line: dict) -> tuple[float, bool]:
    """(font size, is-bold) of a pdfplumber text line, from its characters."""
    size = round(max(c["size"] for c in line["chars"]), 1)
    fonts = Counter(c["fontname"] for c in line["chars"])
    return size, "Bold" in fonts.most_common(1)[0][0]


def _cell(value: str | None) -> str:
    """Sanitise one table cell for markdown (no None, no newlines, no bare |)."""
    return re.sub(r"\s+", " ", (value or "").replace("|", "\\|")).strip()


def _table_markdown(rows: list[list[str | None]]) -> list[str]:
    """Render an extracted table as a GitHub-flavoured markdown table."""
    rows = [[_cell(c) for c in row] for row in rows if any(_cell(c) for c in row)]
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    head, *body = rows
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(row) + " |" for row in body]
    return out


def _repair_hyphens(text: str) -> str:
    """Undo the spaces pdfplumber inserts around hyphens in justified text.

    This PDF's justification widens letter gaps, so pdfplumber reports
    'störungs  -  und' and 'WLAN  -  Zugangs' where the document reads
    'störungs- und' / 'WLAN-Zugangs'. Only ASCII hyphens are touched, so a real
    en-dash sentence break ('–', as used in the Erläuterungen document) is left
    alone. Applied to body prose only — never to table cells (which legitimately
    contain ranges like '6 - 16 Jahre') and never to the hierarchy h1 (whose
    ' - ' separators would otherwise be glued shut).
    """
    # compound with a following conjunction keeps its space: "störungs- und"
    text = re.sub(r"(\w)\s+-\s+(und|oder|bzw)\b", r"\1- \2", text)
    text = re.sub(r"(\w)\s+-\s*([,;])", r"\1-\2", text)      # "Persönlichkeits- ,"
    text = re.sub(r"(\w)\s+-\s+(\w)", r"\1-\2", text)        # "E - Mails" -> "E-Mails"
    # hyphen already attached, gap before the next (capitalised) half of the
    # compound: "Gäste- WLAN" -> "Gäste-WLAN". A capital rules out the German
    # "Ein- und Ausgang" ellipsis, which must keep its space.
    text = re.sub(r"(\w)-\s+([A-ZÄÖÜ])", r"\1-\2", text)
    return text


def _paragraphs(chunks: list[str]) -> list[str]:
    """Join wrapped body lines into paragraphs, undoing German hyphenation."""
    text = ""
    for chunk in chunks:
        if not text:
            text = chunk
        elif text.endswith("-") and chunk[:1].islower():
            text = text[:-1] + chunk          # "ver-" + "antwortlichen"
        elif text.endswith("-") and chunk[:1].isupper():
            text += chunk                     # "Gäste-WLAN-" + "Zugangs" (compound)
        else:
            text += " " + chunk
    text = _repair_hyphens(re.sub(r"\s+", " ", text)).strip()
    return [text] if text else []


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae",
                          "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def title_of(pdf_path: Path) -> str:
    """Document title from the filename, with umlauts composed (NFC).

    Python's `listdir`/`glob` hand back macOS's *decomposed* form (NFD: 'a' +
    U+0308 combining diaeresis) even where `ls` shows it composed, so compose
    first — otherwise 'ä' is two code points and every consumer disagrees.
    """
    return unicodedata.normalize("NFC", pdf_path.stem)


def ascii_name(text: str) -> str:
    """Filename chunk, ASCII only: 'Tarifübersicht Freibäder' -> Tarifuebersicht_Freibaeder.

    Umlauts are transliterated the German way (ä->ae, ß->ss) rather than left as
    non-ASCII, so the filename cannot depend on Unicode normalisation. The
    knowledge base is keyed by filename and the uploader deletes/replaces by it,
    so a name that composes differently on macOS than on the GitLab runner would
    orphan the old file and upload a duplicate. Matches the site's own spelling
    (`Privatkunden/Baeder`). The h1 inside the file keeps the real umlauts.
    """
    folded = unicodedata.normalize("NFC", text).translate(_UMLAUTS)
    # strip any remaining accents (é -> e), then reuse the shared slug rules
    stripped = "".join(c for c in unicodedata.normalize("NFD", folded)
                       if not unicodedata.combining(c))
    return slug(stripped.encode("ascii", "ignore").decode())


def convert(pdf_path: Path) -> str:
    """PDF -> knowledge-base markdown: hierarchy h1, `##` sections, tables kept.

    Headings are found by type size, which is what these documents encode them
    with: the most common size is body text, anything larger (or bold at body
    size) is a heading, anything smaller is furniture ("Seite 1 von 4").
    """
    title = title_of(pdf_path)
    body_out: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        lines = [(page_no, line)
                 for page_no, page in enumerate(pdf.pages)
                 for line in page.extract_text_lines(extra_attrs=["size", "fontname"])]
        body_size = Counter(_line_style(line)[0] for _, line in lines).most_common(1)[0][0]

        # Ruled tables, keyed by page, plus their bounding boxes so the same
        # text is not emitted twice as prose. A page framed by a border box
        # yields a bogus outer "table" holding the entire page as one cell, so
        # any table enclosing another one is dropped in favour of the inner.
        tables: dict[int, list] = {}
        for page_no, page in enumerate(pdf.pages):
            found = page.find_tables({"vertical_strategy": "lines",
                                      "horizontal_strategy": "lines"})
            inner = [t for t in found
                     if not any(o is not t and _encloses(t.bbox, o.bbox) for o in found)]
            if inner:
                tables[page_no] = inner

        pending: list[str] = []          # body lines awaiting a paragraph flush
        heading: list[str] = []          # consecutive heading lines to merge
        heading_size = 0.0
        emitted_tables: set[tuple[int, int]] = set()

        def flush_body() -> None:
            nonlocal pending
            body_out.extend(_paragraphs(pending))
            body_out.extend([""] if pending else [])
            pending = []

        def flush_heading() -> None:
            nonlocal heading, heading_size
            if heading:
                # The document's own biggest heading duplicates the h1 we add
                # from the hierarchy, so it is dropped rather than repeated.
                if heading_size < max_heading:
                    body_out.extend([f"## {' '.join(heading)}", ""])
                heading, heading_size = [], 0.0

        heading_sizes = [s for s, bold in (_line_style(line) for _, line in lines)
                         if s > body_size or (s == body_size and bold)]
        max_heading = max(heading_sizes, default=body_size)

        for page_no, line in lines:
            for idx, table in enumerate(tables.get(page_no, [])):
                # Emit a table once, when the first line inside it is reached.
                top, bottom = table.bbox[1], table.bbox[3]
                if (page_no, idx) not in emitted_tables and top <= line["top"] <= bottom:
                    flush_body()
                    flush_heading()
                    body_out.extend(_table_markdown(table.extract()) + [""])
                    emitted_tables.add((page_no, idx))

            if any(t.bbox[1] <= line["top"] <= t.bbox[3] for t in tables.get(page_no, [])):
                continue                                  # already in a table

            size, bold = _line_style(line)
            text = re.sub(r"\s+", " ", line["text"]).strip()
            if not text or size < body_size:
                continue                                  # blank / page furniture

            if size > body_size or (size == body_size and bold):
                flush_body()
                if heading and size != heading_size:
                    flush_heading()
                heading.append(text)
                heading_size = size
                continue

            flush_heading()
            if pending and _CLAUSE_START.match(text):
                flush_body()                              # "(2) …" starts fresh
            pending.append(text)

        flush_body()
        flush_heading()

    md = "\n".join([f"# {HIERARCHY} - {title}", ""] + _demote_empty_headings(body_out))
    md = re.sub(r"\n{3,}", "\n\n", strip_links(md)).rstrip() + "\n"
    return md


def _demote_empty_headings(body: list[str]) -> list[str]:
    """Turn a heading with no content under it back into plain text.

    The tariff sheets set their validity period ('Saison 2026/2027') in heading
    type at the foot of the page; as a '##' it would be an empty section, so it
    is emitted as a normal line instead.
    """
    out = []
    for i, line in enumerate(body):
        if line.startswith("## "):
            rest = [x for x in body[i + 1:] if x.strip()]
            if not rest or rest[0].startswith("## "):
                out.append(line[3:])
                continue
        out.append(line)
    return out


def split_for_upload(md: str) -> list[str]:
    """Split a document that exceeds MAX_CHUNK into whole-section parts.

    The uploader keeps one chunk per file, so a file over the API's 8192-char
    cap is cut by the API instead — mid-section, and the tail piece loses the
    title line, leaving a retrieved chunk with no idea which document it came
    from. Splitting here at the document's own `##` boundaries keeps every
    section intact and gives each part its own '(Teil k von n)' h1.
    """
    if len(md) <= MAX_CHUNK:
        return [md]

    head, *body = md.splitlines()
    sections: list[list[str]] = []
    for line in body:
        if line.startswith("## ") and sections:
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        else:
            sections.append([line])
    blocks = [b for b in ("\n".join(s).strip("\n") for s in sections) if b]

    budget = MAX_CHUNK - len(head) - len(" (Teil 9 von 9)") - 4
    total = sum(len(b) + 2 for b in blocks)
    parts_wanted = -(-total // budget)                      # ceil
    target = total / parts_wanted

    parts: list[list[str]] = [[]]
    size = 0
    for block in blocks:
        too_big = size + len(block) > budget
        balanced = size >= target and len(parts) < parts_wanted
        if size and (too_big or balanced):
            parts.append([])
            size = 0
        parts[-1].append(block)
        size += len(block) + 2

    n = len(parts)
    out = []
    for i, part in enumerate(parts, start=1):
        out.append("\n".join([f"{head} (Teil {i} von {n})", ""] + part).rstrip() + "\n")
        if len(out[-1]) > MAX_CHUNK:
            log.warning("part %d/%d is still %d chars — a single section exceeds "
                        "the %d-char cap, so the API will split it",
                        i, n, len(out[-1]), MAX_CHUNK)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        log.error("no PDFs found in %s", PDF_DIR)
        return 1

    STATIC_DIR.mkdir(exist_ok=True)
    for pdf_path in pdfs:
        stem = f"{ascii_name(HIERARCHY.replace(' - ', '_'))}_{ascii_name(title_of(pdf_path))}"
        parts = split_for_upload(convert(pdf_path))
        for i, md in enumerate(parts, start=1):
            suffix = f"_Teil_{i}" if len(parts) > 1 else ""
            out = STATIC_DIR / f"{stem}{suffix}.md"
            out.write_text(md, encoding="utf-8")
            log.info("%s -> %s (%d chars)", pdf_path.name, out.name, len(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
