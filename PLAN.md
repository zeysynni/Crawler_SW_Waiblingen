# PLAN.md — Generalizing the Web Crawler

> **Status:** Phases 0–9 (the config-driven LLM crawler) are DONE and archived
> below as history. **Phase 10 — this branch (`experiment/new-crawl-tool`) —
> replaced the LLM crawler entirely with a deterministic crawl4ai pipeline**;
> see the Phase 10 section and `DEVLOG.md` §14. The `faq/` folder was removed
> on this branch (it lives on in `main`).

---

## 1. Goal

Today the crawler is wired to one website (Stadtwerke Waiblingen) through 355
lines of hand-written dictionaries in `config.py`. The goal is:

> Supply a **root URL** and a list of **topics** (each with a navigation
> *path* and free-text *instructions*) in a config file. Run one command.
> Get clean `.md` (and optionally `.pdf`) output per topic.

The crawler should navigate from the homepage into sub-pages (e.g. click
"Products", then crawl), exactly as a person would.

---

## 2. Guiding principles (the "why" behind every phase)

These are the software-engineering ideas this refactor is teaching. Keep
coming back to them — they apply far beyond this project.

1. **Separate data from code.** *What* to crawl is data (YAML). *How* to
   crawl is code (Python). Mixing them is the root cause of today's pain.
2. **Single source of truth.** One place defines a thing. No copy-pasted
   prompts or config in two files.
3. **Validate at the boundary.** The moment untrusted data (a YAML file)
   enters the program, parse it into typed objects (Pydantic) and reject bad
   input *loudly*. Never let a typo silently produce empty output.
4. **Small functions, one job each.** A function you can describe in one
   sentence is a function you can test and reuse.
5. **Make the easy thing the right thing.** Switching targets should be a
   command-line argument, not a code edit.
6. **Fail fast with good messages.** A crash that says *"topic 'strm' not
   found in waiblingen.yaml"* is worth more than a 200-line traceback.

---

## 3. Target project structure

```
.
├── pyproject.toml          # uv-managed deps (replaces requirements.txt)
├── README.md
├── CLAUDE.md
├── PLAN.md
├── .env                    # OPENAI_API_KEY
├── sites/                  # ← DATA: one YAML file per website
│   └── waiblingen.yaml
├── src/crawler/            # ← CODE (a proper package)
│   ├── __init__.py
│   ├── config.py           # settings + Pydantic models + load_site()
│   ├── models.py           # Site, Topic schema  (may live in config.py at first)
│   ├── webpage_structure.py# crawl OUTPUT schema (Webpages/Page/Block/…)
│   ├── prompts.py          # system instruction + prompt builder
│   ├── agent.py            # build + run the crawl agent (was crawl_agent.py)
│   ├── agent_utils.py      # MCP server + runner helpers (already good)
│   ├── mcp_params.py       # MCP server configs
│   └── pipeline.py         # json → markdown → pdf (was utils.py + md2pdf.py)
├── outputs/                # generated .json / .md  (gitignored)
└── customer_files/         # generated .pdf
```

> You don't have to adopt `src/` layout if it feels like too much at once —
> a flat layout is fine for Phase 1. The `src/` move is Phase 7, optional.

---

## 4. The config schema (Phase 1 in detail)

### Data — `sites/waiblingen.yaml`
```yaml
site: stadtwerke-waiblingen
root_url: https://www.stadtwerke-waiblingen.de
topics:
  - name: privatkunden_strom
    path: [Privatkunden, Strom]        # click these labels in order from root
    instructions: |
      Crawl the Strom page top to bottom, then each sub-topic fully:
      Ökostromtarif, Grundversorgung. Expand every '+'.
  - name: kontakt
    url: /kontakt                       # OR give an exact relative URL
    instructions: |
      Crawl the contact page. Do not summarize phone numbers.
```

