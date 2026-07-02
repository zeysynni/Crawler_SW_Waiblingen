# PLAN.md — Generalizing the Web Crawler

> **Status:** active. This is the roadmap for turning the hard-coded
> single-site crawler into a **config-driven** crawler.
> **Working mode:** *you* implement each phase, *I* review and explain.
> **Out of scope for now:** the entire `faq/` folder — pretend it doesn't exist.

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

## 6. Explicitly out of scope (for now)
- The `faq/` folder (ingestion, DB agents, Gradio bot). We resume this only
  after the crawler is solid. Leave it untouched.
- Concurrency / crawling many topics in parallel. Correct first, fast later.

### Future work (not yet scoped — details pending)
- **Post-completion: code review + simplify (prompt-first).** Once all topics
  are crawled and stable, review `enrich.py` and **move page-specific logic that
  was added because of a single bad page out of the code and into per-topic
  `instructions` in `sites/waiblingen.yaml`.** The deterministic layer grew
  reactively (one page looked off → add a handler); much of that is *format*, not
  *reliability*, and belongs in prompts. Keep in code only what the LLM is
  genuinely *unreliable* at (drops content between runs): the FAQ/files/phone
  backstop, plus general infrastructure (URL resolution, fetch/encoding,
  script-stripping, table→Markdown). Content-dedup already lets good LLM output
  win, so shifting format to prompts won't duplicate. Candidates to reconsider:
  the opening-hours `<dl>` extractor, and — most of all — the **missed-section
  backstop `extract_prose_sections`**: it has repeatedly over-captured (a page's
  `<script>` blob, then hidden `.hide`/`display:none` sections), and prose is the
  LLM's *strength* (not something it drops like files/FAQ), so its payoff is low
  and its risk high. Consider narrowing or removing it and trusting the LLM for
  prose. See `DEVLOG.md` §12 for the full rationale.
- **Upload crawl results to an internal platform.** After a crawl, push the
  output `.md` files to an internal platform (web-UI upload today; likely needs
  credentials the user may not have yet — to confirm with colleagues). API/auth
  details TBD.
- **Downstream FAQ pipeline (the real goal).** On the platform, the `.md` files
  are to be **chunked**, then the information **filtered** before feeding the
  FAQ bot — this will require an additional processing step/agent. Far off; just
  recording the direction so the crawler output stays compatible with it.
- **Audit all topic URLs.** The `Privatkunden_Service_*` topics had the wrong
  `url` (pointed at `/Privatkunden/Strom`). This is likely a broader problem —
  check every topic's `url` in `sites/waiblingen.yaml` against the live site.
- **Topic sizing vs the 480s timeout.** Keep the per-topic timeout capped
  (≤480s) and size topics to fit it: if a merged topic is too deep to finish in
  time, split it in the YAML rather than raising the timeout further.
- **Switch the browser to `--headless`** in `mcp_params.py` once crawl quality
  is confirmed (it's run headed for now so the browser can be watched).
- **Per-subtopic file attribution.** PDFs are collected into one Downloads
  section per page, not attributed to the specific subtopic they sit under
  (e.g. Grundversorgung vs Ersatzversorgung). Minor; revisit if it matters.

---

## 7. Known issues (deferred — fix after the project runs end-to-end)

- **Expandable "+" content sometimes not opened.** ✅ RESOLVED (verified on the
  Bäder page). Root cause was twofold, found by inspecting the live DOM:
  1. *Visibility* — accordion answers are pre-rendered but `display:none` while
     collapsed, so they are excluded from the accessibility snapshot the agent
     reads. It saw only the heading.
  2. *Extraction fidelity* — even when content was made visible, `gpt-4.1-mini`
     did not transcribe the (tabular) tariff data into the structured output.

  Fix: (a) `scanner_instruction` now mandates expanding every collapsible before
  reading, plus a rule to transcribe tables/price lists in full as Markdown;
  (b) a deterministic best-effort init script (`scripts/expand_accordions.js`,
  wired via `@playwright/mcp --init-script`) force-opens accordions on load;
  (c) the model was upgraded `gpt-4.1-mini → gpt-4.1`. `gpt-4.1` reliably uses
  the browser tools and faithfully reproduces tables.

  Notes: `gpt-5.5` was tried and **returned empty output without browsing**
  (reasoning model + forced `output_type` short-circuits); revisit only with
  `tool_choice="required"`. `gpt-4.1` costs more than `-mini` — for the weekly
  31-topic run, consider testing a cheaper tier once quality is confirmed across
  more pages. Only Bäder is verified so far; spot-check other FAQ-heavy topics
  (E-Mobilität, Messstellenbetrieb).

---

## 8. Open questions to revisit
- **Run UX** beyond the CLI default above — happy with `--config` +
  `--topics`, or do you also want a `--all-sites` mode later?
- **`path` vs `url`** — is click-by-label navigation reliable enough on this
  site, or should we prefer explicit URLs for the tricky pages?
