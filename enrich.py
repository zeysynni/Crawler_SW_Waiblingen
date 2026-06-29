"""Deterministic enrichment of crawl output.

The LLM agent generalizes across sites and adapts to layout changes (important
for a config-driven crawler whose targets change without notice). But its
capture of FAQ/accordion Q&As is *stochastic* — it misses some between runs.

So we ADD deterministically-extracted Q&As and files on top of the agent's
output (a union — we never remove what the agent found). On sites that match a
common expandable pattern this guarantees completeness; on exotic sites the
agent's own capture still carries it. Neither is a single point of failure.

This runs after the agent crawl, once per topic. It re-fetches each visited
page's HTML (cheap, no browser/LLM) and parses it with BeautifulSoup.
"""

import json
import logging
import re
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString

from pipeline import OUTPUT_DIR

log = logging.getLogger("crawler")


def fetch_html(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _norm(text: str) -> str:
    """Normalize a question for dedup (lowercase, collapse whitespace)."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _table_to_md(table) -> str:
    """Render an HTML <table> as a GitHub-flavored Markdown table."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * ncol) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _panel_text(panel) -> str:
    """Text of an expandable panel, with any <table> rendered as Markdown
    (instead of being flattened into one cell-per-line blob)."""
    if panel is None:
        return ""
    md_tables = []
    for t in panel.find_all("table"):
        md = _table_to_md(t)
        if md:
            md_tables.append(md)
        t.decompose()  # remove so it isn't also flattened into the plain text
    text = panel.get_text("\n", strip=True)
    return "\n\n".join(p for p in [text, *md_tables] if p).strip()


def extract_expandable_qas(html: str) -> list[dict]:
    """Every expandable question/answer pair, across common patterns.

    Covers Bootstrap accordions (`.accordion-item`) and native `<details>`.
    Deduped by question; skips entries whose answer is empty or echoes the
    question. Other widgets fall back to the LLM's own capture (union).
    """
    soup = BeautifulSoup(html, "html.parser")
    qas: list[dict] = []
    seen: set[str] = set()

    def add(q: str, a: str) -> None:
        q, a = q.strip(), a.strip()
        if q and a and _norm(a) != _norm(q) and _norm(q) not in seen:
            seen.add(_norm(q))
            qas.append({"question": q, "answer": a})

    # Bootstrap accordions
    for item in soup.select(".accordion-item"):
        btn = item.select_one(".accordion-button")
        panel = item.select_one(".accordion-collapse")
        if btn:
            add(btn.get_text(" ", strip=True), _panel_text(panel))

    # Native <details>/<summary>
    for det in soup.select("details"):
        summary = det.find("summary")
        if not summary:
            continue
        q = summary.get_text(" ", strip=True)
        summary.extract()   # so the summary line isn't part of the answer
        add(q, _panel_text(det))

    return qas


def extract_prose_sections(html: str) -> list[dict]:
    """Top-level (`<h2>`) prose sections in document order: {heading, text}.

    Skips sections that contain an accordion (those are handled by FAQ
    extraction). Used as a backstop to recover whole sections the LLM dropped.
    """
    soup = BeautifulSoup(html, "html.parser")
    sections: list[dict] = []
    for h in soup.find_all("h2"):
        title = h.get_text(" ", strip=True)
        if not title:
            continue
        texts: list[str] = []
        has_accordion = False
        for node in h.next_elements:
            name = getattr(node, "name", None)
            if name == "h2":
                break  # reached the next section
            if name and "accordion-item" in (node.get("class") or []):
                has_accordion = True
            if isinstance(node, NavigableString):
                s = str(node).strip()
                if s:
                    texts.append(s)
        if has_accordion:
            continue  # FAQ section — handled elsewhere
        sections.append({"heading": title, "text": "\n".join(texts).strip()[:4000]})
    return sections


def _looks_like_prose(text: str) -> bool:
    """True if `text` is real explanatory prose, not an empty/link-label section.

    Drops URL/path tokens, then requires a decent length AND sentence
    punctuation — link lists ("Anmeldung/Einzug …") and empty file sections
    have neither, so they're skipped by the missed-section backstop.
    """
    cleaned = re.sub(r"\S*/\S*", " ", text)          # remove url/path tokens
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned) >= 40 and any(p in cleaned for p in ".?!")


def _llm_text_blob(page: dict) -> str:
    """All text the agent captured for a page, normalized — to detect what it missed."""
    parts: list[str] = []
    for block in page.get("blocks", []):
        parts.append(block.get("heading", "") or "")
        for seg in block.get("segments", []):
            parts.append(seg.get("subheading", "") or "")
            parts.append(seg.get("text", "") or "")
            if seg.get("faqs"):
                parts.append(seg["faqs"].get("title", "") or "")
                for qa in seg["faqs"].get("QAs", []):
                    parts.append(qa.get("question", ""))
    return _norm(" ".join(parts))


