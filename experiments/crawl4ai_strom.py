"""Standalone crawl4ai spike — allowlist-driven, NO LLM, no project code.

Crawl targets come from experiments/crawl_targets.yaml: each section names a
base page (crawled too — it has its own content) and the sub-pages to crawl,
by their visible link text on the base page. Anything not listed is not
crawled. Pure crawl4ai basics:

  * AsyncWebCrawler (Playwright, headless)
  * base page crawl -> resolve sub-page labels against the links crawl4ai
    extracted (visible text match) -> crawl those URLs. Deterministic, no
    LLM navigation, no deep-crawl heuristics.
  * DefaultMarkdownGenerator — raw markdown straight from the HTML
  * clean_markdown() — markdown-level noise cut (no PruningContentFilter):
    keep the heading-led sections (# title .. ###), drop the preamble noise
    before the first heading (Sprungmarken, Menü, breadcrumbs) and the tail
    from the footer link list / "Wir nutzen Cookies ..." onward. Links are
    flattened to plain text, images dropped, and the page's h1 is replaced
    by its site hierarchy ("# Privatkunden - Strom - <title>").

Outputs (overwritten each run):
  experiments/crawl4ai_out/raw/<page>.md     full page as markdown
  experiments/crawl4ai_out/clean/<page>.md   heading-led sections only

Run:  uv run python experiments/crawl4ai_strom.py
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml
from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

TARGETS_FILE = Path(__file__).parent / "crawl_targets.yaml"
OUT_DIR = Path(__file__).parent / "crawl4ai_out"
RAW_DIR = OUT_DIR / "raw"
CLEAN_DIR = OUT_DIR / "clean"

# The site footer/cookie tail is identical on every page (one CMS template).
# First line of the footer's quick-link list:
_FOOTER_START = re.compile(
    r"^\s*\*\s*\[\s*Kontakt\s*\]\(https://www\.stadtwerke-waiblingen\.de/kontakt\b"
)
# Cookie-consent overlay text (everything from here on is noise):
_COOKIE_START = "Wir nutzen Cookies und andere Technologien"


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


def slug(url: str) -> str:
    """URL -> safe filename, e.g. .../Privatkunden/Strom/oekostrom -> Privatkunden_Strom_oekostrom"""
    tail = unquote(urlparse(url).path).strip("/") or "index"
    return re.sub(r"[^\w\-äöüÄÖÜß]+", "_", tail)


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def resolve_subpages(result, labels: list[str]) -> list[str]:
    """Match sub-page labels against the visible text of the base page's links."""
    by_text: dict[str, str] = {}
    for link in result.links.get("internal", []):
        text = _norm(link.get("text") or "")
        if text and text not in by_text:
            by_text[text] = link["href"]

    urls = []
    for label in labels:
        href = by_text.get(_norm(label))
        if href:
            urls.append(href)
        else:
            print(f"  !! no link with text {label!r} on {result.url} — skipped")
    return urls


def save_page(result) -> None:
    raw = result.markdown.raw_markdown or ""
    clean = clean_markdown(raw, result.url)
    name = slug(result.url)
    (RAW_DIR / f"{name}.md").write_text(raw, encoding="utf-8")
    (CLEAN_DIR / f"{name}.md").write_text(clean, encoding="utf-8")
    print(f"{len(raw):>7}  {len(clean):>7}  {result.url}")


async def main() -> None:
    targets = yaml.safe_load(TARGETS_FILE.read_text(encoding="utf-8"))
    root_url = targets["root_url"].rstrip("/")

    config = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(),
        cache_mode=CacheMode.BYPASS,    # always fetch fresh
        verbose=False,
    )

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{'raw':>7}  {'clean':>7}  url")

    async with AsyncWebCrawler() as crawler:
        for section in targets["sections"]:
            base_url = f"{root_url}/{section['path'].strip('/')}"

            # the base page is a crawl target itself, not just a link source
            base = await crawler.arun(base_url, config=config)
            if not base.success:
                print(f"  FAILED {base_url}: {base.error_message}")
                continue
            save_page(base)

            sub_urls = resolve_subpages(base, section.get("subpages", []))
            for result in await crawler.arun_many(sub_urls, config=config):
                if result.success:
                    save_page(result)
                else:
                    print(f"  FAILED {result.url}: {result.error_message}")

    print(f"\nwrote raw/*.md and clean/*.md to {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
