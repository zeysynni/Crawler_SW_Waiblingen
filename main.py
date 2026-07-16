"""Entry point: crawl a site with crawl4ai (no LLM) and produce KB-ready markdown.

    uv run python main.py                                  # crawl all sections
    uv run python main.py --sections Privatkunden_Strom    # a subset
    uv run python main.py --upload                         # + push clean/*.md to the KB

Flow per run (see CLAUDE.md for the architecture):

    sites/*.yaml -> config.load_site -> crawl.crawl_site (crawl4ai, retried)
        -> outputs/raw/<page>.md      (full page as markdown)
        -> clean.clean_markdown       (noise cut, link-free, hierarchy h1)
        -> outputs/clean/<page>.md    (+ static/*.md copied in verbatim)
        -> uploader.upload_pages      (--upload only; one chunk per file, replace)
        -> monitor.run_report         (per-page status/timing, uploaded/pruned
                                       names first -> log + Pushover)

Outputs use stable, un-timestamped paths and are overwritten each run; the
previous clean file is measured just before overwrite to detect regressions.
"""

import argparse
import asyncio
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import monitor
import uploader
from clean import clean_markdown
from config import load_site
from crawl import crawl_site

log = logging.getLogger("crawler")

OUTPUT_DIR = Path("outputs")
RAW_DIR = OUTPUT_DIR / "raw"
CLEAN_DIR = OUTPUT_DIR / "clean"
STATIC_DIR = Path("static")   # hand-written pages (e.g. Kundenportal) uploaded as-is


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

        clean = clean_markdown(page.raw_markdown, page.url)
        (RAW_DIR / f"{page.name}.md").write_text(page.raw_markdown, encoding="utf-8")
        clean_path.write_text(clean, encoding="utf-8")

        page.clean_chars = len(clean)
        page.regression = monitor.regressions(old, monitor.md_metrics(clean))


def copy_static() -> list[str]:
    """Copy hand-written pages into the clean outputs (uploaded like any page)."""
    names = []
    if STATIC_DIR.is_dir():
        for src in sorted(STATIC_DIR.glob("*.md")):
            shutil.copy(src, CLEAN_DIR / src.name)
            names.append(src.stem)
            log.info("static page %s -> %s", src.name, CLEAN_DIR / src.name)
    return names


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    parser = argparse.ArgumentParser(description="LLM-free web crawler (crawl4ai).")
    parser.add_argument("--config", default="sites/waiblingen.yaml",
                        help="site YAML (default: %(default)s)")
    parser.add_argument("--sections", default="",
                        help="comma-separated section names (default: all)")
    parser.add_argument("--upload", action="store_true",
                        help="push clean/*.md to the knowledge base after the crawl")
    args = parser.parse_args()

    site = load_site(args.config)
    only = [s.strip() for s in args.sections.split(",") if s.strip()] or None

    started = datetime.now(timezone.utc)
    pages = asyncio.run(crawl_site(site, only))
    save_outputs(pages)
    static_names = copy_static()
    finished = datetime.now(timezone.utc)

    failed = [p for p in pages if not p.ok]

    # upload before reporting, so the run report can name the files the
    # upload actually changed remotely (new/pruned)
    summary = hold = None
    if args.upload:
        names = [p.name for p in pages if p.ok] + static_names
        try:
            # prune only on full runs with zero failures — a subset or a failed
            # page must not delete pages remotely (a transient fetch failure is
            # not "removed from the site YAML")
            summary = uploader.upload_pages(names, prune=only is None and not failed)
        except uploader.UploadHold as e:
            hold = e

    report = monitor.run_report(pages, started, finished, upload=summary)
    log.info("run report:\n%s", report)
    monitor.send_pushover(report, title="Crawler run")

    if args.upload:
        if hold:
            log.error("upload on hold: %s", hold)
            monitor.send_pushover(f"upload HOLD: {hold}", title="Crawler upload")
            return 1
        log.info("upload: %d uploaded, %d skipped, %d pruned",
                 len(summary["uploaded"]), len(summary["skipped"]), len(summary["pruned"]))
        monitor.send_pushover(
            f"upload ok: {len(summary['uploaded'])} new, "
            f"{len(summary['skipped'])} unchanged, {len(summary['pruned'])} pruned",
            title="Crawler upload",
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