### Code — Pydantic models in `config.py`
```python
from pydantic import BaseModel, model_validator

class Topic(BaseModel):
    name: str                      # used as the output filename
    path: list[str] | None = None  # labels to click from the root page
    url:  str | None = None        # OR an explicit (relative or absolute) URL
    instructions: str = ""

    @model_validator(mode="after")
    def _need_path_or_url(self):
        if not self.path and not self.url:
            raise ValueError(f"topic '{self.name}' needs either 'path' or 'url'")
        return self

class Site(BaseModel):
    site: str
    root_url: str
    topics: list[Topic]

    def topic(self, name: str) -> Topic:
        for t in self.topics:
            if t.name == name:
                return t
        raise KeyError(f"topic '{name}' not found in this site config")
```

### Loader
```python
import yaml
from pathlib import Path

def load_site(path: str | Path) -> Site:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Site.model_validate(data)   # validates + raises on bad input
```

That's the whole "separate data from code" idea in ~30 lines. Notice
`config.py` no longer *contains* targets — it knows how to *load* them.

---

## 5. Phases (each one is a small, reviewable step)

Work top to bottom. After each phase: run it, confirm it works, then we
review before moving on. Don't start the next phase until the current one
runs green.

### Phase 0 — Tooling: migrate to `uv`
- **What:** replace `requirements.txt` with `pyproject.toml` managed by `uv`.
- **How:** `uv init` (creates `pyproject.toml`), then
  `uv add openai python-dotenv pydantic pyyaml rich pypandoc openai-agents`.
  Run things with `uv run python main.py`.
- **Verify:** `uv run python -c "import yaml, pydantic, agents"` succeeds.
- **Learn:** lockfiles & reproducible environments; why `@latest` / unpinned
  deps are a supply-chain risk.

### Phase 1 — Config: YAML + Pydantic models + loader ✅ DONE
- **What:** create `sites/waiblingen.yaml` and the `Topic`/`Site` models +
  `load_site()` (section 4). Delete the 355-line dict body of `config.py`.
- **Verify:** a tiny script prints `load_site("sites/waiblingen.yaml").topics`.
  Feed it a deliberately broken YAML and confirm it raises a clear error.
- **Learn:** parsing/validation at the boundary; Pydantic validators.
- **Status:** `sites/waiblingen.yaml` holds all 31 topics as data; `config.py`
  is now just the `Topic`/`Site` models + `load_site()`. Unit tests live in
  `tests/test_config.py` (6 cases, `uv run pytest` is green) — `pytest` was
  added as a dev dependency early (originally Phase 7) since the loader is the
  cleanest pure function to test. **Note:** `main.py` and `md2pdf.py` still
  import the old `structure`/`active_topic` names and won't run until Phase 2/3
  rewire them.

