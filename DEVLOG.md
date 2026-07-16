# DEVLOG — LLM Web Crawler

A detailed log of the work done on this crawler: what we built, the problems we
hit, the decisions we made, and *why*. For day-to-day guidance see `CLAUDE.md`.
This file keeps the details. (`PLAN.md`, referenced in older sections below,
was a personal planning doc and is no longer tracked in the repo.)

---

## 1. Where we started

A working but rigid crawler hard-wired to one site (Stadtwerke Waiblingen): a
355-line `config.py` of Python dicts, no CLI, output schema + JSON→Markdown
conversion, and an OpenAI Agents SDK agent driving a Playwright browser over MCP.
Goal: turn it into a **config-driven, reliable, unattended** tool.

---

## 2. The config-driven refactor (Phases 0–8)

Done in small, reviewed steps (full status in `PLAN.md`):

- **Phase 0 — tooling:** `uv` + `pyproject.toml` + committed `uv.lock`.
- **Phase 1 — config:** crawl targets moved to `sites/waiblingen.yaml`; `config.py`
  became `Topic`/`Site` Pydantic models + `load_site()` (validate at the boundary,
  fail loudly). 31 topics migrated faithfully.
- **Phase 2 — prompts:** `get_user_prompt_structured_output(topic, root_url)` +
  `build_navigation` (supports explicit `url` and click-`path`).
- **Phase 3 — CLI:** `main.py` argparse (`--config`, `--topics`); bad input fails
  fast before the browser starts.
- **Phase 4 — pipeline:** `pipeline.py` consolidates JSON→Markdown; stable,
  un-timestamped, overwritten (keep-newest) outputs. Removed dead `utils.py`,
  `json2md.py`, `md2pdf.py`.
- **Phase 5 — PDF:** `to_pdf()` behind an opt-in `--pdf` flag (so the default/CI
  run needs no LaTeX).
- **Phase 6 — robustness:** stdlib `logging` + per-topic try/except (one bad
  topic doesn't abort the batch) + end-of-run summary.
- **Phase 7 — tests:** unit tests for the pure functions (`load_site`, prompt
  builder, JSON→MD, etc.). `src/` package layout intentionally skipped.
- **Phase 8 — docs/deploy:** README + CLAUDE.md, `.env.example`, a starter
  `.gitlab-ci.yml`.

**Deployment note:** the crawler uses the **Node** `@playwright/mcp` (pinned
`0.0.76`), *not* the Python `playwright` package. `@playwright/mcp@0.0.76`
depends on Playwright `1.61.0-alpha`, so CI must install a matching Chromium at
runtime. Pinned weekly schedule, MD-only (no LaTeX) → lean image.

---

## 3. Crawl quality & robustness (the hard part)

This is where most of the work went. Each problem below was found by inspecting
real output and the live DOM.

### 3.1 The "+" button / accordion problem

**Symptom:** on pages with expandable accordions (e.g. Bäder "Tarife Freibäder"),
the crawler captured the heading but not the body.

**Root cause (found by inspecting the live DOM):** the answer text *is*
server-rendered in the HTML, but while collapsed it's `display:none`. The agent
reads Playwright's **accessibility snapshot**, which **excludes `display:none`
elements** — so it literally couldn't see the answer, only the heading.

**Fixes:**
- `scripts/expand_accordions.js`, loaded via `@playwright/mcp --init-script`,
  force-opens accordions on every page. Crucially it uses a **CSS `!important`
  override** (`display:block`) rather than clicking — clicking a *single-open*
  accordion (`data-bs-parent`) re-collapses the others; the CSS override beats
  Bootstrap's re-collapse.
- We later moved FAQ capture off the LLM entirely (see 3.4).

### 3.2 The model saga

We went through several models before landing:

| Model | Browses? | Faithful? | Deep topics fit rate limit? |
|---|---|---|---|
| `gpt-4.1-mini` | ✅ | ❌ **hallucinated** sub-page content | ✅ |
| `gpt-4.1` | ✅ | ✅ | ❌ 30k TPM → timed out on deep crawls |
| `gpt-5.5` | ❌ returns empty (no browse) | — | — |
| **`gpt-5-mini`** | ✅ (forced) | ✅ | ✅ |

Key findings:
- **gpt-4.1-mini hallucinated.** On the Ökostrom sub-page it invented generic
  "Wasserkraft/Windkraft" marketing text and omitted the real content
  ("toptarif-KLIMA plus", the actual bullet list). Healthy-looking output, wrong
  data — the worst failure mode for a FAQ source.
- **gpt-4.1 is rate-limited.** Each crawl request is ~21.7k tokens (large page
  snapshots), and the org's gpt-4.1 limit is **30k TPM** — so barely one request
  per minute. Retries don't help: a single request is ~70% of the budget. (And
  `gpt-4.1-long-context` is **not a callable model** — it's a rate-limit bucket
  for >128k-token requests, which ours aren't.)
- **gpt-5.x reasoning models return empty output unless tool use is forced.**
  Fix: `ModelSettings(tool_choice="required")` for `gpt-5*`, and omit
  `temperature` (reasoning models reject a custom one). With that, **gpt-5-mini**
  browses, is faithful, has TPM headroom, and is cheaper than gpt-4.1.

### 3.3 Rate limits (why "just wait" doesn't fix gpt-4.1)

TPM = tokens/minute over a rolling window. Waiting paces *many small* requests,
but can't help when a *single* request is a large fraction of the ceiling — a
request is atomic and can't be split across minutes. gpt-4.1's ~21.7k-token
requests vs 30k TPM = unusable for multi-turn crawling. gpt-5-mini's higher
ceiling sidesteps it. `--delay` (between topics) and `max_retries=8` were added
as resilience but don't change that math.

### 3.4 Deterministic enrichment (`enrich.py`) — the key architectural move

**Why:** even with content visible, gpt-5-mini's FAQ/file capture is
*stochastic* — different runs miss different things. But FAQ Q&As, files, and
tables are **structured and server-rendered**, so they can be extracted
deterministically from the HTML with BeautifulSoup — 100% consistent.

So the division of labour became: **LLM = prose + structure; deterministic =
FAQ + files + tables.** After each crawl, `enrich_topic` re-fetches each page's
HTML and:
- **FAQ:** extracts every accordion (`.accordion-item`) and `<details>` Q&A,
  rendering any `<table>` in an answer as a Markdown table. It's *authoritative*:
  it strips the LLM's partial/bare versions (faqs entries, bare question lines,
  question-as-subheading) and attaches the clean set **to the page's own FAQ
  section** (e.g. "Sie haben Fragen?"), not a separate block.
