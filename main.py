import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack

from dotenv import load_dotenv

from config import Site, Topic, load_site
from crawl_agent import create_crawl_agent, launch_crawler
from prompts import get_user_prompt_structured_output
from pipeline import write_markdown, to_pdf

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
    return parser.parse_args()


def select_topics(site: Site, topics_arg: str | None) -> list[Topic]:
    """Resolve the --topics argument to Topic objects, failing fast on a bad name."""
    if not topics_arg:
        return site.topics
    names = [name.strip() for name in topics_arg.split(",") if name.strip()]
    return [site.topic(name) for name in names]   # KeyError on an unknown name


async def process_topic(agent, topic: Topic, root_url: str, make_pdf: bool = False) -> None:
    prompt = get_user_prompt_structured_output(topic, root_url)
    await launch_crawler(agent, topic.name, prompt)
    md_path = write_markdown(topic.name)
    if make_pdf and md_path is not None:
        log.info("wrote PDF: %s", to_pdf(md_path))


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
        for topic in topics:
            try:
                await process_topic(agent, topic, site.root_url, make_pdf=args.pdf)
                succeeded.append(topic.name)
            except Exception:
                # One bad topic must not abort the batch — log it and carry on.
                log.exception("topic '%s' failed", topic.name)
                failed.append(topic.name)

    log.info("done: %d succeeded, %d failed", len(succeeded), len(failed))
    if failed:
        log.warning("failed topics: %s", ", ".join(failed))


if __name__ == "__main__":
    asyncio.run(main())