### Phase 2 — Prompt builder consumes `path` + `instructions` ✅ DONE
- **What:** update `get_user_prompt_structured_output(...)` to take a `Topic`
  + `root_url` and produce navigation text ("From {root_url}, click
  'Privatkunden', then 'Strom' …") plus the free-text instructions.
- **Verify:** print the generated prompt for two topics; read it as if you
  were the agent — is the navigation unambiguous?
- **Learn:** keep prompt *templates* (code) separate from prompt *content*
  (data); functions that transform data into other data are easy to test.
- **Status:** `get_user_prompt_structured_output(topic, root_url)` now builds
  the prompt from a `Topic`; a small `build_navigation()` helper handles both
  cases (explicit `url`, resolving relative URLs against `root_url`; or
  click-by-label `path`). When a topic has both, `url` wins (documented
  tiebreak). Tests in `tests/test_prompts.py` (5 cases). `main.py` still calls
  the old signature and won't run until Phase 3 rewires it.

### Phase 3 — CLI in `main.py` (argparse) ✅ DONE
- **What:**
  `python main.py --config sites/waiblingen.yaml --topics strom,kontakt`
  Default (no `--topics`) crawls every topic in the file.
- **Verify:** run with one topic, with several, with a bad topic name
  (should fail fast with the `KeyError` message from Phase 1).
- **Learn:** stdlib `argparse`; "make the easy thing the right thing".
- **Status:** `main.py` now parses `--config` (default `sites/waiblingen.yaml`)
  and `--topics` (comma-separated; default = all). Config + topic selection
  happen *before* the browser starts, so bad input fails fast and cheap (exit
  code 1, clean message — no agent, no LLM). The pure `select_topics()` helper
  is unit-tested in `tests/test_main.py` (5 cases). A real end-to-end crawl is
  untested here (needs API key + browser + live site).

### Phase 4 — Output pipeline: one file per topic, keep newest ✅ DONE
- **What:** fix the `save_json` overwrite bug (a topic with multiple crawl
  calls currently keeps only the last). Accumulate pages, write once per
  topic. Consolidate `utils.py` (json→md) into a clear `pipeline.py`.
- **Verify:** crawl a topic that visits several pages → the `.md` contains
  all pages, not just the last.
- **Learn:** idempotent outputs; spotting silent data-loss bugs.
- **Status:** new `pipeline.py` holds `save_json`, `json_to_markdown` (pure),
  and `write_markdown`. Decision: **keep only the newest version** — files use
  stable, un-timestamped paths (`outputs/<topic>.json|md`) and overwrite each
  run (suits a weekly re-crawl). The old multi-subpart overwrite bug is gone by
  design: one topic = one crawl call now. Deleted dead `utils.py` and
  `json2md.py`. Tests in `tests/test_pipeline.py` (6 cases). `md2pdf.py`
  untouched — folded in at Phase 5.

### Phase 5 — PDF step, wired in ✅ DONE
- **What:** fold `md2pdf.py` into the pipeline so `.md → .pdf` is one call
  (or an explicit `--pdf` flag). Remove the stale `json2md.py`.
- **Verify:** `customer_files/<topic>.pdf` is produced and opens.
- **Learn:** removing dead code is a feature; one obvious path per task.
- **Status:** `pipeline.to_pdf()` converts one `.md` → `customer_files/<topic>.pdf`
  via pypandoc/xelatex. **Opt-in** behind `main.py --pdf` (default off), so the
  default/weekly run produces MD only and never needs LaTeX — keeping the CI
  image lean (the deployment decision). Deleted the stale `md2pdf.py` (its
  `__main__` referenced the removed `active_topic`). Tests in
  `tests/test_pipeline.py` mock pandoc for the arg/path logic; a real
  conversion was verified manually (valid PDF, umlauts render).

### Phase 6 — Robustness: error handling + logging ✅ DONE
- **What:** replace `print()` with the stdlib `logging` module; wrap each
  topic crawl in try/except so one failing topic doesn't kill the batch.
- **Verify:** force one topic to fail (bad URL) → others still complete and
  you get a clear log line for the failure.
- **Learn:** logging vs printing; partial failure handling in batch jobs.
- **Status:** `main.py`, `crawl_agent.py`, `pipeline.py` use a shared
  `logging.getLogger("crawler")`; `basicConfig` (timestamp/level/name) is set
  once in `main()`. The per-topic loop catches `Exception`, logs the full
  traceback via `log.exception`, and continues; an end-of-run summary reports
  succeeded/failed counts + the failed topic names. No `print()` remains.
  Batch-resilience verified by simulation (the loop needs the live agent, so
  it has no unit test).

### Phase 7 *(optional)* — Package layout + a few tests ✅ TESTS DONE (layout skipped)
- **What:** move code under `src/crawler/`, add `pytest` and unit tests for
  the pure functions (`load_site`, prompt builder, `json_to_markdown`).
- **Verify:** `uv run pytest` is green.
- **Learn:** what makes code testable (no I/O, no globals, data in → data
  out); why the agent call itself is the hardest thing to test.
- **Status:** `pytest` added (Phase 1) and used throughout — 23 tests across
  `tests/test_config.py`, `test_prompts.py`, `test_main.py`, `test_pipeline.py`.
  The `src/crawler/` package move was **intentionally skipped**: a flat layout
  is fine at this size, and the import surface is small. Revisit only if the
  module count grows.

### Phase 8 — Docs + deployment ✅ DONE
- **What:** update `README.md` and `CLAUDE.md` to match the final shape.
- **Verify:** a stranger could clone, install with `uv`, and run a crawl
  using only the README.
- **Status:** `README.md` rewritten (uv install, CLI usage, YAML targets, tests)
  and `CLAUDE.md` updated to the finished shape. Added `.env.example` and a
  starter `.gitlab-ci.yml` (scheduled weekly pipeline, MD-only so no LaTeX).
  **Deployment gotcha documented:** the crawler uses the *Node* `@playwright/mcp`
  (pinned 0.0.76 → Playwright 1.61.0-alpha), NOT the Python playwright package,
  so CI must install the matching Chromium at runtime — and that pinned alpha is
  a fragility to revisit. The CI file is a template; it needs one round of
  validation on a real runner.

### Phase 9 — Crawl quality, robustness & monitoring ✅ DONE (post-refactor)
- **What:** make the output trustworthy and the pipeline unattended-safe.
- **Status (see `DEVLOG.md` for the full story):**
  - **Model → `gpt-5-mini`.** gpt-4.1-mini *hallucinated* sub-page content;
    gpt-4.1 is rate-limited on deep crawls; gpt-5.x needs `tool_choice="required"`
    to browse. gpt-5-mini is faithful, fits the rate limit, and is cheap.
  - **Accordion visibility** — collapsed Bootstrap content is `display:none`
    (invisible to the a11y snapshot); a CSS-override init script force-opens it.
  - **Deterministic enrichment (`enrich.py`)** — FAQ Q&As, PDF files, and tables
    are extracted from the HTML (not left to the stochastic LLM), deduped,
    attached in place; whole `<h2>` prose sections the LLM drops are recovered.
  - **`.md` rendering** — follow JSON (document) order, no injected
    labels/titles, preserve the page's Markdown formatting, show a page name.
  - **Robustness** — per-topic error isolation, rate-limit retries, 480s cap,
    keep-newest outputs.
  - **Monitoring (`monitor.py`)** — Pushover alerts on failure/regression + a
    detailed end-of-run summary (totals + per-topic breakdown).

- **Phase 9b — subtopics, retries & enrichment hardening (this session).** The
  **whole Stadtwerke Waiblingen site is now crawled** (Privatkunden,
  Geschäftskunden, Netze, standalone). Added since Phase 9 (see `DEVLOG.md`):
  - **`subtopics`** — multi-page topics list sub-page *labels*; `resolve_subtopics`
    maps them to real URLs off the base page (deterministic, no LLM click-nav).
  - **Retry-on-failure** — `crawl_topic` re-launches a topic on transient failure,
    bounded by `--retries` (cap `MAX_RETRIES=3`).
  - **Enrichment hardening** — files by heading incl. non-PDF (doc/xls/ppt/zip/…);
    `tel:` phone recovery; opening-hours `<dl>`; ä-URL encoding; `_soup` strips
    script/style; `_is_hidden` skips hidden content; **content-based dedup**
    (never label-only); `_locate_heading` matches by specificity; unified
    by-section FAQ/accordion attachment.
  - Model still `gpt-5-mini`; ~77 unit tests.

---

### Phase 10 — Replace the LLM crawler with crawl4ai ✅ DONE (this branch)

**Motivation.** After Phase 9, the LLM's only remaining job was prose capture:
navigation was deterministic (`subtopics`), structured content came from
`enrich.py`. A spike (`experiments/CRAWL4AI_SPIKE.md`) showed crawl4ai's plain
HTML→markdown conversion captures everything deterministically — including
collapsed accordions — so the stochastic layer (and its cost, retries, MCP/CI
plumbing, and the whole recover-what-the-LLM-dropped problem class) was removed.

**What changed:**
- New pipeline: `sites/*.yaml` (allowlist) → `config.py` → `crawl.py`
  (crawl4ai, retry×1) → `outputs/raw/` → `clean.py` → `outputs/clean/`
  (+ `static/*.md`) → `monitor.run_report` → `uploader.upload_pages`.
- Deleted: `crawl_agent.py`, `prompts.py`, `agent_utils.py`, `mcp_params.py`,
  `webpage_structure.py`, `enrich.py`, `pipeline.py` (incl. PDF export),
  `scripts/`, the old `sites/` format, `faq/`, and their tests.
- Upload: **one chunk per file, no overlap** (one page = one retrieval unit);
  `prune_stale` keeps the KB in sync with renames/removals on full runs.
- Monitoring: per-page ✓/✗/⚠ report with failure reason, start time,
  duration, size; failures ordered first for Pushover truncation.
- Problems met + solutions: `DEVLOG.md` §14 (fit_markdown prunes backwards →
  rule-based `clean.py`; marketing h1s → breadcrumb-nav hierarchy; teaser-card
  link texts → staged label matching; off-section URLs → YAML-derived names +
  `url:` override; external apps → `static/`).

---

## 6. Explicitly out of scope (for now)
- The FAQ bot: the `faq/` folder was **removed on this branch** (it lives on
  in `main`); building on it resumes only after the crawler is settled.
- Concurrency / crawling many sections in parallel. A full run takes ~2 min
  sequentially — correct first, fast later.

### Future work (not yet scoped — details pending)

> Most pre-Phase-10 items (prompt-first `enrich.py` cleanup, topic sizing vs
> the 480s agent timeout, `--headless` for MCP, per-subtopic file
> attribution, model choice) are **obsolete** — the LLM layer they addressed
> no longer exists. Still relevant:

- **Downstream retrieval quality.** One page = one chunk is a clean first
  pass (Phase 10); measure the FAQ bot's retrieval once it consumes the new
  KB and revisit chunking (e.g. splitting the 4 pages above the API's
  8192-char cap at `##` headings ourselves instead of letting the API split).
- **Duplicate pages.** The site serves identical content in two sections
  (Privatkunden Trinkwasser ≡ Geschäftskunden Wasser; both Fernwärme pages).
  Kept faithfully for now; dedupe if retrieval double-hits become a problem.
- **Old remote KB files.** `upload_state.json` from the pre-Phase-10 pipeline
  was never persisted, so files the old crawler uploaded can't be auto-pruned.
  One manual cleanup of the KB may be needed before the first full `--upload`.
- **Cosmetics in clean output.** Opening hours lose the day/time separator
  (`Montag08:00 …`); footer-template sections (`So erreichen Sie uns`,
  `Öffnungszeiten`) repeat on every page. Both harmless; fix in `clean.py`
  only if the bot's answers suffer.
- **CI schedule.** Wire the weekly GitLab run: `uv sync`,
  `uv run playwright install chromium`, `uv run python main.py --upload`,
  persist `upload_state.json` between runs (cache/artifact).

---

## 7. Known issues (deferred)

- The pre-Phase-10 issue list (accordion expansion, model choice, agent
  timeouts) is resolved by removal: those were failure modes of the LLM
  layer. Current known limitations live in `experiments/CRAWL4AI_SPIKE.md`
  ("Known leftovers") and the Future-work list above.
- `clean.py`'s footer/cookie sentinels are specific to the Waiblingen CMS
  template. A second site needs its own sentinels (or a generalized
  mechanism) — deliberate, documented coupling.

---

## 8. Open questions to revisit
- **Multi-site support:** the allowlist format is site-agnostic, but
  `clean.py`'s sentinels are not. Generalize when (if) a second site arrives,
  not before.
- **Should download URLs return?** Links are stripped from clean output. If
  the FAQ bot should hand out PDF links, add a targeted exception for
  `/resources/` links in `strip_links` (see CLAUDE.md conventions).