- **Files:** extracts every PDF link, **skipping empty-text "ghost" links**
  (hidden/stale `<a>` with no visible label — these caused "files that don't
  exist"). Authoritative: strips the LLM's file lists and attaches the real list
  to the page's own "Downloads…" section (or the last block) — never an injected
  "Downloads" title.
- **Missed prose sections:** recovers whole `<h2>` sections the LLM dropped
  entirely (e.g. "Trotz Umzug Kunde bleiben"), conservatively (only substantial
  prose with sentence punctuation; skips link-lists, empty, and download
  sections).

This guarantees the "extremely important" FAQ content regardless of model luck,
while staying general (Bootstrap + `<details>` cover most sites; the LLM still
carries exotic widgets).

### 3.5 `.md` rendering polish

Reviewing real `.md` output surfaced several issues, all fixed:
- **Doubled headings** (`## ## Service`) — the model sometimes puts `##` inside
  the heading field; `json_to_markdown` now strips leading `#`.
- **Injected labels/titles** — it used to add `**Dateien:**`, `**Kontakt:**`, and
  a faqs `### FAQ` title on top of the block heading, causing duplicates. Removed
  — content renders under the block heading only.
- **Wrong order** — an attempt to reorder blocks by the HTML document order
  (fuzzy heading-matching) made things worse. Removed: we **render blocks in JSON
  order** (the agent crawls top-to-bottom, which is reliable enough).
- **Page names** — each page section now starts with `# <title>` (from the HTML
  `<title>`, site suffix stripped), since the page name isn't always in the body.
- **A schema-simplification experiment** (drop `faqs`/`files`, add a `table`
  field) was tried behind a `_beta` output and **rolled back** — it didn't read
  better. Lesson learned: that rollback also reverted the *Markdown-formatting*
  prompt instructions, which we then had to re-add separately (keep **bold** and
  bullet/numbered lists as real Markdown).

### 3.6 Robustness & monitoring

- Per-topic `try/except` → one failure doesn't abort the batch.
- `max_turns=120`, `timeout=480s` (deep topics on the slower reasoning model
  need room; `max_turns` is the real loop guard).
- **`monitor.py` + Pushover:** alert on a topic failure, alert on a regression
  (a crawl that lost pages / ≥30% FAQs / ≥40% content vs the previous run), and
  **always** send a detailed end-of-run summary (totals + per-topic breakdown).
  No-ops cleanly if `PUSHOVER_TOKEN`/`PUSHOVER_USER` aren't set. This makes the
  "I never check it" weekly run safe.

---

## 4. Current architecture

```
sites/*.yaml → config.load_site → main.py → crawl_agent (gpt-5-mini) + Playwright MCP
    → Webpages (Pydantic) → outputs/{topic}.json
    → enrich.enrich_topic   (deterministic FAQ/file/table + missed-section recovery)
    → pipeline.json_to_markdown → outputs/{topic}.md   (JSON order, page name, no injected labels)
    → pipeline.to_pdf (only with --pdf) → customer_files/{topic}.pdf
    → monitor: regression check + Pushover summary
```

- **You only edit `sites/waiblingen.yaml`** to change targets/focus (URLs
  maintained by hand).
- **LLM** owns prose + structure (in document order, Markdown preserved).
- **`enrich.py`** owns FAQ + files + tables (deterministic, authoritative).
- **`monitor.py`** owns alerting.

---

## 5. Key decisions & rationale

| Decision | Why |
|---|---|
| LLM core (not a hand-coded scraper) | General across future customer sites + robust to unannounced layout changes (a deterministic scraper breaks silently). |
| `gpt-5-mini` | Faithful + browses (with forced tool use) + TPM headroom + cheap. |
| Deterministic FAQ/files/tables | They're structured & server-rendered; LLM capture of them is stochastic. |
| Force-open accordions via CSS, not clicks | Single-open Bootstrap accordions re-collapse on click; `!important` CSS doesn't. |
| Follow JSON order, no reorder | The agent crawls top-to-bottom; fuzzy HTML re-sorting did more harm than good. |
| No injected section titles | Attach content to the page's own sections; avoids "FAQ"/"Downloads" duplicates. |
| Keep-newest outputs | Suits a weekly re-crawl; nothing to accumulate (one topic = one crawl). |
| Pushover monitoring | Unattended pipeline needs to *notice* silent breakage. |

---

## 6. Known limitations

- **Prose completeness is not 100%.** The LLM occasionally drops a section; the
  missed-section backstop recovers whole `<h2>` sections but not every detail.
- **Per-subtopic file attribution.** A page's PDFs are collected into one
  Downloads section, not attributed to the specific subtopic they sit under.
- **Dead links.** A PDF link present in the HTML but pointing at a removed file
  is still listed (we'd need to HTTP-check each link to detect that).
- **`@playwright/mcp` → Playwright 1.61.0-alpha** is a pre-release pin; revisit
  for CI stability.

---

## 7. Future work (not yet scoped)

- **Upload `.md` to the internal platform** (needs credentials; web-UI upload
  today) → then **chunk + filter** for the downstream RAG/FAQ bot.
- **Audit all topic URLs** in `waiblingen.yaml` against the live site (the
  `Privatkunden_Service_*` topics had the wrong URL — likely broader).
- **Switch the MCP to `--headless`** once quality is confirmed (run headed now).
- The `faq/` folder (FAQ bot) — resumes after the crawler is solid.

---

## 8. Commit reference (this work, on `refactor/config-driven-crawler` — since renamed; the LLM crawler lives on in `crawler-llm-agent`)

```
6aa7c4c Refactor crawler to be config-driven, tested, and deployable
84767e1 Fix expandable-content extraction + make crawls robust
a41a857 Switch to gpt-5-mini and harden content capture
0161fa6 Fix Markdown heading doubling, raise timeout, keep topics shallow
b6776e9 Add deterministic FAQ + file enrichment
4311b6f Union-enrich FAQs/files; add Pushover monitoring for unattended runs
8579fa1 Enrich: dedup FAQs, keep FAQ in place, recover missed prose sections
645d78e Render <table>s inside accordions as Markdown, not flattened text
b2fb630 Detailed end-of-run summary (Pushover + log)
7f468bb Cleaner .md: page names, deterministic files, no injected labels, real order
c2aeb88 Follow JSON order, no injected titles, keep Markdown formatting
```

---

## 9. Design rationale: why LLM + BeautifulSoup (could it be BS-only?)

A recurring, fair question: if BeautifulSoup extracts files/FAQ/contacts/tables
deterministically (and more reliably than the LLM), why keep the LLM at all —
could the whole crawler be BeautifulSoup-only?

**What BeautifulSoup alone does well (on this site):** `tel:` numbers, PDF
links, accordion Q&As, tables — anything structured, server-rendered, with
predictable markup. For these, BS beats the LLM; that is exactly why they live
in `enrich.py`. This site also serves its content in the HTML (so `fetch_html`
+ BS reaches it without a browser).

**What BeautifulSoup alone does *not* give you:**
1. **Prose in reading order, without the boilerplate.** BS hands you the whole
   DOM — nav, footer, cookie banner, sidebar, content all mixed. Deciding which
   nodes are content vs. junk, in reading order, preserving bold/lists, is
   site-specific logic you'd hand-write per layout. The LLM does it adaptively.
2. **Generalization to new/changing sites.** The project goal (CLAUDE.md) is an
   *unattended* crawler for *future customers whose sites change without
   notice*. A pure-BS scraper is a bespoke parser pinned to one site's CSS
   classes; on a redesign or a new customer it silently breaks and someone
   rewrites the selectors. The LLM absorbs layout change for free.
3. **JS-rendered sites.** This site serves HTML, but many sites render
   client-side, where `urllib`+BS gets an empty shell and you need the browser
   (Playwright) the LLM drives.

**So it depends on scope:**
- Only this one, stable site → a well-written BS scraper could plausibly do the
  *whole* job: cheaper, faster, fully deterministic.
- Many customer sites that change unannounced (the stated goal) → keep the LLM
  for prose + structure, keep BS authoritative for structured content.

We already are that hybrid. The LLM is not there because BS *can't* parse HTML;
it is there so we don't hand-maintain a parser per customer and per redesign.
Note the direction of travel: every quality fix so far has moved work *from* the
LLM *into* deterministic BS extraction — so the sensible long-term posture is
"BS-authoritative wherever the markup is predictable, LLM for the fuzzy rest."

**Open idea (not done):** a BS-only extraction of one full page (prose included)
to compare side-by-side against the LLM output, to quantify the gap concretely.

---

## 10. Retry-on-failure for crawls

Crawls fail transiently — a turn hits the 480s timeout, the browser hiccups.
(Seen live: `Privatkunden_Waerme` timed out on attempt 1, succeeded on a plain
re-run.) So `main.py` now re-launches a failed topic instead of just recording
it as failed.

**Where:** `crawl_topic(agent, topic, root_url, make_pdf, attempts, backoff)`
wraps `process_topic`. The per-topic loop calls it; the loop's `except` only
fires once every attempt is used up (so one dead topic still can't abort the
batch).

**How it's bounded (not infinite):** a fixed-count loop, not a `while`:
```python
for attempt in range(1, attempts + 1):   # exactly `attempts` iterations
    try:
        return await process_topic(...)   # success → return, done
    except Exception:
        if attempt < attempts:
            await asyncio.sleep(backoff)   # not last → wait, continue
        else:
            raise                          # last → give up, propagate
```
`range(1, attempts + 1)` is decided before the loop; the body can only `return`
(success), `raise` (final failure), or fall through to the next fixed
iteration. Max `process_topic` calls = `attempts`.

**Values:**
- `attempts = retries + 1` (1 initial try + N retries).
- `retries = max(0, min(args.retries, MAX_RETRIES))`, `MAX_RETRIES = 3`.
- CLI: `--retries` (default **2** → **3 attempts**; `0` disables),
  `--retry-backoff` (default **15**s between tries).
- Ceiling: 4 attempts (asking for ≥3 retries clamps to 3).

**Note / possible future tweak:** retries reuse the *same* agent + MCP browser
(created once per run). That handles timeouts/API blips fine. If a failure is a
truly wedged browser, a retry might not clear it — a future option is to
recreate the MCP server between attempts. Not needed so far.

---

## 11. Why recovered FAQs/accordions land near the end of the page

Observation: deterministically-recovered FAQ/accordion panels tend to appear
toward the bottom of a page. This is expected; two mechanisms cause it, and for
most pages it faithfully mirrors the source layout.

**1. Attach-after-the-matching-heading (the usual case).** The by-section logic
finds the block or subheading whose text matches the accordions' section heading
and inserts the panel *right after it* (or at the end of that block's segments).
The agent captures a page top-to-bottom, and FAQ/accordion sections usually live
near the bottom (after the intro prose, before the contact block). So the
matching heading/subheading is already late in the block's segment list, and the
recovered FAQ inherits that late position. Example segment orders:
- `abschlag`: one block = text, subheading, subheading, contacts, **faqs(9)**,
  contacts — FAQ near the end of the block.
- `Umzugsservice`: block[0] = text, subheading, text, text, subheading, text,
  subheading, **faqs(10)**, subheading, contacts… — FAQ after the 3rd subheading.
It is not arbitrary: it tracks where the accordions actually are on the page.

**2. Append-as-new-block fallback (the stronger "end of page" case).** If the
accordions' section heading matches NO block the agent captured, we can't place
them in context, so they're appended as a new block at the very end of the page:
```python
loc = _locate_heading(page, g["heading"])
if loc is not None:
    ... insert at the matched position ...
else:
    page["blocks"].append({"heading": g["heading"] or "FAQ", ...})   # end of page
```
Better a real end-block than a wrong in-context guess.

**Why not pull FAQs higher?** That would mean overriding the page's own document
order, which we deliberately avoid (see the "trust JSON/document order, don't
reorder" principle). The trend toward the end is the combination of (a) where
FAQs sit on the source pages and (b) the conservative no-match fallback.

---

## 12. Code vs. prompt — did we over-build the deterministic layer?

A reflection prompted by the question "did we work too much on code? individual
things should go into the prompt." Short answer: partly yes.

**Where we over-reacted with code.** Several `enrich.py` additions were triggered
by *one bad page*: the opening-hours `<dl>` extractor and some FAQ-placement
complexity came from specific pages looking off. These are the most
site-markup-specific and least robust. The clearest proof of the point: the
Netze_Übersicht installer lists were flattened by the deterministic
`_panel_text`, and a single **prompt** tweak in `waiblingen.yaml` made the LLM
reproduce the page's list format far better than the flattener ever could.

**Where the code genuinely earns it (don't rip out).** Evidence from this
session shows the LLM *drops structured content between runs*:
Netze/Stromnetz/Netzanschluss recovered **13 files** from enrichment, EEG 13,
Messstellenbetrieb 16 — several pages captured 0 files by the LLM itself. That
stochasticity is exactly why the deterministic FAQ/files/phone backstop exists.
Moving those fully into the prompt would reintroduce "sometimes half the PDFs
are missing."

**The healthy division that emerged:**
- **Code = infrastructure + backstop** — URL resolution, fetch/encoding,
  script/style stripping, table→Markdown (general, site-agnostic); plus
  FAQ/files/phones as a *dedup-aware* backstop.
- **Prompt (`sites/waiblingen.yaml`) = format + per-page nuance** — "expand these
  accordions", "format each installer as one list item with phone inline", "this
  page is simple, don't go deeper". Per-topic quirks live here.

**The key enabler: content-dedup.** `_qa_already_present` / `_content_norm` make
the deterministic layer *yield* to good LLM output (matched by content, not just
label), so pushing formatting into prompts does not cause duplication. This is
what makes a prompt-first approach safe.

**Recommendation.** Freeze the deterministic layer; resist adding more
page-specific handlers. Treat a per-page issue as a prompt/YAML tweak first, and
only keep/add code when the LLM is *unreliable* (drops content), not merely
*unformatted*. Candidate to reconsider later: whether opening-hours belongs in
code or a prompt line. See PLAN.md "Post-completion: code-review + simplify".

---

## 13. Knowledge-base upload stage (`uploader.py`, `--upload`)

Downstream of the crawl: push each topic's `outputs/<topic>.md` to the
`aigateway.eu` knowledge base (the RAG/FAQ bot's source). Opt-in via `--upload`;
the weekly deploy does crawl → upload in one run. `api_test/` remains a manual
sandbox (untracked); `uploader.py` is the canonical, tested logic.

**Decisions:**
- **Whole `.md`, not "facts".** The API has a separate facts endpoint, but
  splitting single facts out of their page context risks worse answers; we upload
  the whole Markdown and let the platform chunk.
- **Per-file chunking (`chunk_params_for`).** The default strategy accepts
  per-file params. Units on a page are mostly short with a long tail, so `p90`
  collapsed every file to the 800 floor (no variation). We size to the **p95**
  logical-unit length (heading/FAQ-bold split), clamped **[800, 2000]**, with
  **~10% overlap**. p95 keeps ~95% of units whole; overlap cushions the rare unit
  that still splits. Result varies per file (contact page ~800, FAQ-heavy ~1300).
- **Replace by stored `file_id`.** State is a keyed map
  `{ "<topic>.md": {file_id, sha256, chunk_params, uploaded_at} }` in
  `upload_state.json`. On re-crawl: delete the old `file_id`, upload the new file,
  store the new id. `sha256` lets us skip files unchanged since the last upload.
- **Failure policy.** Retry a failed delete/upload **once**; if it fails again,
  raise `UploadHold` (state already saved) and `main.py` exits non-zero. A
  scheduler (GitLab) re-runs ~24h later; the sha-skip means it resumes only the
  still-pending topics without re-crawling.
- **API quirks:** upload uses the **v2** endpoint, delete **v1** (intentional,
  per the platform). IDs (KB, import strategy) are env-overridable constants; the
  key is `AIGATEWAY_KEY`.

**Deploy note:** `upload_state.json` holds the remote `file_id`s and MUST persist
between weekly runs (CI cache/artifact, or commit it) — otherwise replace can't
find the old file to delete and duplicates accumulate.

**Open:** chunking params are a reasonable first pass but unvalidated against real
retrieval quality; revisit once the RAG bot can be measured.

## 14. Replacing the LLM crawler with crawl4ai (branch `crawler-crawl4ai`)

**Decision:** the LLM layer's remaining job had shrunk to prose capture —
navigation was already deterministic (`subtopics`), structured content came
from `enrich.py`. A spike (`experiments/CRAWL4AI_SPIKE.md`) showed crawl4ai's
plain HTML→markdown conversion captures *everything* deterministically, so
this branch removes the LLM entirely: no `OPENAI_API_KEY`, no MCP subprocess,
no Playwright-in-CI plumbing, ~62 pages in minutes, byte-reproducible.

**What was replaced:**
- `crawl_agent.py`/`prompts.py`/`agent_utils.py`/`mcp_params.py`/
  `webpage_structure.py` → `crawl.py` (crawl4ai fetch, retry×1, timestamps)
- `enrich.py` (~600 lines of BeautifulSoup recovery) → `clean.py`
  (~100 lines of markdown-level cleaning). The whole recover-what-the-LLM-
  dropped problem class disappears when nothing is stochastic.
- `pipeline.py` (JSON→md, PDF export) → gone; the crawl *is* markdown.
  One page = one clean `.md` = one KB file = one chunk.
- old `sites/waiblingen.yaml` (topics + free-text agent instructions) → new
  allowlist format (sections + `subpages` by visible link text; per-topic
  prompt instructions have no consumer anymore).
- `faq/` removed on this branch (kept on `main`).

**Problems met & solutions:**
1. *crawl4ai's `fit_markdown` (PruningContentFilter) prunes backwards* on this
   CMS — its text/link-density heuristic drops headings + download lists and
   keeps cookie-banner prose. → rule-based `clean_markdown`: keep from the
   first heading to the footer/cookie sentinel (one CMS template site-wide).
2. *Marketing h1s* ("Unser bestes Angebot: toptarif-KLIMA plus") broke the
   hierarchy heading. → derive it from the page's own breadcrumb nav (numbered
   list above the h1), keep a differing h1 as `##` below.
3. *Teaser-card links* merge title+tagline ("Fernwärme Bedarfsgerecht und
   günstig"), so exact label matching missed them. → resolve labels exact →
   unique-prefix → unique-substring; ambiguity/miss is a loud report line.
4. *Deep-crawl discovery picked up unwanted pages* (Kunden-Center teaser).
   → allowlist instead of BFS+blocklist: unlisted pages are never crawled.
5. *Some sub-pages live at off-section URLs* (`/abschlag`,
   `/abrechnung-zahlung`, E-Ladestation under `/Netze/`). → output names come
   from the YAML section+label, not the URL; `url:` override for sections
   whose display path isn't the real URL (Störung → `/notfallnummern`).
6. *External pages* (Kundenportal login app, new-waiblingen.de, Planauskunft
   portal) have no crawlable KB content. → excluded from the allowlist;
   Kundenportal ships as hand-written `static/Kundenportal.md`, uploaded like
   any page.
7. *Duplicate content across sections* (Privatkunden Trinkwasser ≡
   Geschäftskunden Wasser; both Fernwärme pages identical): kept — faithful
   to the site; revisit at retrieval time if it causes double hits.

**Upload changes:** one chunk per file, overlap 0 (`chunk_params_for` = file
length). The API hard-caps `max_characters` at **8192** (422 above it —
found by live test), so the ~4 pages over the cap are sent at 8192 with a
**1000-char overlap** and split by the API at structural boundaries; every
other page stays one retrieval unit. The sha-skip also compares stored chunk
params, so a params change re-uploads unchanged content. Added
`prune_stale`: full runs delete remote files whose local page vanished
(renames don't accumulate); partial runs (`--sections`) skip pruning so they
can't wipe the rest of the KB. NB: `upload_state.json` was never persisted
from the old pipeline, so old remote files may need one manual cleanup.

**Monitoring changes:** `run_report` lists every page with ✓/✗/⚠, failure
reason, start time, duration, and clean size; failures and regression/notes
lines come first so Pushover's 1024-char cap never hides them. Regression
baseline is the previous clean file (chars/section counts), measured just
before overwrite.

## 15. Hardening for shared use (code review, 2026-07-08 – 07-16)

Before opening the branch to colleagues, the whole pipeline went through a
structured review (five independent passes — CLAUDE.md compliance, bug scan,
git history, design-doc adherence, comment compliance — each finding then
verified against the code and real crawl output). The report, with per-finding
status, replaced the old LLM-era `docs/code_review.md`.

**Bugs found & fixed:**
1. *Prune could delete healthy KB content.* On a full `--upload` run, the
   upload list was built from this run's *successes*, so a page that merely
   failed to fetch (transient timeout) looked "removed" to `prune_stale` and
   was deleted from the KB. Now pruning runs only on full runs with **zero
   failed pages** (`main.py`: `prune=only is None and not failed`) — a fetch
   failure is not "removed from the YAML".
2. *Hold between delete and upload persisted a dead `file_id`.* `replace_upload`
   deletes the old remote file first; if the subsequent upload failed twice,
   the saved state still pointed at the deleted file (looked live, wasn't).
   The state entry is now dropped immediately after a successful delete;
   regression-tested (`test_hold_after_delete_drops_stale_file_id`).

**Known, accepted risks (documented in `docs/code_review.md`):**
- `upload_state.json` lives in a best-effort GitLab CI *cache*; eviction means
  the next run re-uploads everything and the old remote files can't be
  auto-deleted (ids lost). The 30-day artifact copy is the manual fallback.
- Two latent bugs that don't fire on this site but will matter for a second
  one: `resolve_subpages` keeps only the *first* anchor text per URL, and
  `breadcrumb` can merge two numbered lists separated by blanks.

**Report improvement:** the run report now names the files an upload actually
changed remotely — `new:` (first upload or content changed) and `pruned:`
lines right after the headline, before failures; the unchanged bulk stays a
count. To know those names, the upload now runs *before* the report, so on
`--upload` runs both Pushover messages arrive after the upload finishes. The
separate `upload ok: N new, M unchanged, K pruned` message is unchanged.

**Housekeeping:** stale branch names (`experiment/new-crawl-tool` →
`crawler-crawl4ai`) and wrong DEVLOG §-references fixed across the docs;
misleading comments corrected (sub-page fetches are deliberately sequential;
the h1 hierarchy comes from the page's breadcrumb, not `Section.path`); small
simplifications (`slug` regex, dead `getattr`, duplicate `Path()` wrapping).
`PLAN.md` (personal planning notes) and `api_test/` (upload API scratch
scripts) were untracked/ignored — the repo now contains only the tool.
