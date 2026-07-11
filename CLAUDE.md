# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

**LLM-free web crawler** for knowledge-base content: crawl4ai (Playwright
underneath) fetches a configured allowlist of pages, converts them to
markdown, a deterministic cleaning layer cuts CMS noise, and the resulting
one-file-per-page markdown is uploaded to a knowledge-base API. The current
target is Stadtwerke Waiblingen, a German utility company.

> **Status:** this branch (`crawler-crawl4ai`) **replaced the former
> LLM-driven crawler** (gpt-5-mini agent + Playwright MCP + `enrich.py`) with
> a fully deterministic crawl4ai pipeline. No LLM is involved anywhere; a full
> site crawl (~62 pages) takes minutes and is reproducible. The old pipeline
> lives on in `main`/`crawler-llm-agent` history. Rationale,
> problems met, and their solutions: **`experiments/CRAWL4AI_SPIKE.md`** and
> **`DEVLOG.md` §14**. Read those before changing the crawl flow.

## Environment & tooling

- Package manager: **`uv`** (not pip). Dependencies live in `pyproject.toml`;
  the lockfile is `uv.lock` (committed). Deps are deliberately minimal:
  `crawl4ai`, `pydantic`, `python-dotenv`, `pyyaml`, `requests` (+ `pytest`).
- **No `OPENAI_API_KEY` needed.** Optional `PUSHOVER_TOKEN` / `PUSHOVER_USER`
  in `.env` enable phone alerts (no-op if absent). `--upload` needs
  `AIGATEWAY_KEY`; optional overrides `AIGATEWAY_KB_ID`,
  `AIGATEWAY_IMPORT_STRATEGY_ID`, `UPLOAD_STATE_FILE`.
- Browser: `uv run playwright install chromium` once after `uv sync`
  (crawl4ai drives Playwright directly — no MCP subprocess, no Node).
- This project must stay **outside** any other `uv` project's directory tree —
  otherwise `uv` absorbs it as a workspace member. Keep it standalone.

**Setup:**
```bash
uv sync
uv run playwright install chromium
# optional .env: PUSHOVER_TOKEN/PUSHOVER_USER, AIGATEWAY_KEY
```

## Commands

Always run through `uv run` so the project's own `.venv` is used.

```bash
uv run python main.py                                # crawl all sections
uv run python main.py --sections Privatkunden_Strom  # a subset (comma-separated)
uv run python main.py --upload                       # + push clean/*.md to the KB
uv run pytest                                        # run the unit tests
```

