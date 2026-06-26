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
* 🖥️ Simple CLI: pick a config and a subset of topics
* 📄 JSON → Markdown automatically; Markdown → PDF on demand (`--pdf`)
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
├── pipeline.py             # JSON → Markdown → (optional) PDF
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
    → pipeline.json_to_markdown → outputs/<topic>.md
    → pipeline.to_pdf (only with --pdf) → customer_files/<topic>.pdf
```

Each topic maps to one crawl; outputs use stable, un-timestamped paths and are
overwritten on each run (keep-newest), which suits a regular re-crawl.

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
```

Get a key at https://platform.openai.com/api-keys.

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
```

Options:

| Flag | Default | Meaning |
|------|---------|---------|
| `--config` | `sites/waiblingen.yaml` | Path to the site YAML config |
| `--topics` | all topics | Comma-separated topic names to crawl |
| `--pdf` | off | Also write `customer_files/<topic>.pdf` |

Outputs:

```
outputs/<topic>.json     # raw structured result
outputs/<topic>.md       # generated Markdown (the main deliverable)
customer_files/<topic>.pdf   # only with --pdf
```

### Adding / editing crawl targets

Edit `sites/waiblingen.yaml` (or add a new YAML file). Each topic needs a `name`
and either a `url` or a click-`path`, plus free-text `instructions`:

```yaml
site: stadtwerke-waiblingen
root_url: https://www.stadtwerke-waiblingen.de
topics:
  - name: kontakt
    url: https://www.stadtwerke-waiblingen.de/kontakt
    instructions: |
      - Kontakt
  - name: strom
    path: [Privatkunden, Strom]      # OR navigate by clicking labels from root
    instructions: |
      Crawl the Strom page top to bottom; expand every '+'.
```

Bad input fails fast with a clear message (e.g. a topic with neither `url` nor
`path`, or an unknown `--topics` name).

You may adjust the output schema in `webpage_structure.py` — but if you do, also
update `pipeline.json_to_markdown` to match.

---

## Tests

```bash
uv run pytest
```

Tests cover the pure functions (config loading/validation, the prompt builder,
topic selection, JSON→Markdown, and PDF path logic with pandoc mocked). The
agent/browser crawl itself is not unit-tested.

---

## Design Principles

* **Separate data from code** — targets are YAML, logic is Python
* **Validate at the boundary** — YAML is parsed into typed Pydantic models, bad input is rejected loudly
* **Small, single-purpose functions** — easy to test (data in → data out)
* **Make the easy thing the right thing** — switch targets via CLI args, not code edits

---

## Limitations

* Depends on LLM accuracy and careful prompt design
* Expandable ("+") content is not always reliably opened (known issue — see `PLAN.md`)
* Output length can affect quality — prefer crawling a focused subset of topics

---

## Extras

* In the **`faq/`** folder, an FAQ Bot (with two DB variants: knowledge-graph and
  SQL) is built on top of the crawl results, with a Gradio UI. It is separate
  from the crawler and documented within that folder.

---

## License

MIT License
