# LLM-Powered Web Crawler

## Overview

This project implements an **LLM-powered web crawler** that extracts structured
content from websites and converts it into Markdown (and optionally PDF).

Instead of traditional scraping, an LLM agent drives a real browser (Playwright,
via MCP) to navigate pages and extract structured output validated by Pydantic.

The crawler is **config-driven**: you describe *what* to crawl as data (a YAML
file of topics), and run one command. The current target is Stadtwerke
Waiblingen, a German utility company.

---

## Key Features

* 🔍 Agent-based web crawling using LLMs + Playwright (MCP)
* 🧠 Structured extraction with Pydantic schemas
* 🗂️ Config-driven targets in YAML (data, not code)
* 🧩 **Deterministic enrichment** (`enrich.py`): recovers FAQ/accordion Q&As,
  downloadable files, tables, phone numbers, and opening hours from the HTML —
  the structured content the LLM captures inconsistently — and attaches them in
  place (union, never duplicated; dedup by content, matched to the page's own headings)
* 🔗 **Subtopics**: list sub-page *labels* in YAML; they're resolved to real URLs
  deterministically (no fragile LLM click-navigation)
* 🖥️ CLI: pick a config and a subset of topics; opt-in PDF and upload
* 📄 JSON → Markdown automatically; Markdown → PDF on demand (`--pdf`)
* ☁️ **Opt-in upload** (`--upload`) of the Markdown to a knowledge-base API
  (per-file chunking, replace-by-file-id, resumable)
* 🔁 **Retry-on-failure** for transient crawl errors; 📟 **Pushover** alerts +
  end-of-run summary (`monitor.py`)
* 🧾 stdlib `logging` + per-topic error isolation (one bad topic won't abort the batch)

---

## Architecture

```
.
├── main.py                 # Entry point + CLI (argparse)
├── config.py               # Topic/Site Pydantic models + load_site() loader
├── sites/                  # DATA: one YAML file per website
│   └── waiblingen.yaml
├── crawl_agent.py          # Build + run the crawl agent
├── agent_utils.py          # Shared agent helpers (OpenAI client, MCP, runner)
├── prompts.py              # System instruction + prompt builder
├── webpage_structure.py    # Pydantic schema for the crawl OUTPUT
├── enrich.py               # Deterministic post-crawl enrichment (FAQ/files/…)
├── pipeline.py             # JSON → Markdown → (optional) PDF
├── monitor.py              # Pushover alerts + regression check + run summary
├── uploader.py             # Opt-in upload of Markdown to the knowledge base
├── mcp_params.py           # Playwright MCP server configuration
├── tests/                  # Unit tests for the pure functions
├── outputs/                # Generated .json / .md (gitignored)
├── customer_files/         # Generated .pdf
└── faq/                    # FAQ Bot (separate; see "Extras")
```

### Pipeline

```
sites/*.yaml (targets) → main.py → crawl_agent.py → LLM + Playwright MCP
    → Webpages (Pydantic) → outputs/<topic>.json
    → enrich.enrich_topic → deterministic FAQ/file/table/phone/hours recovery
    → pipeline.json_to_markdown → outputs/<topic>.md
    → pipeline.to_pdf (only with --pdf) → customer_files/<topic>.pdf
    → uploader.upload_topics (only with --upload) → knowledge-base API (replace)
    → monitor: regression check + Pushover summary
```

Each topic maps to one crawl; outputs use stable, un-timestamped paths and are
overwritten on each run (keep-newest), which suits a regular (e.g. weekly) re-crawl.

**Division of labour:** the LLM captures prose and page structure in document
order; deterministic enrichment owns the structured content (FAQ/files/tables/
phones/hours) it would otherwise drop between runs. Page-*format* issues are
fixed via per-topic `instructions` in the YAML, not code (see `DEVLOG.md` §12).

---

## Data Model

The crawler outputs structured data using Pydantic models (see
`webpage_structure.py`):

* `Webpages` → `Page` → `Block` → `ContentSegment`
* Case-dependent structures like `FAQ` / `QA` / files / contacts

This ensures a consistent structure, easy post-processing, and compatibility
with downstream pipelines.

---

## Installation

This project uses **[uv](https://docs.astral.sh/uv/)** (not pip). Dependencies
live in `pyproject.toml`; the lockfile `uv.lock` is committed for reproducible
installs.

```bash
git clone <repo-url>
cd crawler
uv sync          # creates .venv and installs from uv.lock
```

PDF export (optional) additionally requires a system **pandoc** install and a
LaTeX engine (**xelatex**).

---

## Environment Setup

Create a `.env` file in the repo root (see `.env.example`):

```env
OPENAI_API_KEY=your_api_key_here
# optional — phone alerts + end-of-run summary (no-op if absent):
PUSHOVER_TOKEN=...
PUSHOVER_USER=...
# optional — only needed for --upload (knowledge-base API):
AIGATEWAY_KEY=...
```

Get an OpenAI key at https://platform.openai.com/api-keys.

---

## Usage

Run through `uv run` so the project's own `.venv` is used.

```bash
# Crawl every topic in the default config
uv run python main.py

# Crawl a subset (recommended while testing)
uv run python main.py --topics kontakt,Privatkunden_Wasser

# Use a different site config
uv run python main.py --config sites/waiblingen.yaml --topics kontakt

# Also export each topic to PDF (needs pandoc + xelatex)
uv run python main.py --topics kontakt --pdf

# Crawl and upload the Markdown to the knowledge base (needs AIGATEWAY_KEY)
uv run python main.py --upload
```

Options:

| Flag | Default | Meaning |
|------|---------|---------|
| `--config` | `sites/waiblingen.yaml` | Path to the site YAML config |
| `--topics` | all topics | Comma-separated topic names to crawl |
| `--pdf` | off | Also write `customer_files/<topic>.pdf` |
| `--upload` | off | Upload each crawled topic's `.md` to the knowledge base (replace; needs `AIGATEWAY_KEY`) |
| `--delay` | `0` | Seconds to wait between topics (rate-limit pacing) |
| `--retries` | `2` | Re-launch a topic if its crawl fails (up to 3 attempts; capped at 3) |
| `--retry-backoff` | `15` | Seconds to wait before re-launching a failed topic |

Outputs:

```
outputs/<topic>.json     # raw structured result
outputs/<topic>.md       # generated Markdown (the main deliverable)
customer_files/<topic>.pdf   # only with --pdf
```

### Adding / editing crawl targets

Edit `sites/waiblingen.yaml` (or add a new YAML file). Each topic needs a `name`
and either a `url` or a click-`path`, plus optional `subtopics` and free-text
`instructions`:

```yaml
site: stadtwerke-waiblingen
root_url: https://www.stadtwerke-waiblingen.de
topics:
  - name: kontakt
    url: https://www.stadtwerke-waiblingen.de/kontakt
    instructions: |
      - Kontakt
  - name: PrivateKunden_Strom
    url: https://www.stadtwerke-waiblingen.de/Privatkunden/Strom
    subtopics:                       # sub-page LABELS, resolved to URLs off the base page
      - Ökostromtarif
      - Grundversorgung
    instructions: |
      Crawl this page completely, then each sub-page listed below.
  - name: strom
    path: [Privatkunden, Strom]      # OR navigate by clicking labels from root
    instructions: |
      Crawl the Strom page top to bottom.
```

`subtopics` labels must match the visible link text on the base page (spacing and
`&` matter); they're resolved to real URLs deterministically, so no LLM click-guessing.

Bad input fails fast with a clear message (e.g. a topic with neither `url` nor
`path`, or an unknown `--topics` name).

You may adjust the output schema in `webpage_structure.py` — but if you do, also
update `pipeline.json_to_markdown` to match.

---

## Tests

```bash
uv run pytest
```

Tests cover the pure functions: config loading/validation, the prompt builder,
topic selection, JSON→Markdown, PDF path logic (pandoc mocked), the deterministic
enrichment parsers (`enrich.py`), retry logic, and the uploader (HTTP mocked).
The agent/browser crawl itself is not unit-tested.

---

## Design Principles

* **Separate data from code** — targets are YAML, logic is Python
* **Validate at the boundary** — YAML is parsed into typed Pydantic models, bad input is rejected loudly
* **Small, single-purpose functions** — easy to test (data in → data out)
* **Make the easy thing the right thing** — switch targets via CLI args, not code edits

---

## Limitations

* Depends on LLM accuracy and careful prompt design
* Expandable ("+") / accordion content: made reliable via a force-open init
  script **and** deterministic HTML extraction (`enrich.py`), rather than relying
  on the LLM to click every one
* Large per-page transcription tasks (e.g. a directory of ~90 entries) are
  captured deterministically, not by the LLM (see `DEVLOG.md` §12)
* `upload_state.json` (remote file ids) must persist between runs when deployed
  (e.g. GitLab CI cache/artifact), or `--upload` can't replace cleanly

---

## Extras

* In the **`faq/`** folder, an FAQ Bot (with two DB variants: knowledge-graph and
  SQL) is built on top of the crawl results, with a Gradio UI. It is separate
  from the crawler and documented within that folder.

---

## License

MIT License
