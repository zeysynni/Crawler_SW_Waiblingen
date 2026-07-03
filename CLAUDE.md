# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

LLM-powered web crawler: an AI agent drives a Playwright browser (via MCP) to
navigate a website and extract structured content into `.json`, `.md`, and
`.pdf`. The current target is Stadtwerke Waiblingen, a German utility company.

> **Status:** the config-driven refactor is **done** (Phases 0–8 in `PLAN.md`),
> plus a quality pass: model is **`gpt-5-mini`**; structured content (FAQ, files,
> tables, phones, opening hours) is recovered **deterministically** (`enrich.py`)
> on top of the LLM crawl; multi-page topics use **`subtopics`** (labels resolved
> to URLs deterministically); failed topics **retry**; runs are watched via
> **Pushover** (`monitor.py`). The **whole Stadtwerke Waiblingen site is
> crawled** (Privatkunden, Geschäftskunden, Netze, standalone). Targets are YAML
> under `sites/`; `config.py` is a loader + Pydantic models; `main.py` is a CLI.
> See **`PLAN.md`** for phase history + the pending prompt-first cleanup, and
> **`DEVLOG.md`** (esp. §9–12) for the work log + decisions. Read those before
> changing the crawl flow.

## Scope

- **In scope:** everything for the crawler (`main.py`, `config.py`,
  `sites/*.yaml`, `crawl_agent.py`, `prompts.py`, `webpage_structure.py`,
  `pipeline.py`, `enrich.py`, `monitor.py`, `uploader.py`, `agent_utils.py`,
  `mcp_params.py`, `scripts/`, `tests/`).
- **Out of scope for now:** the `faq/` folder (FAQ bot, DB ingestion, Gradio
  UI). Leave it untouched until the crawler is solid. Do not refactor it.

## Environment & tooling

- Package manager: **`uv`** (not pip). Dependencies live in `pyproject.toml`;
  the lockfile is `uv.lock` (committed). There is no `requirements.txt`.
- Requires `OPENAI_API_KEY` in a `.env` file at the repo root. Optional
  `PUSHOVER_TOKEN` / `PUSHOVER_USER` enable phone alerts (no-op if absent).
  `--upload` needs `AIGATEWAY_KEY` (knowledge-base API); optional overrides
  `AIGATEWAY_KB_ID`, `AIGATEWAY_IMPORT_STRATEGY_ID`, `UPLOAD_STATE_FILE`.
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
(comma-separated; default all), `--pdf` (opt-in PDF; needs pandoc + xelatex),
`--delay` (seconds between topics), `--retries` (re-launch a failed topic, default
2 → up to 3 attempts, hard-capped at `MAX_RETRIES=3`), `--retry-backoff` (default 15s),
`--upload` (opt-in: push each crawled topic's `.md` to the knowledge base; needs
`AIGATEWAY_KEY`).

## Architecture

### Crawl pipeline
```
sites/*.yaml → config.load_site → main.py → crawl_agent.py → gpt-5-mini + Playwright MCP
    → Webpages (Pydantic) → outputs/{topic}.json
    → enrich.enrich_topic   (deterministic FAQ/file/table recovery from the HTML)
    → pipeline.json_to_markdown → outputs/{topic}.md
    → pipeline.to_pdf (only with --pdf) → customer_files/{topic}.pdf
    → uploader.upload_topics (only with --upload) → knowledge-base API (replace)
    → monitor: regression check + Pushover summary
```
One topic = one crawl. Outputs use stable, un-timestamped paths and are
overwritten each run (keep-newest).

### Division of labour (important)
- **LLM (gpt-5-mini)** captures prose and page structure, in document order,
  preserving the page's Markdown formatting (headings, **bold**, lists).
