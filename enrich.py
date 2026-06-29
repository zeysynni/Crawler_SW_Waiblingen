"""Deterministic enrichment of crawl output.

The LLM agent is good at prose and page structure, but its capture of
FAQ/accordion Q&As and downloadable files is *stochastic* — it misses some
between runs. Those two things are structured and server-rendered in the HTML,
so we extract them deterministically (100% consistent, no model variance) and
inject them into the crawl JSON, replacing the agent's partial versions.

This runs after the agent crawl, once per topic. It re-fetches each visited
page's HTML (cheap, no browser/LLM) and parses it with BeautifulSoup.
"""

import json
import logging
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

from pipeline import OUTPUT_DIR

log = logging.getLogger("crawler")


def fetch_html(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def extract_accordion_qas(html: str) -> list[dict]:
    """Every Bootstrap accordion question/answer pair on the page.

    Question = the `.accordion-button` text; answer = the `.accordion-collapse`
    panel text. Deduped by question; skips entries whose answer is empty or just
    echoes the question.
    """
    soup = BeautifulSoup(html, "html.parser")
    qas: list[dict] = []
    seen: set[str] = set()
    for item in soup.select(".accordion-item"):
        btn = item.select_one(".accordion-button")
        panel = item.select_one(".accordion-collapse")
        if not btn:
            continue
        q = btn.get_text(" ", strip=True)
        a = panel.get_text("\n", strip=True) if panel else ""
        if q and a and a != q and q not in seen:
            seen.add(q)
            qas.append({"question": q, "answer": a})
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


def enrich_topic(topic: str, output_dir: Path | str = OUTPUT_DIR) -> None:
    """Replace the agent's FAQ/file capture with deterministic HTML extraction.

    For each page in ``<output_dir>/<topic>.json``: re-fetch the HTML, extract
    all accordion Q&As and PDF files, drop the agent's (partial) faqs/files, and
    append authoritative FAQ + Downloads blocks. Prose/contacts are untouched.
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

        qas = extract_accordion_qas(html)
        files = extract_pdf_files(html)

        # Drop the agent's stochastic faqs/files; the deterministic ones replace them.
        for block in page.get("blocks", []):
            for seg in block.get("segments", []):
                seg["faqs"] = None
                seg["files"] = None

        page.setdefault("blocks", [])
        if qas:
            page["blocks"].append(
                {"heading": "FAQ", "segments": [{"faqs": {"title": "FAQ", "QAs": qas}}]}
            )
        if files:
            page["blocks"].append(
                {"heading": "Downloads", "segments": [{"files": "\n".join(files)}]}
            )
        log.info("enrich %s: %d FAQ, %d files", url, len(qas), len(files))

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
