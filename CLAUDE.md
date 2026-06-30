# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

LLM-powered web crawler: an AI agent drives a Playwright browser (via MCP) to
navigate a website and extract structured content into `.json`, `.md`, and
`.pdf`. The current target is Stadtwerke Waiblingen, a German utility company.

> **Status:** the config-driven refactor is **done** (Phases 0–8 in `PLAN.md`),
> and a quality pass on top of it: model is **`gpt-5-mini`**, FAQ/file/table
> content is recovered **deterministically** (`enrich.py`) on top of the LLM
> crawl, and runs are watched via **Pushover** (`monitor.py`). Targets are YAML
> under `sites/`; `config.py` is a loader + Pydantic models; `main.py` is a CLI.
> See **`PLAN.md`** for phase history and design principles, and **`DEVLOG.md`**
> for the detailed work log + decisions. Read those before changing the crawl
> flow.

## Scope

- **In scope:** everything for the crawler (`main.py`, `config.py`,
  `sites/*.yaml`, `crawl_agent.py`, `prompts.py`, `webpage_structure.py`,
  `pipeline.py`, `enrich.py`, `monitor.py`, `agent_utils.py`, `mcp_params.py`,
  `scripts/`, `tests/`).
- **Out of scope for now:** the `faq/` folder (FAQ bot, DB ingestion, Gradio
  UI). Leave it untouched until the crawler is solid. Do not refactor it.

## Environment & tooling

- Package manager: **`uv`** (not pip). Dependencies live in `pyproject.toml`;
  the lockfile is `uv.lock` (committed). There is no `requirements.txt`.
- Requires `OPENAI_API_KEY` in a `.env` file at the repo root. Optional
  `PUSHOVER_TOKEN` / `PUSHOVER_USER` enable phone alerts (no-op if absent).
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
sites/*.yaml → config.load_site → main.py → crawl_agent.py → gpt-5-mini + Playwright MCP
    → Webpages (Pydantic) → outputs/{topic}.json
    → enrich.enrich_topic   (deterministic FAQ/file/table recovery from the HTML)
    → pipeline.json_to_markdown → outputs/{topic}.md
    → pipeline.to_pdf (only with --pdf) → customer_files/{topic}.pdf
    → monitor: regression check + Pushover summary
```
One topic = one crawl. Outputs use stable, un-timestamped paths and are
overwritten each run (keep-newest).

### Division of labour (important)
- **LLM (gpt-5-mini)** captures prose and page structure, in document order,
  preserving the page's Markdown formatting (headings, **bold**, lists).
- **Deterministic enrichment (`enrich.py`)** owns FAQ Q&As, downloadable files,
  and tables — these are structured + server-rendered, so they're extracted
  from the HTML with BeautifulSoup (reliable, not subject to LLM variance).
  Enrichment is authoritative for FAQ/files: it strips the LLM's versions and
  attaches the deterministic ones in place (FAQ → its real section; files → the
  page's own "Downloads…" section). It never reorders blocks.

### Key files
- `config.py` — `Topic`/`Site` Pydantic models + `load_site()` loader. Holds no
  targets itself; it loads + validates them from YAML.
- `sites/*.yaml` — **the crawl targets (data).** One file per website:
  `site`, `root_url`, and a list of `topics` (each: `name`, `url` or `path`,
  `instructions`). Add/edit targets here, not in Python.
- `webpage_structure.py` — Pydantic models for the crawl **output**
  (`Webpages → Page → Block → ContentSegment → FAQ → QA`); enforces structured
  output from the LLM. Field descriptions stress copying exactly and preserving
  the page's Markdown formatting (bold, bullet/numbered lists).
- `crawl_agent.py` — builds the `Agent` (model **`gpt-5-mini`**) with the
  Playwright MCP server and runs it; `launch_crawler` saves JSON.
- `agent_utils.py` — shared helpers: one reused `AsyncOpenAI` client
  (`max_retries=8`), `create_mcp_servers`, and `run_agent` (wraps `Runner.run`,
  `max_turns=120`, `timeout=480s`). For `gpt-5*` models it sets
  `tool_choice="required"` (forces the reasoning model to actually browse) and
  omits temperature; other models get `temperature=0`.
- `prompts.py` — system instruction + `get_user_prompt_structured_output(topic,
  root_url)` and `build_navigation` (handles `url` and click-`path`).
- `enrich.py` — **deterministic post-crawl enrichment.** Re-fetches each page's
  HTML and extracts FAQ Q&As (Bootstrap accordions + `<details>`, tables→Markdown),
  PDF files (skips empty-text "ghost" links), and whole `<h2>` prose sections the
  LLM dropped; injects them in place. Pure parsers are unit-tested.
- `monitor.py` — `send_pushover`, coverage metrics, `regressions` (drops vs the
  previous crawl), and `run_summary` (the detailed end-of-run message).
- `pipeline.py` — `save_json`, `json_to_markdown` (pure), `write_markdown`,
  `to_pdf`. `json_to_markdown` renders blocks **in JSON order** and adds NO
  injected labels (no `**Dateien:**`/`**Kontakt:**`/faqs title); it shows a
  page title (`# <title>`) and renders a `table` field as-is. If you change
  `webpage_structure.py`, update `json_to_markdown`.
- `mcp_params.py` — Playwright MCP config. Loads `scripts/expand_accordions.js`
  via `--init-script` to force-open accordions (so collapsed content is visible).
- `scripts/expand_accordions.js` — CSS-override that keeps accordion panels open
  (beats Bootstrap's single-open `data-bs-parent` re-collapse).
- `main.py` — entry point: argparse CLI (`--config/--topics/--pdf/--delay`),
  `load_site`, per-topic loop with error isolation, enrichment, regression
  alert, and an end-of-run summary.
- `tests/` — unit tests for the pure functions (`uv run pytest`).

### MCP servers
- `@playwright/mcp@0.0.76` — browser automation. Pinned (not `@latest`).
  Loaded with `--init-script scripts/expand_accordions.js`. Runs **headed** for
  now (switch to `--headless` once quality is confirmed). NB: it depends on
  Playwright `1.61.0-alpha`, which matters for the CI browser install.

### Logging & errors
- Use the shared `logging.getLogger("crawler")`; `basicConfig` is set once in
  `main()`. Do not add `print()`. The per-topic loop catches exceptions, logs
  the traceback via `log.exception`, continues, and Pushover-alerts the failure.

## Conventions

- Keep **data separate from code** — crawl targets are YAML under `sites/`,
  not Python. Don't add new targets as Python dicts.
- Validate external input at the boundary with Pydantic; fail loudly with a
  clear message rather than producing empty output.
- Small, single-purpose functions. Prefer pure functions (data in → data out)
  for anything that isn't the agent call itself, and unit-test them.
- **Deterministic over stochastic for structured content:** FAQ/files/tables
  come from `enrich.py` (HTML parsing), not the LLM. Don't rely on the LLM to
  reliably capture those.
- **Don't reorder; trust the JSON order** (the agent crawls top-to-bottom).
  **Don't inject section titles** ("Downloads"/"FAQ") — attach content to the
  page's own sections. Preserve the page's Markdown formatting.
- Outputs go to `outputs/` (gitignored); PDFs to `customer_files/`.