CLI flags: `--config` (default `sites/waiblingen.yaml`), `--sections`
(comma-separated section names, default all), `--upload` (opt-in: replace each
page's `.md` in the knowledge base; needs `AIGATEWAY_KEY`). Exit code is
non-zero if any page failed or an upload was put on hold.

## Architecture

### Crawl pipeline
```
sites/*.yaml → config.load_site → crawl.crawl_site (crawl4ai, no LLM, retry×1)
    → outputs/raw/<page>.md        (full page as markdown)
    → clean.clean_markdown         (noise cut, links flattened, hierarchy h1)
    → outputs/clean/<page>.md      (+ static/*.md copied in verbatim)
    → monitor.run_report           (per-page status/timing → log + Pushover)
    → uploader.upload_pages        (--upload only; one chunk per file, replace)
```
One page = one output file = one KB file = **one chunk** (no overlap;
the ~4 pages above the API's 8192-char cap split with a 1000-char overlap).
Outputs use stable, un-timestamped paths and are overwritten each run; the
previous clean file is measured just before overwrite for regression checks.

### Key design points
- **Allowlist navigation, no discovery.** `sites/*.yaml` claims every page:
  each section is a base page (`path`, optional `url` override) plus
  `subpages` named by their **visible link text** on the base page. Labels
  resolve deterministically (`crawl.resolve_subpages`: exact match, else
  unique prefix — teaser cards merge title+tagline into one link text — else
  unique substring; ambiguity or a miss is reported, never guessed). Unlisted
  pages are simply never crawled.
- **Raw vs clean.** `raw/` is crawl4ai's untouched HTML→markdown conversion
  (keeps everything, incl. content of *collapsed* accordions — the DOM is
  converted, not the a11y snapshot, so no expand-scripts are needed).
  `clean/` is the KB form produced by `clean.py`: keep from the first heading
  to the footer/cookie sentinels, h1 replaced by the site hierarchy from the
  page's own breadcrumb nav (`# Privatkunden - Strom - Ökostromtarif`;
  a differing marketing h1 is kept as `##` below), links flattened to plain
  text, images dropped.
- **Do NOT use crawl4ai's `PruningContentFilter`/`fit_markdown`** on this
  site: its statistical text/link-density scoring prunes exactly backwards
  (drops headings + download lists, keeps cookie-banner prose). Noise removal
  is rule-based in `clean.py` instead. See DEVLOG §14.
- **Static pages.** Content that cannot be crawled (the external Kundenportal
  login app) lives as hand-written markdown in `static/`; `main.py` copies it
  into `outputs/clean/` and it is uploaded like any crawled page.

### Key files
- `config.py` — `Section`/`Site` Pydantic models + `load_site()`. Holds no
  targets itself; validates the YAML allowlist (unknown keys fail loudly).
- `sites/*.yaml` — **the crawl allowlist (data).** One file per website.
  Add/edit targets here, not in Python. Labels must match the visible link
  text on the base page (whitespace-collapsed, case-insensitive).
- `crawl.py` — crawl4ai integration: `crawl_site`/`crawl_section`/`_fetch`
  (retry once, timestamps) and the pure `resolve_subpages`. Returns
  `PageResult` objects (name, url, raw markdown or error, timings, notes).
- `clean.py` — **pure markdown cleaning** (no I/O): `slug`, `strip_links`,
  `breadcrumb`, `clean_markdown`. The footer/cookie sentinels are specific to
  the Waiblingen CMS template — adjust them for a new site.
- `monitor.py` — `send_pushover`, `md_metrics`/`regressions` (clean-file
  baseline comparison), `run_report` (per-page ✓/✗/⚠ lines with reason,
  start time, duration, size; failures ordered first so Pushover's 1024-char
  truncation never hides them).
- `uploader.py` — knowledge-base upload (`--upload`). `chunk_params_for`
  returns **one chunk per file, overlap 0** (files above the API's hard
  `MAX_CHUNK=8192` cap can't stay whole: they're sent at 8192 with
  `SPLIT_OVERLAP=1000` and the API splits at structural boundaries). The
  sha-skip also compares chunk params, so param changes re-upload. `upload_pages` does **replace**
  (delete stored `file_id`, upload, persist new id in `upload_state.json` —
  gitignored, **must survive between runs**), skips unchanged files (sha256),
  prunes remote files whose local page vanished (full runs only — partial
  runs pass `prune=False`), retries once then raises `UploadHold` so a
  scheduler resumes ~24h later. NB: upload uses the API's **v2** endpoint,
  delete **v1** (intentional).
- `main.py` — entry point: argparse CLI, orchestration, static-page copy,
  regression measurement, report, exit code.
- `static/` — hand-written KB pages (currently `Kundenportal.md`).
- `tests/` — unit tests for the pure functions (`uv run pytest`).

## Conventions

- Keep **data separate from code** — crawl targets are YAML under `sites/`,
  not Python. Don't add new targets as Python dicts.
- Validate external input at the boundary with Pydantic; fail loudly with a
  clear message rather than producing empty output.
- Small, single-purpose functions. Prefer pure functions (data in → data out)
  for anything that isn't the crawl itself, and unit-test them.
- **Deterministic over stochastic, allowlist over discovery.** Navigation and
  cleaning are rules, not heuristics or LLM judgment. A page the YAML doesn't
  claim is not crawled; a label that doesn't resolve is a loud report line,
  never a silent guess.
- **Config over code for page problems.** A wrong/missing page is fixed in
  `sites/*.yaml` (adjust the label, add a `url` override) — new Python only
  for genuinely new *mechanisms*.
- **No hyperlinks in clean output.** The KB stores plain text; links are
  flattened (`strip_links`). If download URLs are ever needed, add a targeted
  exception for `/resources/` links — do not re-enable links wholesale.
- Use the shared `logging.getLogger("crawler")`; `basicConfig` is set once in
  `main()`. Do not add `print()`.
- Outputs go to `outputs/raw/` + `outputs/clean/` (gitignored).
