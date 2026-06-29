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

from bs4 import BeautifulSoup

from pipeline import OUTPUT_DIR

log = logging.getLogger("crawler")


def fetch_html(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _norm(text: str) -> str:
    """Normalize a question for dedup (lowercase, collapse whitespace)."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


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
            add(btn.get_text(" ", strip=True), panel.get_text("\n", strip=True) if panel else "")

    # Native <details>/<summary>
    for det in soup.select("details"):
        summary = det.find("summary")
        if not summary:
            continue
        q = summary.get_text(" ", strip=True)
        # answer = the details text minus the summary line
        summary.extract()
        add(q, det.get_text("\n", strip=True))

    return qas


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


def _agent_questions(page: dict) -> set[str]:
    qs: set[str] = set()
    for block in page.get("blocks", []):
        for seg in block.get("segments", []):
            faqs = seg.get("faqs")
            if faqs:
                for qa in faqs.get("QAs", []):
                    qs.add(_norm(qa.get("question", "")))
    return qs


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

        have_q = _agent_questions(page)
        new_qas = [qa for qa in extract_expandable_qas(html) if _norm(qa["question"]) not in have_q]

        have_files = _agent_files_blob(page)
        new_files = [f for f in extract_pdf_files(html) if _norm(f) not in have_files]

        page.setdefault("blocks", [])
        if new_qas:
            page["blocks"].append(
                {"heading": "FAQ (auto-added)", "segments": [{"faqs": {"title": "FAQ", "QAs": new_qas}}]}
            )
        if new_files:
            page["blocks"].append(
                {"heading": "Downloads (auto-added)", "segments": [{"files": "\n".join(new_files)}]}
            )
        log.info("enrich %s: +%d FAQ, +%d files (agent had %d FAQ)",
                 url, len(new_qas), len(new_files), len(have_q))

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
