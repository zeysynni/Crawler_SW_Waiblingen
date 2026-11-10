# Stadtwerke Waiblingen: knowledge-base crawler and FAQ bot

Three parts that share one corpus of markdown files:

1. **A deterministic web crawler** (crawl4ai, no LLM) that turns an allowlist of
   stadtwerke-waiblingen.de pages, plus PDFs and an Excel sheet, into clean
   knowledge-base markdown — **one page = one `.md` file** — and keeps a company
   knowledge base in sync with it every week.
2. **A RAG FAQ bot** that embeds that markdown into a local Chroma store and
   answers customer questions in a Gradio chat, with six switchable chunking
   methods and optional query rewriting and reranking.
3. **An evaluation** of the bot: 148 real and generated customer questions,
   retrieval metrics, an LLM judge and a dashboard to compare configurations.

```
PART 1  CORPUS                                   PART 2  FAQ BOT      PART 3  EVALUATION
──────────────────────────────────────────       ───────────────      ──────────────────
PDFs/*.pdf     ─┐                                outputs/clean/*.md   tests.jsonl
Excels/*.xlsx  ─┼→ static/*.md ─┐                        ↓                  ↓
the website    ─┴→ crawl ───────┴→ outputs/clean/ →  vectors  →  answers  →  scores
                                       └→ --upload → company knowledge base (weekly, GitLab CI)
```

**The contract between the parts is `outputs/clean/*.md`.** Part 1 writes it;
parts 2 and 3 only read it. Parts 2 and 3 are a personal learning project and
never touch the company knowledge base.

> **Branches.** The production crawler is `crawler-sw-waiblingen` (it feeds the
> weekly GitLab job). `main` holds a copy of the crawler plus parts 2 and 3. See
> `HANDOVER.md` Part B.

---

## Highlights

**Crawler**
- **No LLM, reproducible:** crawl4ai fetches and converts; rule-based cleaning cuts
  menus, footer and cookie banner, flattens links, and titles each file with the
  site hierarchy (`# Privatkunden - Strom - Ökostromtarif`). ~62 pages in minutes.
- **Allowlist, no discovery:** `sites/*.yaml` names every page by its link text;
  unlisted pages are never crawled, an unresolved label is reported, never guessed.
- **Non-crawlable sources** (PDFs, the colleagues' Excel) are converted in CI before
  every crawl, so replacing a file in the repo — from the browser — is the whole
  update procedure. A tariff PDF that loses its table **fails the run** instead of
  shipping prices as prose.
- **Stateless upload:** each run reconciles against the live knowledge base (replace
  by filename, prune what is gone — only on a complete, error-free run).
- **Run report** per page (✓/✗/⚠, timing, size regressions) to the log and Pushover.

**FAQ bot and evaluation**
- Six chunking methods side by side, one Chroma collection each: whole page,
  markdown, recursive, LLM-chosen split points, and the whole page with an
  LLM-written summary or example questions on top. Best so far: **whole page +
  summary**.
- The LLM never retypes source text — it chooses split points or writes a header;
  the text is always cut from the page itself.
- Dashboard: MRR, nDCG, keyword coverage, LLM-judged answer quality, by question
  category, plus a t-SNE map of the chunks.

---

## Repository layout

```
crawler/        crawler code: main.py crawl.py clean.py config.py uploader.py monitor.py
sites/*.yaml    the crawl allowlist (data, edited by hand)
PDFs/ Excels/   source documents + their converters (pdf2md.py, xlsx2md.py)
static/         pages that are not crawled (generated from PDFs/Excels, plus Kundenportal.md)
outputs/        the corpus: raw/ and clean/ (gitignored, rebuildable)
textutils.py    helpers shared by the crawler and the converters
faq_bot/        part 2: pro_implementation/ (ingest, answer, prompts), app.py (chat)
evaluation/     part 3: tests.jsonl, eval.py, evaluator.py (dashboard)
tests/          unit tests for the crawler and converters
.gitlab-ci.yml  the weekly crawl + upload
```

Code is grouped; everything a person edits or replaces (allowlist, PDFs, Excel,
static pages) stays at the top level where it is easy to find.

---

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group convert --group bot     # crawler + converters + FAQ bot
uv run playwright install chromium      # browser for crawl4ai
cp .env.example .env                    # OPENAI_API_KEY for the bot; others optional
```

## Quick start

```bash
uv run python crawler/main.py                                   # crawl → outputs/clean/
cd faq_bot/pro_implementation && uv run python ingest.py --method all   # build the stores
cd .. && uv run python app.py                                    # chat
cd ../evaluation && uv run python evaluator.py                   # evaluate
uv run pytest                                                    # tests (from the repo root)
```

`crawler/main.py` never touches the company knowledge base; only `--upload`
does, and that is normally left to CI.

---

## Documentation

| File | Read it for |
|---|---|
| `HANDOVER.md` | **how to use every part** — browser-only knowledge-base upkeep, every command, the FAQ bot, the evaluation, troubleshooting |
| `DEVLOG.md` | **why** things are the way they are — decisions, problems met, history (index at the top) |
| `CLAUDE.md` | architecture and conventions, written for Claude Code |
