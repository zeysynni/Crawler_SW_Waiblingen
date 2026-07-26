# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

**LLM-free web crawler** for one target: the **Abfall-ABC** of AHK
(Abfallwirtschaft Heidekreis, a German waste-management utility) — the
complete A–Ö table of waste types with disposal locations, as one clean
markdown file. crawl4ai (Playwright underneath) fetches the configured page;
because the A–Ö table is a JS component that renders only one letter into the
DOM, the clean output is built by a deterministic **extractor** from the JSON
embedded in the fetched HTML (`div.Abc-List`'s `data-items` attribute, 798
entries). This is a **one-time/on-demand crawl** — there is no upload stage
and no CI schedule on this branch.

> **Branch lineage:** this branch (`crawler-ahk`) is the general crawl4ai
> tool from `crawler-crawl4ai` (the shared Stadtwerke-Waiblingen crawler),
> adapted to AHK and stripped of everything Waiblingen-specific (site YAML,
> static pages, the whole knowledge-base upload stage, CI). History and
> rationale: `DEVLOG.md` — §14 (crawl4ai rewrite), §15 (review/hardening),
> §16 (this AHK adaptation). A non-technical explanation of how the crawler
> works lives in `docs/HOW_IT_WORKS.md`.

## Environment & tooling

- Package manager: **`uv`** (not pip). Dependencies live in `pyproject.toml`;
  the lockfile is `uv.lock` (committed). Deps are deliberately minimal:
  `crawl4ai`, `beautifulsoup4` (extractors only), `pydantic`,
  `python-dotenv`, `pyyaml` (+ `pytest`).
- **No API keys needed.** Optional `PUSHOVER_TOKEN`/`PUSHOVER_USER` in `.env`
  enable phone alerts (no-op if absent).
- Browser: `uv run playwright install chromium` once after `uv sync`.
- This project must stay **outside** any other `uv` project's directory tree —
  otherwise `uv` absorbs it as a workspace member. Keep it standalone.

**Setup:**
```bash
uv sync
uv run playwright install chromium
```

## Commands

Always run through `uv run` so the project's own `.venv` is used.

```bash
uv run python main.py                                # crawl all sections
uv run python main.py --sections Service_Abfall-ABC  # a subset (comma-separated)
uv run pytest                                        # run the unit tests
```

CLI flags: `--config` (default `sites/ahk-heidekreis.yaml`), `--sections`
(comma-separated section names, default all). Exit code is non-zero if any
page failed.

## Architecture

### Crawl pipeline
```
sites/*.yaml → config.load_site → crawl.crawl_site (crawl4ai, no LLM, retry×1)
    → outputs/raw/<page>.md        (full page as markdown, as crawled)
    → clean.clean_markdown         (noise cut, links flattened, hierarchy h1)
      or extract.EXTRACTORS        (sections with `extract:` — clean built
                                    from the fetched HTML instead)
    → outputs/clean/<page>.md      (the deliverable)
    → monitor.run_report           (per-page status/timing → log + Pushover)
```

### Key design points
- **Allowlist navigation, no discovery.** `sites/*.yaml` claims every page:
  each section is a base page (`path`, optional `url` override) plus optional
  `subpages` named by their **visible link text** on the base page. Labels
  resolve deterministically (`crawl.resolve_subpages`: exact match, else
  unique prefix, else unique substring; ambiguity or a miss is reported,
  never guessed). Unlisted pages are simply never crawled.
- **Raw vs clean.** `raw/` is crawl4ai's untouched HTML→markdown conversion.
  `clean/` is the deliverable, produced either by `clean.py` (rule-based
  noise cut) or, per section, by an extractor.
- **Extractors** (`extract:` in the YAML). For JS components whose data is
  embedded server-side but never fully rendered: a pure function over the
  fetched HTML (`extract.EXTRACTORS[name](html, url)`) replaces
  `clean.clean_markdown` for that page. Currently `abfall_abc` (the A–Ö
  table). A broken extractor (site relaunch) marks the page failed — loud ✗
  in the report, never a silent empty file. Unknown extractor names fail at
  config load.
- **Do NOT use crawl4ai's `PruningContentFilter`/`fit_markdown`**: its
  statistical text/link-density scoring prunes exactly backwards on CMS sites
  (drops headings + download lists, keeps cookie-banner prose). Noise removal
  is rule-based in `clean.py` instead. See DEVLOG §14.

### Key files
- `config.py` — `Section`/`Site` Pydantic models + `load_site()`. Holds no
  targets itself; validates the YAML allowlist (unknown keys and unknown
  extractor names fail loudly).
- `sites/ahk-heidekreis.yaml` — **the crawl allowlist (data).** Currently one
  section: `Service/Abfall-ABC` with `extract: abfall_abc`.
- `crawl.py` — crawl4ai integration: `crawl_site`/`crawl_section`/`_fetch`
  (retry once, timestamps) and the pure `resolve_subpages`. Returns
  `PageResult` objects (name, url, raw markdown, fetched html, extractor
  name, timings, notes).
- `extract.py` — **pure HTML→markdown extractors** (no I/O), keyed in
  `EXTRACTORS`. `abfall_abc`: BeautifulSoup finds `div.Abc-List`, parses the
  `data-items` JSON, emits `## <letter>` + a markdown table per letter; icon
  slugs (`aetzend`, `restmuelltonne_NO`, …) become readable hint text.
- `clean.py` — pure markdown cleaning (no I/O): `slug`, `strip_links`,
  `breadcrumb`, `clean_markdown`. The footer/cookie sentinels
  (`## Weitere Links`, cookie-consent text) are specific to the AHK CMS
  template — adjust them for a new site.
- `monitor.py` — `send_pushover`, `md_metrics`/`regressions` (clean-file
  baseline comparison), `run_report` (per-page ✓/✗/⚠ lines with reason,
  start time, duration, size; failures first so Pushover's 1024-char
  truncation never hides them).
- `main.py` — entry point: argparse CLI, orchestration, regression
  measurement, report, exit code.
- `tests/` — unit tests for the pure functions (`uv run pytest`).

## Conventions

- Keep **data separate from code** — crawl targets are YAML under `sites/`,
  not Python. Don't add new targets as Python dicts.
- Validate external input at the boundary with Pydantic; fail loudly with a
  clear message rather than producing empty output.
- Small, single-purpose functions. Prefer pure functions (data in → data out)
  for anything that isn't the crawl itself, and unit-test them.
- **Deterministic over stochastic, allowlist over discovery.** Navigation,
  cleaning, and extraction are rules, not heuristics or LLM judgment.
- **Config over code for page problems.** A wrong/missing page is fixed in
  `sites/*.yaml` — new Python only for genuinely new *mechanisms* (the
  `extract:` mechanism is the precedent: new code because JS-embedded data
  is a new mechanism, registered once, then opted into via YAML).
- **No hyperlinks in clean output.** Links are flattened (`strip_links`);
  extractor tables carry plain text only.
- Use the shared `logging.getLogger("crawler")`; `basicConfig` is set once in
  `main()`. Do not add `print()`.
- Outputs go to `outputs/raw/` + `outputs/clean/` (gitignored).
- **Note:** `upload_state.json` in the working tree (gitignored, untracked)
  belongs to the *Waiblingen* branch's knowledge-base upload — do not delete
  it here; branches share untracked files.
