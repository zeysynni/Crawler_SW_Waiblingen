import asyncio
import openai
from contextlib import AsyncExitStack

from agents.mcp import MCPServerStdio
from agents import Runner, RunConfig, ModelSettings, trace
from agents.models.openai_provider import OpenAIProvider

_openai_client: openai.AsyncOpenAI | None = None


def get_openai_client() -> openai.AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.AsyncOpenAI(max_retries=5)
    return _openai_client


async def create_mcp_servers(
    stack: AsyncExitStack, params_list: list, timeout_seconds: int = 30
) -> list:
    return [
        await stack.enter_async_context(
            MCPServerStdio(params, client_session_timeout_seconds=timeout_seconds)
        )
        for params in params_list
    ]


async def run_agent(
    agent,
    message,
    label: str,
    max_turns: int = 200,
    timeout_seconds: int = 300,
):
    run_config = RunConfig(
        model_provider=OpenAIProvider(openai_client=get_openai_client()),
        model_settings=ModelSettings(temperature=0),
    )
    with trace(label):
        result = await asyncio.wait_for(
            Runner.run(agent, input=message, max_turns=max_turns, run_config=run_config),
            timeout=timeout_seconds,
        )
    return result
