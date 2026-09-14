"""Entry point: crawl a site with crawl4ai (no LLM) and produce clean markdown.

    uv run python main.py                                  # crawl all sections
    uv run python main.py --sections Service_Abfall-ABC    # a subset

Flow per run (see CLAUDE.md for the architecture):

    sites/*.yaml -> config.load_site -> crawl.crawl_site (crawl4ai, retried)
        -> outputs/raw/<page>.md      (full page as markdown)
        -> render.render_clean       (clean.clean_markdown, or the section's
                                      extract.EXTRACTORS entry for `extract:`
                                      sections — built from the fetched HTML)
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
from numpy import signedinteger
from tqdm.utils import SimpleTextIOWrapper

import monitor
from config import load_site
from crawl import crawl_site
from render import render_clean

log = logging.getLogger("crawler")

OUTPUT_DIR = Path("outputs")
RAW_DIR = OUTPUT_DIR / "raw"
CLEAN_DIR = OUTPUT_DIR / "clean"


def save_outputs(pages, site) -> None:
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
        try:
            clean = render_clean(page, site)
        except Exception as e:        # noqa: BLE001 — see render.render_clean
            # a broken extractor (site relaunch?) is a failed page, not a crash
            page.error = f"extractor {page.extract}: {e}"
            continue
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
    save_outputs(pages, site)   # may mark a page failed (broken extractor)
    finished = datetime.now(timezone.utc)

    report = monitor.run_report(pages, started, finished)
    log.info("run report:\n%s", report)
    monitor.send_pushover(report, title="Crawler run")

    return 1 if any(not p.ok for p in pages) else 0


if __name__ == "__main__":
    sys.exit(main())