- **Deterministic enrichment (`enrich.py`)** recovers structured, server-rendered
  content the LLM captures inconsistently — FAQ/accordion Q&As, downloadable files
  (PDF **and** doc/xls/ppt/zip/csv…), tables, phone numbers (`tel:`), and weekly
  opening hours (`<dl>`). It attaches each in place, keyed on the section heading,
  and never reorders. **Union, don't duplicate:** it adds only what the LLM
  missed — dedup is by **content** (a distinctive chunk of the answer/section),
  never by label alone (a heading being present doesn't mean the body is). It
  parses via `_soup`, which drops `<script>/<style>`, and skips hidden content
  (`_is_hidden`: `hide`/`d-none`/`display:none`…) so the deterministic layer
  matches what's *visible*, like the agent's a11y snapshot.
- **Design tension (see `DEVLOG.md` §12):** page-*format* issues belong in
  per-topic `instructions` (prompt-first); the deterministic layer is for
  *reliability* (content the LLM drops), not formatting. `PLAN.md` has a
  post-completion task to trim single-page-driven code back into prompts.

### Key files
- `config.py` — `Topic`/`Site` Pydantic models + `load_site()` loader. Holds no
  targets itself; it loads + validates them from YAML. `Topic` supports
  `subtopics` (sub-page labels; requires a base `url`).
- `sites/*.yaml` — **the crawl targets (data).** One file per website:
  `site`, `root_url`, and a list of `topics` (each: `name`, `url` or `path`,
  optional `subtopics`, `instructions`). Add/edit targets here, not in Python.
  `subtopics` lists sub-page **labels**; the crawler resolves them to real URLs
  deterministically off the base page (`enrich.resolve_subtopics`) — no fragile
  LLM click-navigation. Labels match the visible link text (spacing/`&` matters).
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
  root_url, subtopic_urls=None)` (injects resolved sub-page URLs as "Sub-pages to
  crawl") and `build_navigation` (handles `url` and click-`path`).
- `enrich.py` — **deterministic post-crawl enrichment.** Re-fetches each page's
  HTML (`fetch_html` percent-encodes non-ASCII URLs, e.g. `ä`) via `_soup`
  (strips script/style) and recovers, keyed on section heading:
  FAQ/accordion Q&As (`extract_expandable_qa_groups`; Bootstrap + `<details>`,
  tables→Markdown), downloadable files by heading (`extract_file_groups`; broad
  extensions, skips ghost links), `tel:` phones (`extract_phone_contacts`,
  49/leading-0 canonicalized), opening hours (`extract_opening_hours`), and
  dropped `<h2>` prose (`extract_prose_sections`, skips hidden + accordion +
  download sections). Attachment (`_locate_heading` by specificity;
  `_qa_already_present`/`_content_norm` content-dedup) adds only missing content
  and never reorders. Also `resolve_subtopics(base_url, labels)→[{label,url}]`.
  Pure parsers are unit-tested.
- `monitor.py` — `send_pushover`, coverage metrics, `regressions` (drops vs the
  previous crawl), and `run_summary` (the detailed end-of-run message).
- `uploader.py` — **opt-in upload to the knowledge-base API** (`--upload`).
  `chunk_params_for` picks per-file chunking from content structure (p95 unit
  size clamped [800, 2000] + ~10% overlap). `upload_topics` does **replace**:
  delete the previously stored `file_id`, upload the new `.md`, persist the new
  id in a keyed state map (`upload_state.json`, gitignored — **must survive
  between weekly runs**, e.g. a GitLab cache/artifact). Skips files unchanged
  since last upload (sha256). Retries a failed delete/upload once, then raises
  `UploadHold` (state saved) so a scheduler resumes the pending topics ~24h
  later. NB: upload uses the API's **v2** endpoint, delete **v1** (intentional).
- `pipeline.py` — `save_json`, `json_to_markdown` (pure), `write_markdown`,
  `to_pdf`. `json_to_markdown` renders blocks **in JSON order** and adds NO
  injected labels (no `**Dateien:**`/`**Kontakt:**`/faqs title); it shows a
  page title (`# <title>`) and renders a `table` field as-is. If you change
  `webpage_structure.py`, update `json_to_markdown`.
- `mcp_params.py` — Playwright MCP config. Loads `scripts/expand_accordions.js`
  via `--init-script` to force-open accordions (so collapsed content is visible).
- `scripts/expand_accordions.js` — CSS-override that keeps accordion panels open
  (beats Bootstrap's single-open `data-bs-parent` re-collapse).
- `main.py` — entry point: argparse CLI
  (`--config/--topics/--pdf/--delay/--retries/--retry-backoff`), `load_site`,
  per-topic loop with error isolation. `process_topic` resolves any `subtopics`
  to URLs and passes them to the prompt; `crawl_topic` wraps it with bounded
  retry (re-launch on transient failure). Then enrichment, regression alert, and
  an end-of-run summary.
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
- **Deterministic over stochastic for structured content:** FAQ/files/tables/
  phones/hours come from `enrich.py` (HTML parsing), not the LLM. Don't rely on
  the LLM to reliably capture those.
- **Prompt-first for format; code for reliability.** Fix a page's *formatting*
  (or "expand this accordion", "don't go deeper") via per-topic `instructions`
  in the YAML — not new code. Add/keep deterministic code only for content the
  LLM *drops between runs*. See `DEVLOG.md` §12 and the `PLAN.md` cleanup task.
- **Dedup by content, never by label.** When deciding whether the LLM already
  captured something, match a distinctive chunk of the **content** — a heading/
  label being present does NOT mean the body is (that bug silently dropped
  accordion answers and prose). See `_qa_already_present`, the prose backstop.
- **Match visible content.** Extractors skip `<script>/<style>` and hidden
  elements (`_is_hidden`) so the deterministic layer mirrors the a11y snapshot.
- **Don't reorder; trust the JSON order** (the agent crawls top-to-bottom).
  Don't inject generic titles ("Downloads"/"FAQ") — attach content to the page's
  own sections (keep a *real* page subtitle like "Downloads zur Grundversorgung").
  Preserve the page's Markdown formatting.
- Outputs go to `outputs/` (gitignored); PDFs to `customer_files/`.
