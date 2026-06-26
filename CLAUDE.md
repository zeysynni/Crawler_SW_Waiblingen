# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

LLM-powered web crawler: an AI agent drives a Playwright browser (via MCP) to
navigate a website and extract structured content into `.json`, `.md`, and
`.pdf`. The current target is Stadtwerke Waiblingen, a German utility company.

> **Status:** the config-driven refactor is **done** (Phases 0–8 in `PLAN.md`).
> Targets are YAML under `sites/`; `config.py` is a loader + Pydantic models;
> `main.py` is a CLI. See **`PLAN.md`** for phase history, design principles,
> and the deferred **known issue** (expandable "+" content not always opened).
> Read `PLAN.md` before changing the config or crawl flow.

## Scope

- **In scope:** everything for the crawler (`main.py`, `config.py`,
  `sites/*.yaml`, `crawl_agent.py`, `prompts.py`, `webpage_structure.py`,
  `pipeline.py`, `agent_utils.py`, `mcp_params.py`, `tests/`).
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
uv run python main.py                              # crawl all topics in the default config
uv run python main.py --topics kontakt,strom       # crawl a subset
uv run python main.py --config sites/x.yaml --pdf  # different config + PDF export
uv run pytest                                      # run the unit tests
```

CLI flags: `--config` (default `sites/waiblingen.yaml`), `--topics`
(comma-separated; default all), `--pdf` (opt-in PDF; needs pandoc + xelatex).

## Architecture

### Crawl pipeline
```
sites/*.yaml (targets) → config.load_site → main.py → crawl_agent.py → LLM + Playwright MCP
    → Webpages (Pydantic) → outputs/{topic}.json
    → pipeline.json_to_markdown → outputs/{topic}.md
    → pipeline.to_pdf (only with --pdf) → customer_files/{topic}.pdf
```
One topic = one crawl. Outputs use stable, un-timestamped paths and are
overwritten each run (keep-newest).

### Key files
- `config.py` — `Topic`/`Site` Pydantic models + `load_site()` loader. Holds no
  targets itself; it loads + validates them from YAML.
- `sites/*.yaml` — **the crawl targets (data).** One file per website:
  `site`, `root_url`, and a list of `topics` (each: `name`, `url` or `path`,
  `instructions`). Add/edit targets here, not in Python.
- `webpage_structure.py` — Pydantic models for the crawl **output**
  (`Webpages → Page → Block → ContentSegment → FAQ → QA`); enforces structured
  output from the LLM.
- `crawl_agent.py` — builds the `Agent` with the Playwright MCP server and runs
  it; `launch_crawler` saves JSON.
- `agent_utils.py` — shared helpers: one reused `AsyncOpenAI` client,
  `create_mcp_servers`, and `run_agent` (wraps `Runner.run` with a timeout).
- `prompts.py` — system instruction + `get_user_prompt_structured_output(topic,
  root_url)` and the `build_navigation` helper (handles `url` and click-`path`).
- `pipeline.py` — `save_json`, `json_to_markdown` (pure), `write_markdown`,
  `to_pdf`. If you change `webpage_structure.py`, update `json_to_markdown`.
- `mcp_params.py` — Playwright MCP config (and DB params used only by `faq/`).
- `main.py` — entry point: argparse CLI, `load_site`, per-topic crawl loop with
  error isolation + an end-of-run summary.
- `tests/` — unit tests for the pure functions (`uv run pytest`).

### MCP servers
- `@playwright/mcp@0.0.76` — browser automation for crawling. Pinned (not
  `@latest`) for reproducible runs; bump deliberately after testing.

### Logging & errors
- Use the shared `logging.getLogger("crawler")`; `basicConfig` is set once in
  `main()`. Do not add `print()`. The per-topic loop catches exceptions, logs
  the traceback via `log.exception`, and continues so one bad topic can't abort
  the batch.

## Conventions

- Keep **data separate from code** — crawl targets are YAML under `sites/`,
  not Python. Don't add new targets as Python dicts.
- Validate external input at the boundary with Pydantic; fail loudly with a
  clear message rather than producing empty output.
- Small, single-purpose functions. Prefer pure functions (data in → data out)
  for anything that isn't the agent call itself, and unit-test them.
- Outputs go to `outputs/` (gitignored); PDFs to `customer_files/`.
