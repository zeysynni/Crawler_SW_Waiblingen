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
  `outputs/clean/` (KB form); pages in `static/` ride along
* 📎 **Non-crawlable sources converted in the pipeline**: PDFs (`pdfplumber`,
  ruled tables kept intact) and the colleagues' Excel become `static/*.md`
  before every crawl — so replacing a source document in the repo is the whole
  update procedure, doable from a browser with no git (`HANDOVER.md`). A tariff
  PDF that loses its table structure **fails the run** rather than shipping
  prices as prose
* ☁️ **Opt-in upload** (`--upload`): **stateless reconcile** against the live
  knowledge base — list it, replace each page by filename (delete every remote
  copy of that name, then upload), prune filenames no longer produced locally.
  **One chunk per file, no overlap** (pages above the API's 8192-char cap split
  with 1000-char overlap). No local state to keep in sync, so a lost CI cache
  or interrupted run self-heals on the next run
* 📟 **Detailed run report** (log + Pushover): per page ✓/✗/⚠ with failure
  reason, start time, duration, size; regression check vs the previous run;
  on `--upload` an `uploaded N, pruned M` count and the pruned names come first
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
├── static/                 # KB pages that aren't crawled:
│                           #   Kundenportal.md hand-written, the rest generated
├── PDFs/                   # source PDFs + pdf2md.py    → static/*.md
├── Excels/                 # source .xlsx + xlsx2md.py  → static/*.md
├── tests/                  # unit tests for the pure functions
├── docs/                   # code-review report (findings + fix status)
├── HANDOVER.md             # browser-only procedures (no git needed)
└── outputs/                # generated raw/ + clean/ markdown (gitignored)
```

### Pipeline

```
PDFs/*.pdf     → PDFs/pdf2md.py     ⎫ CI runs both before the crawl;
Excels/*.xlsx  → Excels/xlsx2md.py  ⎭ each writes static/*.md

sites/*.yaml → config.load_site → crawl.crawl_site (crawl4ai, retry×1)
    → outputs/raw/<page>.md        full page as markdown
    → clean.clean_markdown         noise cut, links flattened, hierarchy h1
    → outputs/clean/<page>.md      (+ static/*.md copied in verbatim)
    → uploader.upload_pages        --upload only; reconcile against live KB
                                   (list, replace by filename, prune)
    → monitor.run_report           per-page status/timing → log + Pushover
                                   (upload count + pruned names first)
```

---

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group convert              # create .venv from uv.lock
uv run playwright install chromium   # browser for crawl4ai (once)
cp .env.example .env                 # optional: Pushover + upload key
```

`--group convert` adds `pdfplumber`/`openpyxl` for the two source converters.
Plain `uv sync` gives you a working crawler but not those (and
`tests/test_convert.py` will fail to import).

## Usage

```bash
uv run python main.py                                # crawl all sections
uv run python main.py --sections Privatkunden_Strom  # a subset
uv run python main.py --upload                       # + push to the knowledge base
uv run pytest                                        # unit tests

uv run python PDFs/pdf2md.py                         # PDFs  → static/*.md
uv run python Excels/xlsx2md.py                      # Excel → static/*.md
```

Outputs land in `outputs/raw/` and `outputs/clean/` (gitignored, overwritten
each run — stable filenames like `Privatkunden_Strom_Grundversorgung.md`).

---

## Running it manually

The pipeline runs **weekly on a GitLab schedule** and needs no attention. To run
it yourself, either route works and they are interchangeable — the uploader
reconciles against the live knowledge base every time, so there is no local
state to get out of sync and no "wrong order" to worry about.

### Locally

```bash
uv run python main.py            # crawl only — nothing touches the knowledge base
uv run python main.py --upload   # crawl + upload: the real weekly run
```

`--upload` needs `AIGATEWAY_KEY` in `.env`. A full run is ~62 pages / a few
minutes, and sends the same Pushover messages as CI. **Exit code is non-zero** if
any page failed or the upload was put on hold.

To see what a run *would* change without touching the knowledge base, run
without `--upload` and read `outputs/clean/`.

**A subset:**
```bash
uv run python main.py --sections Privatkunden_Strom,kontakt --upload
```
Remote pruning switches itself off for a subset — and for any run with a failed
page — so a partial run can never delete the pages it didn't crawl.

**After changing a PDF or the Excel locally**, regenerate `static/` first. CI does
this automatically; a local `main.py` does not:

```bash
uv run python PDFs/pdf2md.py
uv run python Excels/xlsx2md.py
uv run python main.py --upload
```

Not needed otherwise — the generated `static/*.md` are committed and current.

### From GitLab, without a terminal

**Build → Pipelines → New pipeline → Run pipeline.**

Runs exactly the scheduled job, converters included. Optionally set the variable
`SECTIONS` to `kontakt,Privatkunden_Strom` for a subset. This is the route for
whoever maintains the knowledge base without using git — see `HANDOVER.md`.

Note that **pushing a commit does not start a pipeline**: the job is gated to
`schedule` and `web` triggers, so uploading a new PDF takes effect at the next
weekly run, or immediately if you click *Run pipeline*.

### Updating the non-crawled pages

| To change | Edit | Takes effect |
|---|---|---|
| A Bäder PDF (tariffs, terms) | replace the file in `PDFs/` | next run, converted automatically |
| The colleagues' knowledge base | replace `Excels/Knowledge Base.xlsx` | next run, converted automatically |
| The Kundenportal page | edit `static/Kundenportal.md` | next run |
| Which pages get crawled | edit `sites/waiblingen.yaml` | next run |

Don't hand-edit `static/Privatkunden_Baeder_*.md` or
`static/Wissensdatenbank_*.md` — they are generated from `PDFs/` and `Excels/`
and are overwritten on every run. Fix the source document instead.

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