def extract_pdf_files(html: str) -> list[str]:
    """Display name (or filename) of every PDF link on the page, deduped."""
    soup = BeautifulSoup(html, "html.parser")
    files: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        if ".pdf" in a["href"].lower():
            name = a.get_text(" ", strip=True) or a["href"].rsplit("/", 1)[-1]
            if name and name not in seen:
                seen.add(name)
                files.append(name)
    return files


def _strip_redundant_faq(page: dict, det_norms: set[str]) -> int | None:
    """Remove the agent's representations of questions the deterministic set covers.

    Drops matching faqs QAs, bare question lines inside text, and a subheading
    that is itself one of the questions — so the authoritative FAQ block added
    afterwards doesn't duplicate them.

    Returns the index of the block where the agent's FAQ section sat (so the
    clean FAQ can be inserted there, in its original position), or None if the
    agent had no FAQ signal (then the caller appends at the end).
    """
    anchor: int | None = None
    for idx, block in enumerate(page.get("blocks", [])):
        had_faq = bool(re.search(r"faq|fragen", _norm(block.get("heading", ""))))
        for seg in block.get("segments", []):
            if seg.get("faqs"):
                kept = [qa for qa in seg["faqs"].get("QAs", [])
                        if _norm(qa.get("question", "")) not in det_norms]
                seg["faqs"] = {"title": seg["faqs"].get("title"), "QAs": kept} if kept else None
                had_faq = True
            if seg.get("text"):
                lines = [ln for ln in seg["text"].splitlines() if _norm(ln) not in det_norms]
                if len(lines) != len(seg["text"].splitlines()):
                    had_faq = True
                seg["text"] = "\n".join(lines).strip() or None
            sub = seg.get("subheading")
            if sub and _norm(sub) in det_norms:
                seg["subheading"] = None
                had_faq = True
            elif sub and re.search(r"faq|fragen", _norm(sub)):
                had_faq = True
        if had_faq and anchor is None:
            anchor = idx
    return anchor


def _agent_files_blob(page: dict) -> str:
    parts = []
    for block in page.get("blocks", []):
        for seg in block.get("segments", []):
            if seg.get("files"):
                parts.append(seg["files"])
    return _norm("\n".join(parts))


def enrich_topic(topic: str, output_dir: Path | str = OUTPUT_DIR) -> None:
    """Add deterministically-found FAQ Q&As and files the agent missed (union).

    For each page in ``<output_dir>/<topic>.json``: re-fetch the HTML, extract
    expandable Q&As and PDF files, and append only the ones NOT already captured
    by the agent. The agent's output is never removed — so if the deterministic
    pass finds nothing (exotic site), nothing is lost.
    """
    path = Path(output_dir) / f"{topic}.json"
    if not path.exists():
        log.warning("enrich: %s not found, skipping", path)
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    for page in data.get("pages", []):
        url = page.get("url")
        if not url:
            continue
        try:
            html = fetch_html(url)
        except Exception as e:
            log.warning("enrich: could not fetch %s (%s); keeping agent output", url, e)
            continue

        det_qas = extract_expandable_qas(html)
        det_norms = {_norm(qa["question"]) for qa in det_qas}

        # Where deterministic extraction found accordions, it's authoritative:
        # strip the agent's redundant representations of those same questions
        # (partial faqs entries, bare question lines, question-as-subheading) so
        # the clean FAQ block below doesn't duplicate them. If it found nothing
        # (exotic page), we leave the agent's output untouched.
        anchor = _strip_redundant_faq(page, det_norms) if det_qas else None

        have_files = _agent_files_blob(page)
        new_files = [f for f in extract_pdf_files(html) if _norm(f) not in have_files]

        page.setdefault("blocks", [])
        if det_qas:
            faq_block = {"heading": "FAQ", "segments": [{"faqs": {"title": "FAQ", "QAs": det_qas}}]}
            if anchor is not None:
                page["blocks"].insert(anchor + 1, faq_block)   # in its original position
            else:
                page["blocks"].append(faq_block)               # no anchor — best effort
        if new_files:
            page["blocks"].append(
                {"heading": "Downloads", "segments": [{"files": "\n".join(new_files)}]}
            )

        # Backstop: recover whole <h2> sections the agent dropped entirely,
        # inserted in document order (after the nearest preceding section the
        # agent did capture).
        blob = _llm_text_blob(page)
        sections = extract_prose_sections(html)
        added = 0
        for i, sec in enumerate(sections):
            if _norm(sec["heading"]) in blob or not _looks_like_prose(sec["text"]):
                continue  # captured already, or not substantial prose (link list / empty) to bother
            insert_at = len(page["blocks"])
            for j in range(i - 1, -1, -1):
                prev = _norm(sections[j]["heading"])
                idx = next((bi for bi, b in enumerate(page["blocks"])
                            if prev and prev in _norm(b.get("heading", ""))), None)
                if idx is not None:
                    insert_at = idx + 1
                    break
            page["blocks"].insert(
                insert_at, {"heading": sec["heading"], "segments": [{"text": sec["text"]}]}
            )
            added += 1

        log.info("enrich %s: %d FAQ, +%d files, +%d sections", url, len(det_qas), len(new_files), added)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
