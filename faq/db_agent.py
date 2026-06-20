import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import AsyncExitStack

from agents import Agent
from agent_utils import create_mcp_servers, run_agent


async def create_db_agent(stack: AsyncExitStack, ingest_instruction: str, db_params: list) -> Agent:
    servers = await create_mcp_servers(stack, db_params, timeout_seconds=30)
    agent = Agent(
        name="Databank",
        instructions=ingest_instruction,
        model="gpt-4.1-mini",
        mcp_servers=servers,
    )
    return agent


async def create_faq_agent(stack: AsyncExitStack, qa_instruction: str, db_params: list) -> Agent:
    servers = await create_mcp_servers(stack, db_params, timeout_seconds=30)
    agent = Agent(
        name="FAQ",
        instructions=qa_instruction,
        model="gpt-4.1-mini",
        mcp_servers=servers,
    )
    return agent


async def launch_DB(agent, topic, message):
    return await run_agent(agent, message, label=f"{topic}_db")
