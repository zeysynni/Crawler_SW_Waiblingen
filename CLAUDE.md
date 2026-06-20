# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

LLM-powered web crawler: an AI agent drives a Playwright browser (via MCP) to
navigate a website and extract structured content into `.json`, `.md`, and
`.pdf`. The current target is Stadtwerke Waiblingen, a German utility company.

> **Active work:** the crawler is being generalized from a single hard-coded
> site into a **config-driven** tool (supply a root URL + topics in YAML).
> See **`PLAN.md`** for the roadmap and phase status. When changing the config
> or crawl flow, read `PLAN.md` first — it defines the target design.

## Scope

- **In scope:** everything for the crawler (`main.py`, `config.py`,
  `crawl_agent.py`, `prompts.py`, `webpage_structure.py`, `utils.py`,
  `agent_utils.py`, `mcp_params.py`, `md2pdf.py`).
- **Out of scope for now:** the `faq/` folder (FAQ bot, DB ingestion, Gradio
  UI). Leave it untouched until the crawler is solid. Do not refactor it.

## Environment & tooling

- Package manager: **`uv`** (not pip). Dependencies live in `pyproject.toml`;
  the lockfile is `uv.lock` (committed). There is no `requirements.txt`.
- Requires `OPENAI_API_KEY` in a `.env` file at the repo root.
- PDF export requires a system **`pandoc`** install (used by `pypandoc`).
- This project must stay **outside** any other `uv` project's directory tree —
  otherwise `uv` absorbs it as a workspace member and shares that project's
  `.venv`. Keep it standalone.

**Setup:**
```bash
uv sync                      # create .venv and install from uv.lock
# create .env with: OPENAI_API_KEY=your_key
```

**Add a dependency:**
```bash
uv add <package>             # updates pyproject.toml + uv.lock
```

## Commands

Always run through `uv run` so the project's own `.venv` is used.

```bash
uv run python main.py        # run the crawler
uv run python md2pdf.py      # convert the active topic's Markdown to PDF
```

## Architecture

### Crawl pipeline
```
config.py (targets) → main.py → crawl_agent.py → LLM + Playwright MCP
    → Webpages (Pydantic) → outputs/{topic}.json
    → utils.json_to_markdown → outputs/{topic}.md
    → md2pdf.py → customer_files/{topic}.pdf
```

### Key files
- `config.py` — **(being refactored, see PLAN.md)** currently holds crawl
  targets as Python dicts plus an `active_topic` selector at the bottom and a
  derived `structure` dict. The target design moves the data to YAML under
  `sites/` and makes `config.py` a loader + Pydantic models.
- `webpage_structure.py` — Pydantic models for the crawl **output**
  (`Webpages → Page → Block → ContentSegment → FAQ → QA`); enforces structured
  output from the LLM.
- `crawl_agent.py` — builds the `Agent` with the Playwright MCP server and runs
  it; `launch_crawler` saves JSON.
- `agent_utils.py` — shared helpers: one reused `AsyncOpenAI` client,
  `create_mcp_servers`, and `run_agent` (wraps `Runner.run` with a timeout).
- `prompts.py` — system instruction + `get_user_prompt_structured_output`
  template for the crawl agent.
- `mcp_params.py` — Playwright MCP config (and DB params used only by `faq/`).
- `utils.py` — JSON→Markdown conversion and file I/O.
- `main.py` — entry point: loads `structure` from `config.py` and crawls each
  topic.

### MCP servers
- `@playwright/mcp@latest` — browser automation for crawling. (Pinning the
  version is a known TODO — `@latest` is a supply-chain risk.)

## Conventions

- Keep **data separate from code** — crawl targets are data (headed for YAML),
  not Python. This is the central refactor; don't add new targets as Python
  dicts.
- Validate external input at the boundary with Pydantic; fail loudly with a
  clear message rather than producing empty output.
- Small, single-purpose functions. Prefer pure functions (data in → data out)
  for anything that isn't the agent call itself.
- Outputs go to `outputs/` (gitignored); PDFs to `customer_files/`.
