import logging
from contextlib import AsyncExitStack

from agents import Agent

from mcp_params import web_crawling_mcp_params
from prompts import scanner_instruction
from webpage_structure import Webpages
from pipeline import save_json
from agent_utils import create_mcp_servers, run_agent

log = logging.getLogger("crawler")


async def create_crawl_agent(stack: AsyncExitStack) -> Agent:
    servers = await create_mcp_servers(stack, web_crawling_mcp_params, timeout_seconds=120)
    agent = Agent(
        name="crawler",
        instructions=scanner_instruction,
        model="gpt-4.1-mini",
        mcp_servers=servers,
        output_type=Webpages,
    )
    return agent


async def launch_crawler(agent, topic, message):
    result = await run_agent(agent, message, label=topic)
    path = save_json(result, topic)
    log.info("saved JSON: %s", path)
