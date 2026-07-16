# Deterministic Web Crawler (crawl4ai)

## Overview

An **LLM-free web crawler** that turns a configured allowlist of web pages
into clean, knowledge-base-ready Markdown. [crawl4ai](https://docs.crawl4ai.com)
(Playwright underneath) fetches and converts each page to markdown; a
rule-based cleaning layer cuts CMS noise (menus, footer, cookie banner),
flattens links to plain text, and titles every file with its site hierarchy.
The result: **one page = one `.md` file = one knowledge-base chunk**.

The crawler is **config-driven**: *what* to crawl is data (`sites/*.yaml`),
*how* to crawl is code. The current target is Stadtwerke Waiblingen, a German
utility company. A full site crawl (~62 pages) takes ~2 minutes and is
reproducible — no API keys, no model costs, no stochastic output.

> This branch replaced the previous **LLM-driven** crawler (gpt-5-mini agent +
> Playwright MCP + deterministic enrichment). Why and how: `DEVLOG.md` §14 and
> `experiments/CRAWL4AI_SPIKE.md`.

---

## Key Features

* 🚫🧠 **No LLM anywhere** — deterministic fetch, convert, clean; byte-reproducible
* 🗂️ **Allowlist navigation**: YAML claims every page (base page + sub-pages by
  their visible link text); unlisted pages are never crawled, unresolved labels
  are reported loudly, never guessed
* 🧹 **Rule-based cleaning**: keep the heading-led content, cut
  Sprungmarken/menu/breadcrumb preamble and footer/cookie tail; h1 becomes the
  site hierarchy (`# Privatkunden - Strom - Ökostromtarif`); links flattened,
  images dropped
* 🪗 **Collapsed accordions captured for free** — the DOM is converted, not the
  visible viewport, so FAQ/accordion content needs no expand tricks
* 📄 Two outputs per page: `outputs/raw/` (full conversion) and
  `outputs/clean/` (KB form); hand-written pages in `static/` ride along
* ☁️ **Opt-in upload** (`--upload`): replace-by-file-id, **one chunk per file,
  no overlap** (pages above the API's 8192-char cap split with 1000-char
  overlap), sha+params skip for unchanged files, stale remote files pruned,
  resumable after failures (`upload_state.json`)
* 📟 **Detailed run report** (log + Pushover): per page ✓/✗/⚠ with failure
  reason, start time, duration, size; regression check vs the previous run;
  on `--upload` runs the files actually uploaded (`new:`) or pruned are
  named first
* ✅ Pydantic-validated config, unit-tested pure functions, stdlib logging

---

## Architecture

```
.
├── main.py                 # Entry point + CLI (argparse)
├── config.py               # Section/Site Pydantic models + load_site()
├── sites/                  # DATA: one YAML allowlist per website
│   └── waiblingen.yaml
├── crawl.py                # crawl4ai fetches, label→URL resolution, retries
├── clean.py                # pure markdown cleaning (noise cut, links, h1)
├── monitor.py              # run report + regression check + Pushover
├── uploader.py             # opt-in upload to the knowledge base
├── static/                 # hand-written KB pages (e.g. Kundenportal)
├── tests/                  # unit tests for the pure functions
├── docs/                   # code-review report (findings + fix status)
└── outputs/                # generated raw/ + clean/ markdown (gitignored)
```

### Pipeline

```
sites/*.yaml → config.load_site → crawl.crawl_site (crawl4ai, retry×1)
    → outputs/raw/<page>.md        full page as markdown
    → clean.clean_markdown         noise cut, links flattened, hierarchy h1
    → outputs/clean/<page>.md      (+ static/*.md copied in verbatim)
    → uploader.upload_pages        --upload only; one chunk per file, replace
    → monitor.run_report           per-page status/timing → log + Pushover
                                   (uploaded/pruned file names first)
```

---

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                              # create .venv from uv.lock
uv run playwright install chromium   # browser for crawl4ai (once)
cp .env.example .env                 # optional: Pushover + upload key
```

## Usage

```bash
uv run python main.py                                # crawl all sections
uv run python main.py --sections Privatkunden_Strom  # a subset
uv run python main.py --upload                       # + push to the knowledge base
uv run pytest                                        # unit tests
```

Outputs land in `outputs/raw/` and `outputs/clean/` (gitignored, overwritten
each run — stable filenames like `Privatkunden_Strom_Grundversorgung.md`).

## Adding / changing crawl targets

Edit `sites/waiblingen.yaml` — no code changes needed:

```yaml
sections:
  - path: Privatkunden/Strom      # base page (crawled itself) + output name
    subpages:                     # sub-pages by their visible link text
      - Ökostromtarif
      - Grundversorgung
  - path: Störung                 # display name ...
    url: notfallnummern           # ... fetched from a different URL
```

If a label doesn't match a link on the base page, the run report says so
(`⚠ no link with text '…'`) — fix the label, don't add code.
