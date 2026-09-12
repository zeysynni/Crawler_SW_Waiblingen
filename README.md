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
uv run python main.py                                # standard flow: crawl only
uv run python main.py --sections Privatkunden_Strom  # a subset
uv run python main.py --upload                       # ⚠️ also rewrites the live KB
uv run pytest                                        # unit tests

uv run python PDFs/pdf2md.py                         # PDFs  → static/*.md
uv run python Excels/xlsx2md.py                      # Excel → static/*.md
```

The two converters are **not** run by `main.py` — they are separate commands
(CI runs them before each crawl). `--upload` is opt-in and is the only command
that writes outside this repository.

Outputs land in `outputs/raw/` and `outputs/clean/` (gitignored, overwritten
each run — stable filenames like `Privatkunden_Strom_Grundversorgung.md`).

**The next section documents every command, what it costs and what it writes.**

---

## The whole pipeline, end to end

Everything in this repository, in the order you would run it. Three parts, and
each command is independent — you can run any one of them on its own.

```
PART 1  ACQUISITION  →  markdown corpus        PART 2  FAQ BOT      PART 3  EVALUATION
──────────────────────────────────────────     ─────────────────    ──────────────────
PDFs/*.pdf     ─┐                              outputs/clean/*.md   tests.jsonl
Excels/*.xlsx  ─┼→ static/*.md ─┐                      ↓                   ↓
the website    ─┴→ crawl ───────┴→ outputs/clean/ →  vectors  →  answers  →  scores
```

**The contract between the parts is `outputs/clean/*.md`** — one markdown file
per page. Part 1 writes it, parts 2 and 3 only read it.

### 0. Setup (once)

```bash
uv sync --group convert --group bot     # crawler + converters + RAG layer
uv run playwright install chromium      # browser for crawl4ai
cp .env.example .env                    # then add OPENAI_API_KEY for the bot
```

### 1. Produce the markdown corpus

**The standard flow is one command:**

```bash
uv run python main.py          # crawl → outputs/raw/ + outputs/clean/
                               #       + copies static/*.md into outputs/clean/
```

It crawls every page in `sites/waiblingen.yaml`, cleans each one, copies the
`static/` pages in, and writes a run report. **It never uploads anything and
never touches the knowledge base.** Network access to the website is the only
outside contact.

**Converting PDFs and Excel files is NOT part of that flow.** The two converters
are separate commands, deliberately — on the server they are run by CI before
the crawl, and locally you have to run them yourself:

```bash
uv run python Excels/xlsx2md.py    # Excels/*.xlsx → static/Wissensdatenbank_*.md
uv run python PDFs/pdf2md.py       # PDFs/*.pdf    → static/Privatkunden_Baeder_*.md
```

**Order matters.** `main.py` is what copies `static/` into `outputs/clean/`, so
a converter run *after* the crawl does not reach the corpus until the next
crawl. Replacing a source PDF therefore means:

```bash
uv run python PDFs/pdf2md.py       # 1. regenerate static/
uv run python main.py              # 2. crawl, which collects static/ as well
```

Both converters work offline in seconds, need no browser, and each rewrites only
its own prefixed files. They exit non-zero if a conversion looks degraded (a
tariff PDF that produced no table), so a bad conversion fails loudly rather than
reaching the corpus.

#### Every command in part 1

| Command | Network | Writes | Safe to repeat |
|---|---|---|---|
| `uv run python Excels/xlsx2md.py` | no | `static/Wissensdatenbank_*.md` | yes |
| `uv run python PDFs/pdf2md.py` | no | `static/Privatkunden_Baeder_*.md` | yes |
| `uv run python main.py` | the website | `outputs/raw/`, `outputs/clean/` | yes |
| `uv run python main.py --sections A,B` | the website | the same, for those sections only | yes |
| `uv run python main.py --config sites/x.yaml` | the website | the same, for another site | yes |
| `uv run python main.py --upload` | the website **+ aigateway.eu** | the above **+ deletes and rewrites live knowledge-base files** | ⚠️ see below |

Everything above except `--upload` only writes local files and can be re-run as
often as you like: outputs have stable names and are overwritten in place.

#### ⚠️ What `--upload` actually does

`--upload` is the **only** flag that reaches outside this repository. It is
opt-in, it is not part of the standard flow, and it is normally run by CI on a
weekly schedule — not by hand. With it, `main.py` additionally:

1. lists the live knowledge base at `aigateway.eu` (needs `AIGATEWAY_KEY`);
2. **prunes** — `DELETE`s every remote file whose name this run did not produce;
3. **replaces** every page — `DELETE`s all remote copies of that filename, then
   uploads the fresh `.md`.

There is no dry-run and no undo. Two things make it survivable: the prune only
runs on a **full** crawl with **zero** failed pages (a subset or one transient
fetch failure sets `prune=False`, so nothing is deleted), and the upload list
comes from the run's own results plus `static/` names — never from globbing
`outputs/clean/`, so leftover files in that folder are not uploaded.

The risk it cannot protect you from is a **stale local corpus**: if your
checkout produces fewer pages than the knowledge base holds, prune deletes the
difference. Upload from an up-to-date crawler branch, not from a working copy
you have been experimenting in.

There is currently **no "upload without crawling"** flag — the uploader takes
its file list from the crawl that just happened.

For the FAQ bot work in parts 2 and 3 you never need `--upload`. Use plain
`uv run python main.py` to refresh the corpus.

After this step, `outputs/clean/` holds the complete corpus: crawled pages plus
the generated `static/` pages, all named `Section_Subsection_Page.md`.

### 2. Build a vector store and chat

```bash
cd faq_bot/pro_implementation
../../.venv/bin/python ingest.py --method whole     # or markdown / recursive / llm
../../.venv/bin/python ingest.py --list             # what is in the store
../../.venv/bin/python ingest.py --delete NAME      # remove one collection

cd ..
../.venv/bin/python app.py                          # the chat UI
```

| Command | Costs | Writes |
|---|---|---|
| `ingest.py --method whole` | ~2¢ of embeddings, seconds | collection `whole_file` (81 chunks) |
| `ingest.py --method markdown` | ~2¢, seconds | collection `markdown` (128) |
| `ingest.py --method recursive` | ~2¢, seconds | collection `recursive_Chunksize_500_Overlap_100` (738) |
| `ingest.py --method llm` | 81 LLM calls, ~2 min | collection `llm_chunks` (~298) |
| `ingest.py --list` / `--delete` | nothing | nothing / deletes one collection |
| `app.py` | 1-3 LLM calls per question asked | nothing |

Each method writes its **own** collection, so all four can exist side by side
and be compared; re-running one method only rebuilds that one. Chunk size and
overlap are constants at the top of `ingest.py` (`recursive` only) — see
`faq_bot/README.md` §2.3.

`app.py` uses whatever `RagConfig`'s defaults say in
`faq_bot/pro_implementation/answer.py` — that class is how you set which
collection the chat UI reads and whether rewriting and reranking are on
(`faq_bot/README.md` §2.4).

### 3. Measure it

```bash
cd evaluation
../.venv/bin/python evaluator.py     # dashboard: pick store + rewrite/rerank
../.venv/bin/python eval.py 3        # one question, in the terminal
```

| Command | Costs |
|---|---|
| `evaluator.py` → **Retrieval**, both switches off | **no LLM calls** — embeddings only |
| `evaluator.py` → **Retrieval**, rewrite and/or rerank on | 82 or 164 LLM calls |
| `evaluator.py` → **Answer** | + 82 answer calls + 82 judge calls |
| `evaluator.py` → **Chunk Map** | nothing — reads the stored vectors |
| `eval.py <n>` | 2 LLM calls, one question |

Nothing here writes to the vector store or the corpus; the evaluation only
reads. Start with Retrieval and both switches off — it is free, and it is the
cheap way to compare the four chunking methods.

### What to re-run after a change

| You changed | Re-run |
|---|---|
| a PDF or Excel source file | its converter (**not automatic**) → `main.py` → `ingest.py` |
| `sites/*.yaml`, or the website changed | `main.py` → `ingest.py` |
| a file in `static/` by hand | `main.py` → `ingest.py` |
| the chunking method, size, or overlap | `ingest.py` only |
| the answering prompt (`faq_bot/pro_implementation/prompts.py`) | nothing — restart `app.py` |
| rewrite / rerank / which collection | nothing — they are UI checkboxes |
| `evaluation/tests.jsonl` | nothing — it is read on every run |

**Each script must be started from its own folder** (`faq_bot/` for `app.py`,
`evaluation/` for the evaluation scripts) — they use sibling imports. The
crawler and the converters run from the repo root via `uv run`.

Detailed documentation per part: this file and `HANDOVER.md` for part 1,
`faq_bot/README.md` for part 2, `evaluation/README.md` for part 3.

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
