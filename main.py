import asyncio
from contextlib import AsyncExitStack
from dotenv import load_dotenv

from crawl_agent import create_crawl_agent, launch_crawler
from prompts import get_user_prompt_structured_output
from config import structure
from utils import save_markdown_from_json


# check webpage structure first, if you want to change structure, dont forget to change the func json->md in utils.py
load_dotenv(override=True)

async def process_topic(agent, topic: str, topic_config: dict) -> None:
    url = topic_config.get("url")
    subparts = topic_config.get("subpart", [])

    for subpart in subparts:
        prompt = get_user_prompt_structured_output(url, subpart)
        await launch_crawler(agent, topic, prompt)

    # Save once per topic
    save_markdown_from_json(
        json_path=f"outputs/{topic}.json",
        md_path=f"outputs/{topic}.md"
    )


async def main():
    async with AsyncExitStack() as stack:
        agent = await create_crawl_agent(stack)

        # One failing topic must not kill the whole batch (weekly CI run):
        # crawl the rest, then exit non-zero so the pipeline reports the failure.
        failed = []
        for topic, topic_config in structure.items():
            try:
                await process_topic(agent, topic, topic_config)
            except Exception as e:
                print(f"ERROR: topic '{topic}' failed: {e}")
                failed.append(topic)

        if failed:
            raise SystemExit(
                f"{len(failed)}/{len(structure)} topics failed: {', '.join(failed)}"
            )


if __name__ == "__main__":
    asyncio.run(main())