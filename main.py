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
from enrich import enrich_topic, resolve_subtopics
from monitor import read_metrics, topic_metrics, regressions, send_pushover, run_summary
from uploader import upload_topics, UploadHold

# check webpage structure first; if you change the structure, also update the
# json->md converter in pipeline.py
load_dotenv(override=True)

log = logging.getLogger("crawler")

MAX_RETRIES = 3   # hard ceiling: a topic is re-launched at most this many times


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
        "--upload",
        action="store_true",
        help="After crawling, upload each topic's Markdown to the knowledge base "
             "(replace semantics; needs AIGATEWAY_KEY). Unchanged files are skipped.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to wait between topics (rate-limit pacing; e.g. 60). "
             "Helps the batch stay under the per-minute token limit.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Times to re-launch a topic if its crawl fails, e.g. on a timeout "
             "(default: 2, so up to 3 attempts total). Set 0 to disable.",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=15.0,
        help="Seconds to wait before re-launching a failed topic (default: 15).",
    )
    return parser.parse_args()


def select_topics(site: Site, topics_arg: str | None) -> list[Topic]:
    """Resolve the --topics argument to Topic objects, failing fast on a bad name."""
    if not topics_arg:
        return site.topics
    names = [name.strip() for name in topics_arg.split(",") if name.strip()]
    return [site.topic(name) for name in names]   # KeyError on an unknown name


async def process_topic(agent, topic: Topic, root_url: str, make_pdf: bool = False) -> dict:
    json_path = OUTPUT_DIR / f"{topic.name}.json"
    baseline = read_metrics(json_path)   # previous crawl, before we overwrite it

    # Resolve subtopic labels to real sub-page URLs deterministically (no LLM
    # click-guessing) so the agent navigates to exact URLs.
    subtopic_urls = None
    if topic.subtopics:
        base = topic.url if topic.url.startswith("http") else root_url + topic.url
        subtopic_urls = resolve_subtopics(base, topic.subtopics)
        log.info("resolved %d/%d subtopics for '%s': %s",
                 len(subtopic_urls), len(topic.subtopics), topic.name,
                 ", ".join(s["label"] for s in subtopic_urls))

    prompt = get_user_prompt_structured_output(topic, root_url, subtopic_urls)
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
    return new


async def crawl_topic(agent, topic: Topic, root_url: str, make_pdf: bool,
                      attempts: int, backoff: float) -> dict:
    """Run one topic, re-launching it on failure (crawls fail transiently — a
    turn times out, the browser hiccups). Tries up to `attempts` times, waiting
    `backoff` seconds between tries. Re-raises the last error if all fail, so the
    caller records the topic as failed.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await process_topic(agent, topic, root_url, make_pdf)
        except Exception:
            if attempt < attempts:
                log.warning("topic '%s' attempt %d/%d failed; retrying in %.0fs",
                            topic.name, attempt, attempts, backoff)
                log.debug("failure detail for '%s'", topic.name, exc_info=True)
                await asyncio.sleep(backoff)
            else:
                raise   # exhausted every attempt — let the loop record the failure


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

    # Clamp retries to the hard ceiling so a topic is re-launched a bounded
    # number of times (at most MAX_RETRIES), never indefinitely.
    retries = max(0, min(args.retries, MAX_RETRIES))
    attempts = retries + 1

    log.info("crawling %d topic(s) from '%s' (up to %d attempt(s) each): %s",
             len(topics), site.site, attempts, ", ".join(t.name for t in topics))

    succeeded: list[tuple[str, dict]] = []
    failed: list[str] = []
    async with AsyncExitStack() as stack:
        agent = await create_crawl_agent(stack)
        for i, topic in enumerate(topics):
            try:
                metrics = await crawl_topic(agent, topic, site.root_url, args.pdf,
                                            attempts, args.retry_backoff)
                succeeded.append((topic.name, metrics))
            except Exception:
                # One bad topic must not abort the batch — log it and carry on.
                log.exception("topic '%s' failed after %d attempt(s)", topic.name, attempts)
                failed.append(topic.name)
                send_pushover(f"topic '{topic.name}' failed", title="❌ Crawl error")

            # Pace between topics to stay under the per-minute token limit.
            if args.delay and i < len(topics) - 1:
                log.info("waiting %.0fs before next topic", args.delay)
                await asyncio.sleep(args.delay)

    log.info("done: %d succeeded, %d failed", len(succeeded), len(failed))
    if failed:
        log.warning("failed topics: %s", ", ".join(failed))

    # Always send a short, detailed end-of-run summary (success confirmed too).
    summary = run_summary(succeeded, failed)
    log.info("run summary:\n%s", summary)
    title = "⚠️ Crawl finished (with failures)" if failed else "✅ Crawl finished"
    send_pushover(summary, title=title)

    # Upload phase (opt-in): push each successfully-crawled topic's Markdown to
    # the knowledge base (replace semantics; unchanged files skipped). On a
    # double failure the uploader raises UploadHold with state already saved —
    # exit non-zero so the scheduler resumes the still-pending topics ~24h later.
    if args.upload and succeeded:
        try:
            result = upload_topics([name for name, _ in succeeded])
            log.info("upload: %d uploaded, %d unchanged",
                     len(result["uploaded"]), len(result["skipped"]))
            send_pushover(
                f"uploaded {len(result['uploaded'])}, unchanged {len(result['skipped'])}",
                title="✅ Upload finished")
        except UploadHold as e:
            log.error("upload on hold: %s", e)
            send_pushover(f"upload failed twice, holding for retry: {e}",
                          title="⏸️ Upload on hold")
            sys.exit(2)   # scheduler re-runs later; saved state resumes the rest


if __name__ == "__main__":
    asyncio.run(main())
