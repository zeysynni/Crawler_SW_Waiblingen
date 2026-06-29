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
        # Higher retry count so the client waits out 429 rate limits (it honors
        # the Retry-After hint) instead of failing a topic mid-crawl.
        _openai_client = openai.AsyncOpenAI(max_retries=8)
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
    max_turns: int = 80,          # cap loops: a single page shouldn't need many turns
    timeout_seconds: int = 240,   # fail fast if a topic starts looping
):
    # gpt-5.x reasoning models reject a custom temperature, and they tend to
    # return final output without browsing unless tool use is forced.
    model_name = str(getattr(agent, "model", "") or "")
    if model_name.startswith("gpt-5"):
        model_settings = ModelSettings(tool_choice="required")
    else:
        model_settings = ModelSettings(temperature=0)
    run_config = RunConfig(
        model_provider=OpenAIProvider(openai_client=get_openai_client()),
        model_settings=model_settings,
    )
    with trace(label):
        result = await asyncio.wait_for(
            Runner.run(agent, input=message, max_turns=max_turns, run_config=run_config),
            timeout=timeout_seconds,
        )
    return result
