"""Entry point: crawl a site with crawl4ai (no LLM) and produce clean markdown.

    uv run python main.py                                  # crawl all sections
    uv run python main.py --sections Service_Abfall-ABC    # a subset

Flow per run (see CLAUDE.md for the architecture):

    sites/*.yaml -> config.load_site -> crawl.crawl_site (crawl4ai, retried)
        -> outputs/raw/<page>.md      (full page as markdown)
        -> clean.clean_markdown       (noise cut, link-free, hierarchy h1)
           or extract.EXTRACTORS      (sections with `extract:` — clean built
                                       from the fetched HTML instead)
        -> outputs/clean/<page>.md
        -> monitor.run_report         (per-page status/timing -> log + Pushover)

Outputs use stable, un-timestamped paths and are overwritten each run; the
previous clean file is measured just before overwrite to detect regressions.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import monitor
from clean import clean_markdown
from config import load_site
from crawl import crawl_site
from extract import EXTRACTORS

log = logging.getLogger("crawler")

OUTPUT_DIR = Path("outputs")
RAW_DIR = OUTPUT_DIR / "raw"
CLEAN_DIR = OUTPUT_DIR / "clean"


def save_outputs(pages) -> None:
    """Write raw + clean markdown per successful page; measure the previous
    clean file first so `monitor.regressions` has a baseline."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    for page in pages:
        if not page.ok:
            continue
        clean_path = CLEAN_DIR / f"{page.name}.md"
        old = monitor.md_metrics(clean_path.read_text(encoding="utf-8")) if clean_path.exists() else None

        (RAW_DIR / f"{page.name}.md").write_text(page.raw_markdown, encoding="utf-8")
        if page.extract:
            try:
                clean = EXTRACTORS[page.extract](page.html, page.url)
            except Exception as e:
                # a broken extractor (site relaunch?) is a failed page, not a crash
                page.error = f"extractor {page.extract}: {e}"
                continue
        else:
            clean = clean_markdown(page.raw_markdown, page.url)
        clean_path.write_text(clean, encoding="utf-8")

        page.clean_chars = len(clean)
        page.regression = monitor.regressions(old, monitor.md_metrics(clean))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    parser = argparse.ArgumentParser(description="LLM-free web crawler (crawl4ai).")
    parser.add_argument("--config", default="sites/ahk-heidekreis.yaml",
                        help="site YAML (default: %(default)s)")
    parser.add_argument("--sections", default="",
                        help="comma-separated section names (default: all)")
    args = parser.parse_args()

    site = load_site(args.config)
    only = [s.strip() for s in args.sections.split(",") if s.strip()] or None

    started = datetime.now(timezone.utc)
    pages = asyncio.run(crawl_site(site, only))
    save_outputs(pages)   # may mark a page failed (broken extractor)
    finished = datetime.now(timezone.utc)

    report = monitor.run_report(pages, started, finished)
    log.info("run report:\n%s", report)
    monitor.send_pushover(report, title="Crawler run")

    return 1 if any(not p.ok for p in pages) else 0


if __name__ == "__main__":
    sys.exit(main())
