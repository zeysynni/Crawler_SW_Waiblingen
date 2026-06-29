import argparse
import asyncio
import json
import logging
import sys
from contextlib import AsyncExitStack

from dotenv import load_dotenv

from config import Site, Topic, load_site
from crawl_agent import create_crawl_agent, launch_crawler
from prompts import get_user_prompt_structured_output
from pipeline import write_markdown, to_pdf, OUTPUT_DIR
from enrich import enrich_topic
from monitor import read_metrics, topic_metrics, regressions, send_pushover

# check webpage structure first; if you change the structure, also update the
# json->md converter in pipeline.py
load_dotenv(override=True)

log = logging.getLogger("crawler")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-driven web crawler.")
    parser.add_argument(
        "--config",
        default="sites/waiblingen.yaml",
        help="Path to the site YAML config (default: sites/waiblingen.yaml).",
    )
    parser.add_argument(
        "--topics",
        default=None,
        help="Comma-separated topic names to crawl (default: every topic in the config).",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also export each topic's Markdown to PDF (requires pandoc + xelatex).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to wait between topics (rate-limit pacing; e.g. 60). "
             "Helps the batch stay under the per-minute token limit.",
    )
    return parser.parse_args()


def select_topics(site: Site, topics_arg: str | None) -> list[Topic]:
    """Resolve the --topics argument to Topic objects, failing fast on a bad name."""
    if not topics_arg:
        return site.topics
    names = [name.strip() for name in topics_arg.split(",") if name.strip()]
    return [site.topic(name) for name in names]   # KeyError on an unknown name


async def process_topic(agent, topic: Topic, root_url: str, make_pdf: bool = False) -> None:
    json_path = OUTPUT_DIR / f"{topic.name}.json"
    baseline = read_metrics(json_path)   # previous crawl, before we overwrite it

    prompt = get_user_prompt_structured_output(topic, root_url)
    await launch_crawler(agent, topic.name, prompt)
    enrich_topic(topic.name)   # union in deterministically-found FAQs/files the agent missed
    md_path = write_markdown(topic.name)
    if make_pdf and md_path is not None:
        log.info("wrote PDF: %s", to_pdf(md_path))

    # Alert if this crawl lost coverage vs the previous one (silent-change guard).
    new = topic_metrics(json.loads(json_path.read_text(encoding="utf-8")))
    drops = regressions(baseline, new)
    if drops:
        log.warning("regression in '%s': %s", topic.name, "; ".join(drops))
        send_pushover(f"{topic.name}: {'; '.join(drops)}", title="⚠️ Crawl regression")


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Resolve config + topics before touching the browser, so bad input fails
    # fast and cheap (no agent, no LLM calls).
    try:
        site = load_site(args.config)
        topics = select_topics(site, args.topics)
    except FileNotFoundError as e:
        sys.exit(f"error: {e}")
    except KeyError as e:
        sys.exit(f"error: {e.args[0]}")

    log.info("crawling %d topic(s) from '%s': %s",
             len(topics), site.site, ", ".join(t.name for t in topics))

    succeeded: list[str] = []
    failed: list[str] = []
    async with AsyncExitStack() as stack:
        agent = await create_crawl_agent(stack)
        for i, topic in enumerate(topics):
            try:
                await process_topic(agent, topic, site.root_url, make_pdf=args.pdf)
                succeeded.append(topic.name)
            except Exception:
                # One bad topic must not abort the batch — log it and carry on.
                log.exception("topic '%s' failed", topic.name)
                failed.append(topic.name)
                send_pushover(f"topic '{topic.name}' failed", title="❌ Crawl error")

            # Pace between topics to stay under the per-minute token limit.
            if args.delay and i < len(topics) - 1:
                log.info("waiting %.0fs before next topic", args.delay)
                await asyncio.sleep(args.delay)

    log.info("done: %d succeeded, %d failed", len(succeeded), len(failed))
    if failed:
        log.warning("failed topics: %s", ", ".join(failed))

    # Always send an end-of-run summary, so a successful run is confirmed too.
    summary = f"{len(succeeded)} ok, {len(failed)} failed"
    if failed:
        summary += f"\nfailed: {', '.join(failed)}"
    title = "⚠️ Crawl finished (with failures)" if failed else "✅ Crawl finished"
    send_pushover(summary, title=title)


if __name__ == "__main__":
    asyncio.run(main())
