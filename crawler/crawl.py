"""Deterministic site crawl via crawl4ai — no LLM anywhere.

For each configured section (see `config.py` / `sites/*.yaml`):

1. fetch the base page (it is a crawl target itself, not just a link source)
2. resolve the section's `subpages` labels against the links crawl4ai
   extracted from that fetch (visible-text match: exact, else unique prefix,
   else unique substring — a miss or an ambiguity is reported, never guessed)
3. fetch the resolved sub-pages one by one (sequential on purpose — a full
   run takes ~2 min; correct first, fast later)

Every fetch is retried once on failure. Each page comes back as a
`PageResult` carrying the raw markdown (or the error) plus start/end
timestamps for the run report.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from clean import slug
from config import Section, Site

log = logging.getLogger("crawler")

RETRIES = 1   # re-fetch attempts per page on top of the first try


@dataclass
class PageResult:
    """Outcome of one page fetch, success or not."""
    name: str                     # output-file base name (from section path + label)
    url: str
    raw_markdown: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    notes: list[str] = field(default_factory=list)   # e.g. unresolved labels
    links: dict = field(default_factory=dict)        # crawl4ai link map (base pages)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def duration(self) -> float:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def resolve_subpages(links: list[dict], labels: list[str], page_url: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Match sub-page labels against the (internal) links of the base page.

    `links` are crawl4ai link dicts ({"href": ..., "text": ...}). Teaser cards
    merge title + tagline into one link text ('Fernwärme Bedarfsgerecht und
    günstig'), so after an exact match we fall back to a prefix and then a
    substring match — each accepted only if it hits exactly one target URL.
    Returns (resolved (label, url) pairs, human-readable problems).
    """
    pairs: list[tuple[str, str]] = []          # (normalized text, href)
    seen_hrefs: set[str] = set()
    for link in links:
        text = _norm(link.get("text") or "")
        href = link.get("href")
        if text and href and href not in seen_hrefs:
            seen_hrefs.add(href)
            pairs.append((text, href))

    resolved, problems = [], []
    for label in labels:
        want = _norm(label)
        for candidates in (
            {h for t, h in pairs if t == want},
            {h for t, h in pairs if t.startswith(want + " ")},
            {h for t, h in pairs if want in t},
        ):
            if len(candidates) == 1:
                resolved.append((label, candidates.pop()))
                break
            if len(candidates) > 1:
                problems.append(f"label {label!r} is ambiguous on {page_url} "
                                f"({len(candidates)} links)")
                break
        else:
            problems.append(f"no link with text {label!r} on {page_url}")
    return resolved, problems


def _run_config() -> CrawlerRunConfig:
    return CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(),
        cache_mode=CacheMode.BYPASS,     # always fetch fresh
        verbose=False,
    )


async def _fetch(crawler: AsyncWebCrawler, name: str, url: str) -> PageResult:
    """Fetch one page, retrying once; always returns a PageResult."""
    page = PageResult(name=name, url=url, started_at=_now())
    last_error = "unknown"
    for attempt in range(1 + RETRIES):
        try:
            result = await crawler.arun(url, config=_run_config())
            if result.success:
                page.raw_markdown = result.markdown.raw_markdown or ""
                page.links = result.links      # used for subpage resolution
                page.finished_at = _now()
                return page
            last_error = str(result.error_message)
        except Exception as e:                 # noqa: BLE001 — report, don't crash the run
            last_error = f"{type(e).__name__}: {e}"
        log.warning("fetch %s attempt %d failed: %s", url, attempt + 1, last_error)
    page.error = last_error
    page.finished_at = _now()
    return page


async def crawl_section(crawler: AsyncWebCrawler, section: Section, root_url: str) -> list[PageResult]:
    """Crawl one section: base page + resolved sub-pages."""
    base = await _fetch(crawler, section.name, section.base_url(root_url))
    results = [base]
    if not base.ok:
        if section.subpages:
            base.notes.append(f"{len(section.subpages)} subpages not attempted (base failed)")
        return results

    resolved, problems = resolve_subpages(
        base.links.get("internal", []), section.subpages, base.url
    )
    base.notes.extend(problems)
    for label, url in resolved:
        results.append(await _fetch(crawler, f"{section.name}_{slug(label)}", url))
    return results


async def crawl_site(site: Site, only: list[str] | None = None) -> list[PageResult]:
    """Crawl all (or `only` the named) sections of a site."""
    sections = site.sections
    if only:
        sections = [site.section(name) for name in only]

    results: list[PageResult] = []
    async with AsyncWebCrawler() as crawler:
        for section in sections:
            log.info("section %s", section.name)
            results.extend(await crawl_section(crawler, section, site.root_url))
    return results
