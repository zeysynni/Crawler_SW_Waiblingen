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

### Phase 1 — Config: YAML + Pydantic models + loader
- **What:** create `sites/waiblingen.yaml` and the `Topic`/`Site` models +
  `load_site()` (section 4). Delete the 355-line dict body of `config.py`.
- **Verify:** a tiny script prints `load_site("sites/waiblingen.yaml").topics`.
  Feed it a deliberately broken YAML and confirm it raises a clear error.
- **Learn:** parsing/validation at the boundary; Pydantic validators.

### Phase 2 — Prompt builder consumes `path` + `instructions`
- **What:** update `get_user_prompt_structured_output(...)` to take a `Topic`
  + `root_url` and produce navigation text ("From {root_url}, click
  'Privatkunden', then 'Strom' …") plus the free-text instructions.
- **Verify:** print the generated prompt for two topics; read it as if you
  were the agent — is the navigation unambiguous?
- **Learn:** keep prompt *templates* (code) separate from prompt *content*
  (data); functions that transform data into other data are easy to test.

### Phase 3 — CLI in `main.py` (argparse)
- **What:**
  `python main.py --config sites/waiblingen.yaml --topics strom,kontakt`
  Default (no `--topics`) crawls every topic in the file.
- **Verify:** run with one topic, with several, with a bad topic name
  (should fail fast with the `KeyError` message from Phase 1).
- **Learn:** stdlib `argparse`; "make the easy thing the right thing".

### Phase 4 — Output pipeline: one file per topic, no overwrites
- **What:** fix the `save_json` overwrite bug (a topic with multiple crawl
  calls currently keeps only the last). Accumulate pages, write once per
  topic. Consolidate `utils.py` (json→md) into a clear `pipeline.py`.
- **Verify:** crawl a topic that visits several pages → the `.md` contains
  all pages, not just the last.
- **Learn:** idempotent outputs; spotting silent data-loss bugs.

### Phase 5 — PDF step, wired in
- **What:** fold `md2pdf.py` into the pipeline so `.md → .pdf` is one call
  (or an explicit `--pdf` flag). Remove the stale `json2md.py`.
- **Verify:** `customer_files/<topic>.pdf` is produced and opens.
- **Learn:** removing dead code is a feature; one obvious path per task.

### Phase 6 — Robustness: error handling + logging
- **What:** replace `print()` with the stdlib `logging` module; wrap each
  topic crawl in try/except so one failing topic doesn't kill the batch.
- **Verify:** force one topic to fail (bad URL) → others still complete and
  you get a clear log line for the failure.
- **Learn:** logging vs printing; partial failure handling in batch jobs.

### Phase 7 *(optional)* — Package layout + a few tests
- **What:** move code under `src/crawler/`, add `pytest` and unit tests for
  the pure functions (`load_site`, prompt builder, `json_to_markdown`).
- **Verify:** `uv run pytest` is green.
- **Learn:** what makes code testable (no I/O, no globals, data in → data
  out); why the agent call itself is the hardest thing to test.

### Phase 8 — Docs
- **What:** update `README.md` and `CLAUDE.md` to match the final shape.
- **Verify:** a stranger could clone, install with `uv`, and run a crawl
  using only the README.

---

## 6. Explicitly out of scope (for now)
- The `faq/` folder (ingestion, DB agents, Gradio bot). We resume this only
  after the crawler is solid. Leave it untouched.
- Concurrency / crawling many topics in parallel. Correct first, fast later.

---

## 7. Open questions to revisit
- **Run UX** beyond the CLI default above — happy with `--config` +
  `--topics`, or do you also want a `--all-sites` mode later?
- **`path` vs `url`** — is click-by-label navigation reliable enough on this
  site, or should we prefer explicit URLs for the tricky pages?
