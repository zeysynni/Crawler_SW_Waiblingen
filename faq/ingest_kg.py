import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import os
from contextlib import AsyncExitStack
from dotenv import load_dotenv
from db_agent import create_db_agent, launch_DB
from tqdm import tqdm

from mcp_params import kb_db_params
from prompts import ingest_instruction_kg

load_dotenv(override=True)

_DEFAULT_OUTPUTS_DIR = str(Path(__file__).parent.parent / "outputs")


def load_md_files(folder: str = _DEFAULT_OUTPUTS_DIR) -> list[dict]:
    files = []
    for fname in os.listdir(folder):
        if fname.endswith(".md"):
            path = os.path.join(folder, fname)
            with open(path, "r", encoding="utf-8") as f:
                files.append({"filename": fname, "content": f.read()})
    return files


async def main():
    async with AsyncExitStack() as stack:
        agent = await create_db_agent(stack, ingest_instruction_kg, kb_db_params)
        md_files = load_md_files()
        for file in tqdm(md_files, desc="Ingesting", unit="file"):
            message = f"Filename: {file['filename']}\n\n{file['content']}"
            await launch_DB(agent, topic=file['filename'], message=message)
        print("Ingest complete.")


if __name__ == "__main__":
    asyncio.run(main())
