import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import AsyncExitStack
from dotenv import load_dotenv
from db_agent import launch_DB, create_faq_agent
from mcp_params import sqlite_db_params, kb_db_params
from prompts import qa_instruction_sql, qa_instruction_kg
import gradio as gr

load_dotenv(override=True)

# switch db between kb for knowledge-graph and sql
db_name = "kb"  # "sql"

# Agent and its MCP server connection are created once and reused across all chat messages.
_stack: AsyncExitStack | None = None
_agent = None


async def _ensure_agent():
    global _stack, _agent
    if _agent is None:
        _stack = AsyncExitStack()
        await _stack.__aenter__()
        _agent = await create_faq_agent(
            _stack,
            qa_instruction=qa_instruction_sql if db_name == "sql" else qa_instruction_kg,
            db_params=sqlite_db_params if db_name == "sql" else kb_db_params,
        )


async def ask(question: str, history: list) -> str:
    await _ensure_agent()
    # Gradio type="messages" passes history as [{"role": ..., "content": ...}, ...]
    messages = list(history)
    messages.append({"role": "user", "content": question})
    result = await launch_DB(_agent, topic="qa", message=messages)
    return result.final_output


demo = gr.ChatInterface(
    fn=ask,
    title="Stadtwerke Waiblingen FAQ Bot",
    description="Stellen Sie Ihre Fragen zu Strom, Gas und Wärme.",
    type="messages",
)

if __name__ == "__main__":
    demo.launch(share=True)
