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

> **Branch lineage:** this branch (`crawl4ai-crawler`) is the **general**
> crawl4ai tool. It started from `crawler-ahk` (AHK adaptation + the extractor
> mechanism), which came from the shared Stadtwerke-Waiblingen crawler — now
> renamed `crawler-sw-waiblingen`, where the knowledge-base upload stage and
> the GitLab schedule still live. Nothing site-specific is compiled in here any
> more: the cleaning sentinels moved into the site YAML (`stop_at`). History
> and rationale: `DEVLOG.md` — §14 (crawl4ai rewrite), §15 (review/hardening),
> §16 (AHK adaptation), §17 (general crawler + config UI). A non-technical
> explanation lives in `docs/HOW_IT_WORKS.md`.

## Environment & tooling

- Package manager: **`uv`** (not pip). Dependencies live in `pyproject.toml`;
  the lockfile is `uv.lock` (committed). Deps are deliberately minimal:
  `crawl4ai`, `beautifulsoup4` (extractors only), `pydantic`,
  `python-dotenv`, `pyyaml` (+ `pytest`). The `ui` **dependency group** adds
  `gradio` for `app.py` only — plain `uv sync` omits it, so the crawl install
  stays small; use `uv sync --group ui` to run the UI.
- **No API keys needed.** Optional `PUSHOVER_TOKEN`/`PUSHOVER_USER` in `.env`
  enable phone alerts (no-op if absent).
- Browser: `uv run playwright install chromium` once after `uv sync`.
- This project must stay **outside** any other `uv` project's directory tree —
  otherwise `uv` absorbs it as a workspace member. Keep it standalone.

**Setup:**
```bash
uv sync                  # add --group ui for the Gradio config UI
uv run playwright install chromium
```

## Commands

Always run through `uv run` so the project's own `.venv` is used.

```bash
uv run python main.py                                # crawl all sections
uv run python main.py --sections Service_Abfall-ABC  # a subset (comma-separated)
uv run pytest                                        # run the unit tests

uv run python app.py                                 # Gradio config UI (needs --group ui)
```

CLI flags: `--config` (default `sites/ahk-heidekreis.yaml`), `--sections`
(comma-separated section names, default all). Exit code is non-zero if any
page failed.

## Architecture

### Crawl pipeline
```
sites/*.yaml → config.load_site → crawl.crawl_site (crawl4ai, no LLM, retry×1)
    → outputs/raw/<page>.md        (full page as markdown, as crawled)
    → render.render_clean          (clean.clean_markdown — noise cut at the
                                    site's `stop_at`, links flattened,
                                    hierarchy h1 — or, for `extract:` sections,
                                    extract.EXTRACTORS over the fetched HTML)
    → outputs/clean/<page>.md      (the deliverable)
    → monitor.run_report           (per-page status/timing → log + Pushover)
```
`app.py` (the UI) is a second caller of the same pipeline: it builds a `Site`
from its form, calls `crawl_site` + `render_clean`, and writes nothing to disk.

### Key design points
- **Allowlist navigation, no discovery.** `sites/*.yaml` claims every page:
  each section is a base page (`path`, optional `url` override) plus optional
  `subpages` named by their **visible link text** on the base page. Labels
  resolve deterministically (`crawl.resolve_subpages`: exact match, else
  unique prefix, else unique substring; ambiguity or a miss is reported,
  never guessed). Unlisted pages are simply never crawled.
- **Raw vs clean.** `raw/` is crawl4ai's untouched HTML→markdown conversion.
  `clean/` is the deliverable, produced either by `clean.py` (rule-based
  noise cut) or, per section, by an extractor. Which of the two applies is
  decided in **one place**, `render.render_clean`, so the CLI and the UI
  cannot drift apart.
- **Cleaning is data, not code (`stop_at`).** Where a site's noise begins is a
  list of **regexes in the site YAML**, matched with `re.search` against each
  raw line; the clean form is cut at the first line that matches. Validated at
  config load, so a broken pattern fails in `load_site` and not mid-crawl.
  **Empty means cut nothing** — the safe default for an unknown site, because
  showing too much is recoverable and silently dropping content is not. Note
  `stop_at` does *not* apply to `extract:` sections (an extractor builds
  markdown from the HTML and never sees the page tail). Regexes in YAML need
  **single quotes** (`'^##\s+…'`) — YAML rejects `\s` inside double quotes;
  the same trap applies to Python docstrings, which is why `config.py`'s is
  `r"""`. See DEVLOG §17.
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
- `sites/ahk-heidekreis.yaml` — **the crawl allowlist (data).** One section,
  `Service/Abfall-ABC` with `extract: abfall_abc`, plus the site's `stop_at`
  cleaning patterns.
- `crawl.py` — crawl4ai integration: `crawl_site`/`crawl_section`/`_fetch`
  (retry once, timestamps) and the pure `resolve_subpages`. Returns
  `PageResult` objects (name, url, raw markdown, fetched html, extractor
  name, timings, notes).
- `extract.py` — **pure HTML→markdown extractors** (no I/O), keyed in
  `EXTRACTORS`. `abfall_abc`: BeautifulSoup finds `div.Abc-List`, parses the
  `data-items` JSON, emits `## <letter>` + a markdown table per letter; icon
  slugs (`aetzend`, `restmuelltonne_NO`, …) become readable hint text.
- `clean.py` — **pure markdown cleaning** (no I/O): `slug`, `strip_links`,
  `breadcrumb`, `content_span`, `clean_markdown`. Nothing site-specific is left
  in here — the cut points come from the site's `stop_at`. `content_span`
  returns the `[start, end)` line range that survives, and exists so the UI can
  report *where* a pattern cut without re-deriving the rule.
- `render.py` — **`render_clean(page, site)`**: the one place that chooses
  between `clean.clean_markdown` and the section's extractor. Pure, and
  testable without a crawl. Raises on a broken extractor by design — the caller
  decides what that means (CLI: failed page; UI: a message).
- `app.py` — **Gradio config UI** (`uv sync --group ui`). A form for one site
  YAML section that crawls it and shows **raw and clean side by side**, plus a
  line saying exactly where `stop_at` cut (or that nothing matched), plus the
  YAML snippet to paste into `sites/`. It is an *editor for the allowlist*, not
  a bypass: the same Pydantic models validate it, and it writes nothing to
  disk. No LLM — the regex helper is `re.escape`.
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
  cleaning, and extraction are rules, not heuristics or LLM judgment. This
  holds for the UI too: it *authors* config a human reviews and commits, and
  calls the same deterministic pipeline.
- **Prefer a loud wrong answer to a quiet one.** An unresolved label is a
  report line, a broken extractor is a failed page, a `stop_at` that matches
  nothing is a visible warning in the UI. Never a silently smaller file.
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
