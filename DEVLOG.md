# DEVLOG — development history and decisions

The record of this project: what was built, the problems met, the decisions
made, and *why*. It is one of four docs: `README.md` (what it is, setup),
`HANDOVER.md` (how to use every part), `CLAUDE.md` (guidance for Claude Code),
and this file. (`PLAN.md`, referenced in older sections, was a personal
planning doc and is not in the repo.)

| § | Part |
|---|---|
| 1–20 | The crawler: from the LLM crawler to crawl4ai, upload, converters in CI, branch map |
| 21 | PDF converter (`PDFs/pdf2md.py`) |
| 22 | Excel converter (`Excels/xlsx2md.py`) |
| 23 | FAQ bot / RAG layer (`faq_bot/`) |
| 24 | Test set and evaluation (`evaluation/`) |
| 25 | Code review of the crawl4ai pipeline (2026-07) and its open items |
| 26 | The crawl4ai spike that replaced the LLM crawler |
| 27 | November 2026: chunking, headers, reranking, the Excel check, this doc merge |

§21–26 were separate files until 2026-11-09 (`PDFs/README.md`,
`Excels/README.md`, `faq_bot/README.md`, `evaluation/README.md`,
`docs/code_review.md`, `experiments/CRAWL4AI_SPIKE.md`) and were merged here
unchanged except for heading levels and `§` numbers. **Commands in them are a
historical record; the current way to run anything is in `HANDOVER.md`.**

---

## 1. Where we started

A working but rigid crawler hard-wired to one site (Stadtwerke Waiblingen): a
355-line `crawler/config.py` of Python dicts, no CLI, output schema + JSON→Markdown
conversion, and an OpenAI Agents SDK agent driving a Playwright browser over MCP.
Goal: turn it into a **config-driven, reliable, unattended** tool.

---

## 2. The config-driven refactor (Phases 0–8)

Done in small, reviewed steps (full status in `PLAN.md`):

- **Phase 0 — tooling:** `uv` + `pyproject.toml` + committed `uv.lock`.
- **Phase 1 — config:** crawl targets moved to `sites/waiblingen.yaml`; `crawler/config.py`
  became `Topic`/`Site` Pydantic models + `load_site()` (validate at the boundary,
  fail loudly). 31 topics migrated faithfully.
- **Phase 2 — prompts:** `get_user_prompt_structured_output(topic, root_url)` +
  `build_navigation` (supports explicit `url` and click-`path`).
- **Phase 3 — CLI:** `crawler/main.py` argparse (`--config`, `--topics`); bad input fails
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
- **`crawler/monitor.py` + Pushover:** alert on a topic failure, alert on a regression
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
- **`crawler/monitor.py`** owns alerting.

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
re-run.) So `crawler/main.py` now re-launches a failed topic instead of just recording
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

## 13. Knowledge-base upload stage (`crawler/uploader.py`, `--upload`)

Downstream of the crawl: push each topic's `outputs/<topic>.md` to the
`aigateway.eu` knowledge base (the RAG/FAQ bot's source). Opt-in via `--upload`;
the weekly deploy does crawl → upload in one run. `api_test/` remains a manual
sandbox (untracked); `crawler/uploader.py` is the canonical, tested logic.

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
  raise `UploadHold` (state already saved) and `crawler/main.py` exits non-zero. A
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
from `enrich.py`. A spike (§26) showed crawl4ai's
plain HTML→markdown conversion captures *everything* deterministically, so
this branch removes the LLM entirely: no `OPENAI_API_KEY`, no MCP subprocess,
no Playwright-in-CI plumbing, ~62 pages in minutes, byte-reproducible.

**What was replaced:**
- `crawl_agent.py`/`prompts.py`/`agent_utils.py`/`mcp_params.py`/
  `webpage_structure.py` → `crawler/crawl.py` (crawl4ai fetch, retry×1, timestamps)
- `enrich.py` (~600 lines of BeautifulSoup recovery) → `crawler/clean.py`
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
status, replaced the old LLM-era DEVLOG §25.

**Bugs found & fixed:**
1. *Prune could delete healthy KB content.* On a full `--upload` run, the
   upload list was built from this run's *successes*, so a page that merely
   failed to fetch (transient timeout) looked "removed" to `prune_stale` and
   was deleted from the KB. Now pruning runs only on full runs with **zero
   failed pages** (`crawler/main.py`: `prune=only is None and not failed`) — a fetch
   failure is not "removed from the YAML".
2. *Hold between delete and upload persisted a dead `file_id`.* `replace_upload`
   deletes the old remote file first; if the subsequent upload failed twice,
   the saved state still pointed at the deleted file (looked live, wasn't).
   The state entry is now dropped immediately after a successful delete;
   regression-tested (`test_hold_after_delete_drops_stale_file_id`).

**Known, accepted risks (documented in DEVLOG §25):**
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

## 16. Stateless upload: reconcile against the live KB (2026-07-26)

**The §15 "accepted risk" fired.** A `GET .../v2/knowledgebases/{id}/files`
(the API's list endpoint) showed the KB holding **128 files for 63 pages** —
every page duplicated 2×, two of them 3×. Root cause exactly as flagged in
§15: `upload_state.json` was persisted only through GitLab's *cache*, which is
best-effort. On a cache miss the uploader started with empty state, so
`replace_upload` found no old `file_id`, skipped the delete, and uploaded a
fresh copy while `prune_stale` had nothing to compare against — orphaning the
previous copies. Every cache-miss run added a full duplicate set. The local
`upload_state.json` knew only 2 file_ids, so the tool could never clean up the
rest.

**One-time cleanup.** A throwaway script listed all files, grouped by
filename, kept the newest (`created`) of each, and deleted the other 65 (with
safety invariants: never delete the sole copy of a name; kept count == distinct
names). KB verified back to 63 files, one per page.

**Redesign — the KB is the source of truth, reconciled each run.** The
uploader is now **stateless**; `upload_state.json` and the sha/params skip are
gone:
- `list_remote_files()` — `GET` (paginated, 0-indexed `size`) → `{filename:
  [file_id, …]}`.
- `replace_upload(page, remote)` — delete *every* remote copy of that filename,
  then upload the fresh `.md`. This also cleans up any residual duplicates for
  free.
- `prune_stale(pages, remote)` — delete remote filenames not produced locally
  (still gated to full runs with zero failures, per §15 bug 1).
- `.gitlab-ci.yml` — dropped the `cache:` block and the state artifact; a fresh
  runner is now always correct.
The trade-off: no content-diff skip, so every page re-uploads each run. At ~63
pages / weekly that is cheap, and the payoff is self-healing — a lost cache, a
manual KB edit, or an interrupted prior run all get corrected on the next run.
The run report now shows an `uploaded N, pruned M` count (plus the individual
`pruned:` names) instead of a `new:` line per file, so it never floods past
Pushover's 1024-char cap. Tests rewritten around an in-memory fake KB
(list/post/delete) covering replace-by-name, duplicate cleanup, prune, and
pagination. This closes the §15 durability risk (and finding 3 of
DEVLOG §25).

## 17. Adding PDF documents to the KB (`PDFs/`, 2026-08-13)

Four Bäder PDFs (two tariff sheets, the reduced-price explanation, the
guest-WLAN terms) had to join the knowledge base. **No pipeline code changed:**
`PDFs/pdf2md.py` converts them into `static/`, which `main.py:copy_static()`
already copies into `outputs/clean/` and uploads like any crawled page — the
same mechanism `Kundenportal.md` uses. Five files result (the WLAN terms split
in two, below); all six static pages are a single retrieval unit.

**Full write-up — including every problem met and how it was solved — lives in
§21.** The headlines:

- **`pymupdf4llm` corrupts the prices.** Its layout extension runs Tesseract OCR
  over these *text-layer* PDFs and turned `13,80€` into `1380€` while splitting
  columns mid-word. Replaced with **`pdfplumber`** + its `lines` table strategy
  (reads the table's own ruling lines): correct rows, correct prices. Also MIT
  rather than PyMuPDF's AGPL. Do not swap it back.
- **`pdfplumber` is not a project dependency** — one-off job, so
  `uv run --with pdfplumber python PDFs/pdf2md.py` keeps `pyproject.toml` minimal.
- **Writing to `outputs/clean/` would have broken the upload.** The upload list
  is *crawled pages + `static/*.md`* only, so such a file is never uploaded — and
  `prune_stale` would delete it remotely if it ever arrived. Hence `static/`.
- **Umlaut filenames hit the NFC/NFD trap.** macOS's `glob` returns decomposed
  names (`a` + U+0308), where `clean.slug` turns the combining mark into `_`
  (`Bäder` → `Ba_der`). Since the KB is keyed *by filename*, a name composing
  differently on the runner than on macOS would orphan the remote file and
  duplicate it — the §16 failure class again. Fixed by transliterating
  (`ä`→`ae`, `ß`→`ss`) so the filename is ASCII on every platform, matching the
  site's own `Privatkunden/Baeder`. Titles inside the files keep real umlauts;
  existing crawled filenames are unaffected (their names come from YAML content,
  reliably NFC).
- **Justified text needed hyphen repair** (`WLAN - Zugangs`, `Gäste- WLAN`) and
  German line-break dehyphenation (`ver-`+`antwortlichen` joins; `Gäste-WLAN-`+
  `Zugangs` keeps the hyphen). Scoped to prose, ASCII hyphens only — so table
  ranges (`6 - 16 Jahre`), en-dash sentence breaks, and the h1's ` - `
  separators are untouched.
- **The WLAN terms (9959 chars) exceed the 8192 cap**, so rather than let the API
  cut mid-section — leaving the tail chunk with no title line — the document is
  split at its own `##` boundaries into `Teil 1 von 2` (sections 1–8) and
  `Teil 2 von 2` (9–14), each carrying its own h1.

**Seasonal maintenance:** these sheets are dated (`Tarifübersicht … 2026`), so
drop the replacement in `PDFs/`, re-run the script, **read the output** (a layout
change degrades it silently and still exits 0), and use a **full** `--upload` —
`prune_stale` is what removes the superseded remote file when `…_2026` becomes
`…_2027`.

## 18. Adding the colleagues' Excel knowledge base (`Excels/`, 2026-08-18)

Colleagues maintain a SharePoint spreadsheet titled *"Zusätzliche Informationen /
Wissensdatenbank für den FAQ-Bot"* — 18 hand-written topics covering ground the
website does not (Einspeiseanlagen billing, Stammdaten changes, SEPA, …). The
customer supplied a final version, so like `PDFs/` this is a **one-off
conversion, not a sync**: `Excels/xlsx2md.py` writes into `static/` and the
existing `copy_static()` + `--upload` path does the rest. No pipeline code
changed. Full write-up in **§22**; the headlines:

- **Take the raw `.xlsx`, never an export.** SharePoint's *Export* → PDF/CSV both
  produced unusable output: PDF is paginated for print and discards cell
  structure, CSV takes one sheet and (German locale) uses `;` delimiters with
  comma decimals. *Download* — a different menu — gives the real `.xlsx`, which
  `openpyxl` reads directly. Source-quality ranking: **`.xlsx` → `.csv` →
  `.pdf`**. Note this is the exact inverse of asking a supplier for "a PDF
  instead", which was the initial instinct.
- **One file per topic group (8 files), not one file and not one per row.** Since
  the uploader makes each file exactly one chunk, a single 6.5k file would put
  all 18 unrelated topics into one retrieval unit (a Freibad question matching a
  mostly-Einspeiseanlagen chunk). Per-group files match the granularity of the
  crawled pages and keep related topics together. h1 root is `Wissensdatenbank`,
  marking these as supplementary knowledge rather than crawled site content.
- **Layout is located, not assumed.** The table starts at B4 with a title row,
  spacer rows and six trailing empties, so the header row is found by its labels
  (`Thema / Kategorie`, `Inhalt / Wissen`) and a missing header is a loud
  `SystemExit`, not empty output. Half-filled rows are warned about and skipped.
- **Hand-typing artifacts:** in-cell line breaks are meaningful (kept as
  paragraph breaks), space runs collapsed, and the `<Gruppe> – <Unterthema>`
  separator is an **en dash** — the split accepts a hyphen too but only when
  spaced, so compounds like `Gäste-WLAN` are never cut.
- **Personal-data check before upload:** none present (no IBANs, phone numbers,
  customer names or identifier digits); the only addresses are company mailboxes
  that are part of the answers. Worth repeating on any revised file, since this
  content comes from the customer and feeds a customer-facing bot.
- **ASCII filenames** via the same transliteration as §17, for the same reason
  (the KB is keyed by filename; NFC/NFD drift would orphan and duplicate).
  `ascii_name` is duplicated in both one-off folders rather than shared, keeping
  each self-contained; only `clean.slug`/`clean.strip_links` are imported.

`static/` now holds **14 pages** (Kundenportal + 5 from `PDFs/` + these 8), every
one a single retrieval unit.

---

## 19. Source conversion moved into the weekly automation (2026-08-20)

**Why.** The project is being handed over to colleagues who do not use
GitLab/git. The KB-editing story had a hole: `PDFs/` and `Excels/` are the
*sources* for 13 of the 14 `static/` pages, but converting them (§17, §18) was a
command someone typed locally. So a colleague could upload a reissued tariff PDF
perfectly well and **nothing would ever happen** — the KB would keep serving last
year's prices. Steps 1 (get the file into the repo, via GitLab's *Upload file*
button) and 3 (weekly upload) already needed no git; only step 2 did.

**What changed.** `.gitlab-ci.yml` runs both converters before `crawler/main.py`:

```
uv run python PDFs/pdf2md.py
uv run python Excels/xlsx2md.py
uv run python crawler/main.py … --upload
```

Replacing a PDF or the Excel in the repo is now the entire update procedure. The
upload path is untouched — the converters write into `static/` exactly as before,
`copy_static()` picks them up, and the uploader reconciles as always.

**Considered and rejected: untracking `static/`** so colleagues would edit the
`.md` in the KB UI instead. Three problems. It breaks the invariant that licenses
`prune_stale` — *local clean output is the complete KB contents* — so a
hand-added KB file becomes indistinguishable from a page renamed in the YAML, and
the next clean full run deletes it. It loses the git history of customer-facing
answers. And for these two sources it does not even help: one changed Excel row
regenerates whichever of 8 group files it belongs to (renaming the file if the
group name changed), and nobody will hand-transcribe a priced table — §17.2.1 is
what naive extraction does to `13,80€`. Letting colleagues upload the raw
`.pdf`/`.xlsx` to the KB directly fails the same way, plus one-giant-chunk
retrieval.

**Three things automation required.**

- **`_clean_generated` in both converters** — each deletes its own previous
  output (`static/Privatkunden_Baeder_*.md`, `static/Wissensdatenbank_*.md`)
  before regenerating, scoped to the prefix it owns so `Kundenportal.md` and the
  other converter are untouched. This is what makes the *yearly rename* work:
  `prune_stale` only removes remote files no longer produced **locally**, so a
  leftover `…_2026.md` in `static/` is not stale from its point of view and would
  be uploaded for ever alongside `…_2027.md`. Retires the §17.2.9 chore.
- **A degradation guard** — §21.4 used to say *"read the generated
  markdown, do not trust the exit code"*, which is no longer possible on a
  scheduled run. The specific risk is a reissued tariff sheet losing its ruling
  lines: `find_tables` returns nothing, the prices arrive as loose prose, exit 0.
  Now a document whose title matches `TABLE_REQUIRED` (`Tarif`) and whose output
  has no markdown table exits 1. Note coverage-style checks cannot catch this —
  the text is all still there, only its structure is gone.
- **Convert-then-write ordering** — both scripts convert everything into memory
  before touching `static/`, and run *before* the crawl. A failure therefore
  fails the job with `static/` intact and nothing uploaded; without this, a
  half-emptied `static/` on a clean full run would have `prune_stale` delete the
  rest of these documents from the KB.

**`pdfplumber`/`openpyxl` are now locked deps** in a `convert` dependency group
(`uv sync --group convert`), not `uv run --with` per invocation. The original
rationale — one-time job, keep `pyproject.toml` minimal — died with the one-time
part: an unpinned extraction library that changes behaviour between weekly runs
would silently rewrite prices. The group keeps them out of the crawler's own
runtime deps.

**Verified.** Both converters re-run: output **byte-identical** to the committed
`static/*.md` (all 13 files, char counts matching §17/§18). Guard exercised by
forcing a table-less conversion — exits 1, `static/` unchanged at 14 files.
`tests/test_convert.py` covers `has_table`, `TABLE_REQUIRED` (including the
`…2027` rename and the deliberate `Eintrittspreisen` non-match) and
`_clean_generated`'s prefix scoping; suite 33 → 42 tests.

**`HANDOVER.md`** documents the browser-only procedures for a non-git colleague:
replacing a PDF, replacing the Excel, editing a hand-written page, the *Run
pipeline* button (a push does **not** trigger the schedule-gated job), and a
symptom table for the failure modes they can act on. Its central promise is the
one the uploader already guarantees: the KB is never left half-updated, and
deletion only happens on a complete run with zero failures.

---

## 20. Branch map, and the RAG layer on `main` (2026-09-14 – 11-01)

**Branches.** On 2026-09-14 `crawler-crawl4ai` was **renamed
`crawler-sw-waiblingen`**: it is the production Waiblingen crawler. The name
was freed on purpose — a GitLab schedule targets a *branch name*, so reusing it
for experiments would have pointed the production crawl at experimental code.

| Branch | What it is |
|---|---|
| `crawler-sw-waiblingen` | production crawler + upload; pushed to **GitLab `main`**, where the weekly schedule runs |
| `main` (GitHub) | copy of the crawler as of 2026-09-12 **plus** the personal RAG layer (`faq_bot/`, `evaluation/`) |
| `crawl4ai-crawler` | general multi-site crawler, started from `crawler-ahk` |
| `crawler-ahk` | AHK Heidekreis Abfall-ABC adaptation |
| `crawler-llm-agent` | the old gpt-5-mini + Playwright MCP crawler |
| `restructure-rag` | older RAG restructure, superseded by `main` |

GitHub's `main` and GitLab's `main` are **different branches with different
histories** — never push one onto the other. The route to production is
GitHub `crawler-sw-waiblingen` → work laptop → GitLab `main` (via a Merge
Request the first time). Crawler fixes go on `crawler-sw-waiblingen`; `main`'s
copy is not kept in sync.

**RAG layer.** Built 2026-09-03 – 09-16; documented in §23 (FAQ bot) and §24
(evaluation). Later changes: both Gradio UIs launch with
`share=True` (public link); `ingest.py` splits any page above the 8,192-token
embedding cap on its headings instead of failing.

**2026-11-01: `gpt-4.1-nano` retired by OpenAI.** It is the model of every LLM
step in the RAG layer (chunking, rewrite, rerank, answer). `ingest.py --method
llm` showed it as a silent hang, because `process_document`'s `@retry` has no
`stop`. Found by calling the undecorated function (`__wrapped__`). Fix and
working replacement models: §23.4.9. The crawler is unaffected
— it calls no LLM.

---

## 21. PDF → knowledge-base markdown

This folder holds four Stadtwerke Waiblingen **Bäder** PDFs and the script that
converts them to knowledge-base markdown, `pdf2md.py`. The crawler itself is
untouched by this work — `pdf2md.py` only writes into `static/`, which the
pipeline already uploads.

Written 2026-08-13 as a one-off conversion; **since 2026-08-20 it runs in CI
before every weekly crawl**, so replacing a PDF in this folder is the whole
update procedure (§21.4, and `HANDOVER.md` for the browser-only version). Companion
to `DEVLOG.md` (the crawler's own history) — this file documents only the PDF
side.

---

### 21.1 What was done

Four PDFs — the downloads offered on the `Privatkunden/Baeder` page — had to
join the knowledge base alongside the ~62 crawled pages:

| Source PDF | Pages | Text layer | Images |
|---|---|---|---|
| `Tarifübersicht Freibäder 2026.pdf` | 1 | 887 chars | 1 |
| `Tarifübersicht Hallenbad 2026.pdf` | 1 | 897 chars | 1 |
| `Erläuterungen zu den ermäßigten Eintrittspreisen.pdf` | 1 | 1295 chars | 0 |
| `Nutzungsbedingungen Gäste-WLAN Bäder.pdf` | 4 | 10111 chars | 5 |

`pdf2md.py` converts each one into `static/`, from where the existing pipeline
picks it up with **no code changes**: `main.py:copy_static()` copies
`static/*.md` into `outputs/clean/`, and `--upload` pushes them like any crawled
page. Output:

| Generated file (in `static/`) | Chars | KB chunks |
|---|---|---|
| `Privatkunden_Baeder_Tarifuebersicht_Freibaeder_2026.md` | 997 | 1 |
| `Privatkunden_Baeder_Tarifuebersicht_Hallenbad_2026.md` | 1007 | 1 |
| `Privatkunden_Baeder_Erlaeuterungen_zu_den_ermaessigten_Eintrittspreisen.md` | 1323 | 1 |
| `Privatkunden_Baeder_Nutzungsbedingungen_Gaeste-WLAN_Baeder_Teil_1.md` | 6206 | 1 |
| `Privatkunden_Baeder_Nutzungsbedingungen_Gaeste-WLAN_Baeder_Teil_2.md` | 3833 | 1 |

Every file is a single retrieval unit — the API splits none of them.

#### How to run it

```bash
uv run python PDFs/pdf2md.py
```

Runs automatically in CI before every weekly crawl (`.gitlab-ci.yml`) — see §21.4.
Run it locally only to check a conversion by hand.

`pdfplumber` is a **locked dependency in the `convert` group**, so `uv sync
--group convert` is needed once (plain `uv sync` omits it, keeping the crawler's
own runtime deps as minimal as its conventions demand). It was originally pulled
in per-invocation with `uv run --with`, which stopped being right when this
became a weekly automated job: an unpinned extraction library that changes
behaviour between runs would silently rewrite prices in a customer-facing bot.

#### Output shape

Each generated file follows the same conventions as a crawled page (see
`clean.clean_markdown`), so the knowledge base sees one consistent corpus:

- **h1 = site hierarchy** — `# Privatkunden - Bäder - Tarifübersicht Freibäder 2026`
- **`##` for sections**, tables as GitHub-flavoured markdown
- **no hyperlinks** (`clean.strip_links` is reused)

Headings are detected by **type size**, which is how these documents encode
them: the most common size on the page is body text, anything larger — or bold
at body size — is a heading, anything smaller is page furniture (`Seite 1 von
4`) and is dropped. The document's own largest heading is discarded because it
duplicates the h1 we build from the hierarchy.

| Document | Body | Headings | Furniture |
|---|---|---|---|
| Erläuterungen | 16pt | 31pt bold (title), 18pt bold (3 sections) | — |
| Nutzungsbedingungen | 11pt | 14pt bold (title), 11pt bold (14 sections) | 10pt (`Seite x von 4`) |

---

### 21.2 Problems met, and how they were solved

Every one of these was found by inspecting real output against the source PDFs,
not by reasoning ahead of time.

#### 21.2.1 `pymupdf4llm` silently corrupted the prices

**The obvious first choice was wrong.** The one-line
`pymupdf4llm.to_markdown(path)` pulled in a layout extension
(`pymupdf-layout`, `onnxruntime`) which ran **Tesseract OCR** over the pages and
produced:

```
|**Familie 2 Erw. + Kinder**|6 - 16 Jahre|1380€|      <- should be 13,80€
|**Erwachsener**<br>ab 17 Jahre|n|5,00€|              <- column split mid-word
```

A price losing its decimal comma, in a file destined for a customer-facing
FAQ bot, is the worst possible failure mode — it looks plausible and is wrong.

**Diagnosis:** all four PDFs were checked with plain PyMuPDF and *every one has
a real text layer* (see the table in §21.1). OCR was never needed; the extension
chose it anyway and mangled the result.

**Solution:** use **`pdfplumber`** with its `lines` table strategy, which reads
the table's own ruling lines rather than guessing from text positions:

```
| Familie 2 Erw. + Kinder 6 - 16 Jahre | 13,80€ |
```

Correct prices, and the age qualifier correctly merged into its row. Secondary
benefits: `pdfplumber` is **MIT**-licensed, where PyMuPDF is **AGPL-3.0**
(commercial licence otherwise) — worth avoiding in a company project even for a
local script.

> **Do not swap `pymupdf4llm` back in.** This is the single most important note
> in this document.

#### 21.2.2 Writing to `outputs/clean/` would have broken the upload

The first plan was for the script to write straight into `outputs/clean/`, which
looks right — that is where clean pages live. It would have failed three ways:

1. `main.py:103` builds the upload list as *crawled pages + `static/*.md` names
   only*. A file appearing in `outputs/clean/` from nowhere is **never
   uploaded**.
2. On a full `--upload` run, `uploader.prune_stale` deletes every remote
   filename **not** in that list — so had those files ever reached the KB by
   another route, the next run would **delete them**.
3. `outputs/` is gitignored and rebuilt each run, so a fresh clone or a CI
   runner would not have them at all.

**Solution:** write to **`static/`**, the mechanism that already exists for
exactly this (it is how `Kundenportal.md` ships). `crawler/main.py` still performs the
`outputs/clean/` step itself, so the end result is identical — via the path that
survives a CI run.

#### 21.2.3 The page border was detected as a table containing the whole page

`find_tables()` returned **two** tables for each tariff sheet: the real one, and
a bogus outer "table" formed by the page's border box, holding the entire page
as one giant cell. Both were emitted, so the tariff sheet appeared twice — once
as an unreadable single-row blob, once correctly.

**Solution:** `_encloses()` — any detected table whose bounding box contains
another table's box is dropped in favour of the inner one. A general rule, not a
per-file patch.

#### 21.2.4 Umlauts in filenames: the NFC / NFD trap

The generated filenames came out mangled:

```
Privatkunden_Bäder_Erla_uterungen_zu_den_erma_ßigten_Eintrittspreisen.md
                        ^^^                ^^^
```

**Cause.** Unicode can encode `ä` two ways:

| Form | Code points | UTF-8 bytes | `len()` |
|---|---|---|---|
| **NFC** (composed) | `ä` = U+00E4 | `C3 A4` | 1 |
| **NFD** (decomposed) | `a` = U+0061 **+** U+0308 combining diaeresis | `61 CC 88` | 2 |

Both render identically on screen; the bytes differ. macOS hands back the
**decomposed** form from `os.listdir`/`glob` (verified — while `ls` in the shell
showed it composed, which is what made this confusing). In NFD, the combining
diaeresis is a standalone *Mark*, which `clean.slug`'s `[^\w\-]+` regex does not
treat as a word character — so it was replaced with `_`.

The same trap bit the cleanup: an `rm` with hand-typed NFC filenames failed to
match the NFD files actually on disk.

**Why it matters beyond cosmetics:** the knowledge base is keyed **by filename**
and the uploader deletes/replaces by it (`uploader.replace_upload`). A name that
composes differently on the GitLab runner than on macOS would orphan the old
remote file and upload a duplicate — the same class of bug as DEVLOG §16.

**Solution:** `ascii_name()` transliterates the German way — `ä`→`ae`, `ö`→`oe`,
`ü`→`ue`, `ß`→`ss` — after composing to NFC, then strips any remaining combining
marks. An ASCII filename cannot depend on normalisation form on any platform.
It also matches the site's own spelling (`sites/waiblingen.yaml` uses
`path: Privatkunden/Baeder`). The **document title inside the file keeps its
real umlauts** — this applies to filenames only.

> Existing crawled pages still use umlaut filenames. They are safe: their names
> come from YAML *file content*, which is consistently NFC. Renaming them would
> churn the entire KB for no benefit.

#### 21.2.5 Hyphen spacing in the justified WLAN document

The Nutzungsbedingungen came out with broken compounds — 20 occurrences:

```
WLAN - Zugangs      störungs - und      E - Mails
Gäste- WLAN         Port - Sperrungen   MAC - Adressen
```

**Diagnosis.** This PDF is justified, which widens letter gaps, and
`pdfplumber` reports **double spaces** around those hyphens where PyMuPDF reads
them correctly. `x_tolerance` was tested from 3 down to 0.5 with no change, so
the spacing is in the PDF's glyph stream, not a tolerance artefact.

(An earlier probe regex looked for a *single* space and reported "clean" — the
artifacts were double-spaced. Worth remembering when checking for this class of
problem.)

**Solution:** `_repair_hyphens()`, with two safety properties that make it safe
rather than a blunt find-and-replace:

- It touches **only ASCII hyphens**, so a real en-dash sentence break (`–`, used
  throughout the Erläuterungen document) is untouched.
- It is applied to **body prose only** — never to table cells, which
  legitimately contain ranges like `6 - 16 Jahre`, and never to the h1, whose
  ` - ` hierarchy separators would otherwise be glued shut.

German grammar is respected: a compound followed by a conjunction keeps its
space (`störungs- und`), while the rest close up (`E-Mails`, `Gäste-WLAN`).

#### 21.2.6 German line-break hyphenation

Words split across lines needed two different treatments, distinguished by the
case of the continuation:

| Source | Rule | Result |
|---|---|---|
| `ver-` + `antwortlichen` | lowercase → drop the hyphen | `verantwortlichen` |
| `Gäste-WLAN-` + `Zugangs` | uppercase → **keep** the hyphen | `Gäste-WLAN-Zugangs` |

A capital continuation means a genuine German compound hyphen, not syllable
hyphenation.

#### 21.2.7 An empty trailing heading

The tariff sheets set their validity period (`Saison 2026/2027`) in heading type
at the foot of the page, so it became a `##` section with no content under it.
`_demote_empty_headings()` turns any heading with nothing beneath it back into
plain text — the information is kept, the empty section is not.

#### 21.2.8 The WLAN document exceeded the API's chunk cap

At 9959 chars it is over the API's hard `MAX_CHUNK = 8192` limit, so the
uploader would have let the **API** split it. Two problems with that: the cut
lands mid-section, and **the tail chunk carries no title line** — a retrieved
chunk with no indication of which document it came from.

**Solution:** `split_for_upload()` splits at the document's own `##` boundaries
instead, balanced across parts, each part getting its own h1:

```
Teil 1 von 2  →  sections 1–8   (6206 chars)
Teil 2 von 2  →  sections 9–14  (3833 chars)
```

All 14 sections intact, both parts a single retrieval unit, both
self-identifying. The function is general: it computes how many parts are
needed and logs a warning if a single section were ever too large to fit.

#### 21.2.9 Stale output accumulating in `static/`

Successive runs left **8** generated files in `static/` (two earlier naming
generations), all of which `copy_static()` would have uploaded — three
differently-named copies of each document in the knowledge base. Cleaned up
before the final run.

Worth knowing when re-running: the script **overwrites** its own current output
but cannot know about files from a *previous naming scheme*. If a document is
ever renamed, delete the old `static/Privatkunden_Baeder_*.md` first. (On a full
`--upload` run `prune_stale` would eventually remove the orphans remotely, but
only after they had already been uploaded once.)

---

### 21.3 What was verified

- **Prices** cross-checked against the PDFs' own text layer — `13,80€`,
  `15,80€`, `218,00€` etc. all correct, age qualifiers on the right rows.
- **No hyphen artifacts** remain in any of the five outputs; table ranges
  (`6 - 16`) deliberately untouched.
- **Section count preserved**: 14 in the source PDF, 8 + 6 across the two parts.
- **Pipeline integration**: `main.py:copy_static()` returns 6 pages
  (`Kundenportal` + these 5), and `uploader.chunk_params_for` confirms every one
  is a single chunk.
- **Nothing tracked was lost**: `static/Kundenportal.md` is unmodified.

---

### 21.4 Maintaining this

**These documents are seasonal** (`Tarifübersicht … 2026`, `Saison 2026/2027`),
so they will be reissued — which is why this script is **no longer a one-off run
by hand**. `.gitlab-ci.yml` runs it before every weekly crawl, so the whole
maintenance procedure is:

1. Drop the new PDF in this folder, delete the superseded one — via GitLab's
   **Upload file** button in a browser is fine, and needs no git knowledge
   (`HANDOVER.md`).

That is all. The next weekly run converts it, uploads it, and removes the old
knowledge-base file. No local run, no `static/*.md` to commit, no `--upload` to
remember.

Two properties make that safe to do unattended:

- **`_clean_generated` deletes this script's own previous output** (everything
  matching `static/Privatkunden_Baeder_*.md`) before regenerating. A reissue
  renames the file (`…_2026` → `…_2027`), and a stale local file would otherwise
  keep being uploaded for ever — `prune_stale` only removes remote files that are
  *no longer produced locally*, so a leftover in `static/` is not "stale" from its
  point of view. This retires §21.2.9 and the old "delete the superseded file first"
  chore.
- **Conversion runs before the crawl and fails the job**, so a bad conversion
  never reaches the upload step. `static/` is only touched after every PDF has
  converted successfully, so a mid-way failure cannot leave a short set behind
  for `prune_stale` to act on.

#### The degradation guard

The limitation below used to be answered by "read the generated markdown". Since
nobody does that on a scheduled run, it is now enforced: a document whose title
matches `TABLE_REQUIRED` (`Tarif`, i.e. the tariff sheets) and whose output
contains no markdown table **exits 1 with a loud error**. Prices arriving as
loose prose is the one failure that looks plausible and is wrong, so it stops the
pipeline; last week's correct version stays in the knowledge base until someone
looks. Covered by `tests/test_convert.py`.

#### Known limitations

- **Other layout changes still degrade output silently.** The guard covers the
  tariff sheets' tables specifically. A prose document that loses its heading
  structure comes out flat and exits 0.
- **Heading detection assumes size encodes structure.** A PDF that marks
  headings only by position or colour would come out flat.
- **`_repair_hyphens` is tuned to these documents.** Its safety rests on them
  using `–` for sentence dashes and ASCII `-` only inside compounds. A document
  that uses ` - ` as a real dash in prose would have it glued shut.
- **Partial unit tests.** `has_table` and `_clean_generated` are covered
  (`tests/test_convert.py`). The remaining pure helpers (`ascii_name`,
  `_repair_hyphens`, `_demote_empty_headings`, `split_for_upload`) are testable
  and not yet tested.

---

## 22. Excel → knowledge-base markdown

This folder holds the colleagues' knowledge-base spreadsheet and the script that
converts it, `xlsx2md.py`. Like `PDFs/`, the crawler itself is untouched — the
script only writes into `static/`, which the pipeline already uploads.

Written 2026-08-18 as a one-off conversion; **since 2026-08-20 it runs in CI
before every weekly crawl**, so replacing `Knowledge Base.xlsx` is the whole
update procedure (§22.5, and `HANDOVER.md` for the browser-only version). Companion
to `DEVLOG.md` (the crawler's own history) and to DEVLOG §21, which
documents the same pattern for PDF sources.

---

### 22.1 What was done

`Knowledge Base.xlsx` comes from a SharePoint file that colleagues maintain by
hand. Its own title says what it is for:

> Zusätzliche Informationen / Wissensdatenbank für den FAQ-Bot

It is a two-column list — **`Thema / Kategorie`** and **`Inhalt / Wissen`** —
of 18 topics written specifically as extra material for the bot, covering ground
the website does not (Einspeiseanlagen billing, Stammdaten changes, SEPA, and
so on). The customer supplied it as final, so this is a **one-off** conversion,
not a sync.

Each topic is named `<Gruppe> – <Unterthema>`, so the rows group into subject
files:

| Generated file (in `static/`) | Topics | Chars |
|---|---|---|
| `Wissensdatenbank_Einspeiseanlagen.md` | 8 | 2376 |
| `Wissensdatenbank_Gasversorgung.md` | 2 | 1270 |
| `Wissensdatenbank_Stammdaten.md` | 3 | 919 |
| `Wissensdatenbank_Lieferantenwechsel.md` | 1 | 762 |
| `Wissensdatenbank_SEPA.md` | 1 | 397 |
| `Wissensdatenbank_Tarifwechsel.md` | 1 | 353 |
| `Wissensdatenbank_Elektromobilitaet.md` | 1 | 307 |
| `Wissensdatenbank_Freibad_Waiblingen.md` | 1 | 223 |

Every file is a single retrieval unit — the API splits none of them.

#### How to run it

```bash
uv run python Excels/xlsx2md.py
```

Runs automatically in CI before every weekly crawl (`.gitlab-ci.yml`) — see §22.5.
Run it locally only to check a conversion by hand.

`openpyxl` is a **locked dependency in the `convert` group**, so `uv sync --group
convert` is needed once (plain `uv sync` omits it, keeping the crawler's own
runtime deps minimal). It was originally pulled in per-invocation with `uv run
--with`; pinning it matters now that a scheduled job depends on it.

#### Output shape

```markdown
# Wissensdatenbank - Einspeiseanlagen

## Abrechnungszeitraum

Abrechnungen für Einspeiseanlagen werden in der Regel zwischen Januar und …

## Restforderungen

…
```

The group prefix is stripped from each `##` because the h1 already carries it —
`## Abrechnungszeitraum`, not `## Einspeiseanlagen – Abrechnungszeitraum`.

---

### 22.2 Decisions, and why

#### 22.2.1 One file per group — not one file, and not one per row

The uploader makes **each file exactly one retrieval chunk**
(`uploader.chunk_params_for`). That turns file granularity into a retrieval
decision:

- **One file for everything** (6.5k chars, fits the cap) would put all 18
  unrelated topics into a single chunk, so a question about Freibad opening hours
  would match a chunk that is overwhelmingly about Einspeiseanlagen. Rejected.
- **One file per row** (18 files) gives the tightest possible match, but strands
  related topics in separate chunks — an Einspeiseanlagen billing question often
  needs `Abrechnungszeitraum`, `Zählerstand` *and* `Restforderungen` together.
- **One file per group** (chosen) matches the granularity of the existing 62
  crawled chunks, where one page/chunk likewise covers several related
  sub-topics, and keeps siblings retrievable together.

#### 22.2.2 `Wissensdatenbank` as the h1 root

The crawled pages use roots from the site's own navigation (`Privatkunden`,
`Netze`). These rows are the colleagues' supplementary knowledge rather than
website content, and some of it is not strictly Privatkunden material, so they
carry their own root. This also keeps the bot's sources distinguishable when
reviewing what it answered from.

#### 22.2.3 Output goes to `static/`, never `outputs/clean/`

Same reasoning as §21.2.2: `crawler/main.py` builds the upload list from
*crawled pages + `static/*.md` only*, `outputs/` is gitignored and rebuilt each
run, and `prune_stale` deletes remote files not on that list. `static/` is the
supported route (it is how `Kundenportal.md` ships).

#### 22.2.4 ASCII filenames

`ascii_name()` transliterates the German way (`ä`→`ae`, `ß`→`ss`), so
`Elektromobilität` becomes `Wissensdatenbank_Elektromobilitaet.md`. The
knowledge base is keyed **by filename** and the uploader deletes/replaces by it,
so a name whose Unicode normalisation form differs between macOS and the CI
runner would orphan the old remote file and upload a duplicate. Full explanation
of the NFC/NFD trap — which cost real debugging time on the PDFs — is in
§21.2.4.

The function is **deliberately duplicated** in both folders rather than shared,
to keep each one-off conversion self-contained. Only `clean.slug` and
`clean.strip_links` are imported from the pipeline.

---

### 22.3 Notes on the source data

#### 22.3.1 Export as PDF or CSV is the wrong way to get this file

The first attempt used SharePoint's **Export** menu, and both offered formats
came out unusable:

- **PDF export** is laid out for *printing* — page breaks mid-table, headers
  repeated per page, wide columns cut off. It also throws away all cell
  structure, which then has to be reconstructed by heuristics (see
  DEVLOG §21 for how much work that is).
- **CSV export** takes one sheet only, drops merged cells and any meaning
  carried by formatting, and German Excel writes it with **semicolon**
  delimiters and **comma** decimal separators — which looks like one garbled
  column in a viewer expecting commas.

The fix is to take the raw `.xlsx` via **Download** (the `⋯` menu on the file, or
*File → Create a Copy → Download a Copy*), which is a different menu from
`Export`. The `.xlsx` keeps sheets, cells, types and cached formula values, and
`openpyxl` reads it directly with no export step to mangle anything.

**Source-quality ranking: `.xlsx` → `.csv` → `.pdf`.**

#### 22.3.2 The sheet layout is found, not assumed

The table starts at **B4**, with the title in B2 and blank spacer rows around it,
and rows 23–28 are trailing empties. Rather than hard-code that offset,
`read_rows()` locates the header row by its **labels** (`Thema / Kategorie`,
`Inhalt / Wissen`) and raises a clear error if they are missing:

```
Knowledge Base.xlsx: no header row with columns 'Thema / Kategorie' and
'Inhalt / Wissen' — has the sheet layout changed?
```

That follows the project's validate-at-the-boundary convention: fail loudly
rather than write empty files. A half-filled row (topic but no content, or vice
versa) is logged as a warning and skipped rather than silently emitted.

#### 22.3.3 Hand-typing artifacts

- **In-cell line breaks** are meaningful — the `Einspeiseanlagen –
  Bankverbindung` row uses one to separate the explanation from the "send us an
  e-mail at …" instruction. They are kept as paragraph breaks; runs of spaces
  within a line are collapsed (`E-Mail  mit` → `E-Mail mit`).
- **The topic separator is an en dash** (`–`), not a hyphen. The split accepts
  either, but only when **spaced**, so a compound like `Gäste-WLAN` is never cut.
- **`data_only=True`** returns computed values instead of formula text. Caveat:
  Excel only caches those on save, so a formula edited and never saved would read
  as empty — this sheet has no formulas, and the incomplete-row warning would
  catch it if a future version did.
- Email casing is inconsistent in the source (`Vertrieb@` vs `vertrieb@`). Left
  **verbatim** — it is the customer's content and case is irrelevant to delivery.

#### 22.3.4 Personal-data check

Because this file came from the customer and the knowledge base feeds a
customer-facing bot, the content was scanned before conversion. **No personal
data:** no IBANs, no phone numbers, no customer names, no identifier digit runs.
The only email addresses are company ones (`einspeiser@`,
`vertrieb@stadtwerke-waiblingen.de`) which are part of the answers themselves and
belong in the KB. Re-check this if a revised file ever arrives.

---

### 22.4 What was verified

- **All 18 source rows present** in their output file, compared
  whitespace-insensitively; all 18 sub-topic headings present.
- **8 groups, 18 topics** — the totals the script reports match the sheet.
- **Filenames pure ASCII** for all 8 files.
- **Pipeline integration**: `main.py:copy_static()` returns 14 pages
  (`Kundenportal` + 5 from `PDFs/` + these 8), and `uploader.chunk_params_for`
  confirms every one is a single chunk.

---

### 22.5 If a revised file arrives

`.gitlab-ci.yml` runs this script before every weekly crawl, so the procedure is:

1. Replace `Knowledge Base.xlsx` in this folder — via GitLab's **Upload file**
   button in a browser is fine, and needs no git knowledge (`HANDOVER.md`).

That is all. The next weekly run re-converts it, uploads the result, and removes
any knowledge-base file whose group no longer exists. No local run, no
`static/*.md` to commit, no `--upload` to remember.

`_clean_generated` deletes this script's own previous output (everything matching
`static/Wissensdatenbank_*.md`) before regenerating, which is what makes a
renamed group safe: a stale local file would otherwise keep being uploaded for
ever, because `prune_stale` only removes remote files *no longer produced
locally*. This retires the old step 3 chore. Rendering happens before `static/`
is touched, so a failure mid-way cannot leave a short set behind for
`prune_stale` to act on.

**A SharePoint/Graph API sync is still not worth building.** The manual step is
now a file upload in a browser, so an app registration and a CI secret would buy
almost nothing and cost real maintenance. If the sheet ever becomes a genuinely
living document edited weekly, that is the moment to revisit it.

`read_rows` still fails loudly if the two column headers are gone, which is the
guard that matters here — a scheduled run must not quietly upload empty files.
The Excel has no equivalent of the PDF tariff tables' silent-degradation risk:
its structure is cells, not inferred layout.

#### Known limitations

- **Group names come from the text before the dash.** A topic typed without the
  ` – ` separator becomes its own single-topic group; an inconsistent prefix
  (`Einspeiseanlage` vs `Einspeiseanlagen`) would silently create two groups —
  and on a scheduled run nobody reads the per-group topic counts in the log. This
  is the one thing worth spot-checking after a revised sheet lands.
- **Partial unit tests.** `_clean_generated` is covered
  (`tests/test_convert.py`). `read_rows`, `group_rows`, `render` and `ascii_name`
  are all pure and testable, and not yet tested.
- **Column D exists but is empty** in the current file and is ignored. A third
  column added later would need a decision about how to render it.

---

## 23. FAQ bot — retrieval-augmented answering

This folder is the **RAG layer** built on top of the crawler: it reads the
crawler's clean markdown, embeds it into a local Chroma vector store, and
answers questions about Stadtwerke Waiblingen through a Gradio UI.

It is a **personal learning project** and is *not* part of the company crawler
pipeline. The crawler stays LLM-free and unchanged; this layer only *reads* its
output (`outputs/clean/`) and never writes to it. Nothing here touches the
company knowledge base at `aigateway.eu` — that is `crawler/uploader.py`'s job, and only
on the `crawler-sw-waiblingen` branch (named `crawler-crawl4ai` until 2026-09-14).

Written 2026-09-04, extended 2026-09-11 with the **`pro_implementation/`**
layer (§23.7): four switchable chunking methods and switchable retrieval steps, so
the design decisions can be measured instead of argued about. Companion to
DEVLOG §24, which documents the test set and the metrics used to
judge this layer.

---

### 23.1 Architecture

```
outputs/clean/*.md          (produced by the crawler: 62 crawled + static pages)
        │
        ▼
implementation/ingest.py    DirectoryLoader → MarkdownTextSplitter
        │                   → OpenAIEmbeddings(text-embedding-3-large)
        ▼
faq_bot/vector_db/          Chroma, persisted on disk (collection "langchain")
        │
        ▼
implementation/answer.py    retrieve k=10 → build system prompt → ChatOpenAI
        │
        ▼
app.py                      Gradio Blocks: chat + retrieved context side by side

evaluation/                 measures retrieval and answers against tests.jsonl
```

A second, independent implementation was added later in `pro_implementation/`
— four switchable chunking methods and switchable retrieval steps, documented in
full in §23.7. It is the one the dashboard evaluates; `implementation/` above is
the simpler course version, kept as a baseline.

| File | Role |
|---|---|
| `implementation/ingest.py` | Build the vector store. Run it whenever `outputs/clean/` changes. Deletes the old collection first, so it is a full rebuild, not an update. |
| `implementation/answer.py` | The RAG core: `fetch_context`, `combined_question`, `answer_question`. Takes and returns **plain strings** — no UI types (§23.4.4). |
| `app.py` | The Gradio UI, and the only place that knows about Gradio's message format. |
| `vector_db/` | The persisted Chroma store. Committed to git — see §23.4.8 for the trade-off. |
| `explore_chunks.ipynb` | The notebook the ingest/answer code grew out of; kept for the chunk-map and token-count experiments. |

**The dependency direction is one-way**: `app.py → answer.py`, and
`evaluation/ → answer.py`. `answer.py` imports nothing from either. That is what
lets the evaluation call the RAG core without starting a UI.

---

### 23.2 How to run it

#### 23.2.0 First-time setup

```bash
uv sync --group bot          # from the repo root; installs langchain, chroma, gradio, …
```

`.env` in the **repo root** needs one key for this layer:

```
OPENAI_API_KEY=sk-…          # embeddings + answering + the LLM judge
```

`.env` is gitignored. The crawler's own keys (`PUSHOVER_*`, `AIGATEWAY_KEY`) are
unrelated and not needed here.

#### 23.2.1 Where the knowledge files come from

Everything ingested is **`outputs/clean/*.md`** — one markdown file per page,
produced by the crawler (81 files, ~252,000 characters). Nothing in this layer
ever writes there.

```
outputs/clean/Privatkunden_Baeder.md          ← crawled page
outputs/clean/Wissensdatenbank_SEPA.md        ← from Excels/, via the crawler's static/ copy
```

The commands for producing that folder are in the repo root `README.md`
("The whole pipeline, end to end"). To **change the corpus**, do it upstream and
re-ingest:

| To… | Do this | Then |
|---|---|---|
| add/refresh crawled pages | edit `sites/waiblingen.yaml`, run `uv run python crawler/main.py` on the crawler branch | re-run `ingest.py` |
| add a hand-written page | put a `.md` in `static/`, re-run the crawler | re-run `ingest.py` |
| add a PDF/Excel source | drop it in `PDFs/`/`Excels/`, run the converter, re-run the crawler | re-run `ingest.py` |
| test with your own files | drop any `.md` into `outputs/clean/` | re-run `ingest.py` |

The filename matters: `ingest.py` stores it as the chunk's `type`/`source`
metadata, and `evaluator.py`'s chunk map colours by the first two
underscore-separated segments (`Privatkunden_Strom_Waermestrom.md` →
*Privatkunden Strom*). Keep the `Section_Subsection_Page.md` shape.

**A warning that has already cost a debugging session:** `outputs/` is gitignored
and never cleaned, so it accumulates files from other branches. An earlier index
contained 64,778 characters of waste-disposal text for a different company
(§23.4.6). Anything that globs that folder inherits every leftover — delete
`outputs/` and re-crawl before building an index you intend to trust.

#### 23.2.2 Running each program

Two implementations exist. Everything below uses `pro_implementation/`, the
current one; `implementation/` is the simpler course version (§23.1).

```bash
# 1. build a vector store  (from faq_bot/pro_implementation/)
cd faq_bot/pro_implementation
../../.venv/bin/python ingest.py --method whole     # seconds, ~2¢
../../.venv/bin/python ingest.py --list             # check what exists

# 2. chat with it  (from faq_bot/)
cd ..
../.venv/bin/python app.py

# 3. measure it  (from evaluation/)
cd ../evaluation
../.venv/bin/python evaluator.py
```

**Each of the three must be started from its own folder.** They use
sibling imports (`from implementation.answer import …`, `from eval import …`)
that only resolve when that folder is the one the script was launched from.
Running `python faq_bot/app.py` from the repo root fails with `ModuleNotFoundError`.

**`ingest.py` is the exception** — its paths come from `__file__`, so it works
from anywhere. Run it from its folder anyway, for consistency.

**After changing a dependency, restart the process.** A running Gradio app or
notebook kernel keeps the packages it loaded at start-up (§23.4.3).

#### 23.2.3 Choosing the chunking method

The method is a CLI flag; each writes its own collection, so all four can exist
side by side (§23.7.1, §23.7.4):

```bash
../../.venv/bin/python ingest.py --method whole       # 1 file = 1 chunk       →  81
../../.venv/bin/python ingest.py --method markdown    # LangChain defaults     → 128
../../.venv/bin/python ingest.py --method recursive   # 500 chars / 100 overlap→ 738
../../.venv/bin/python ingest.py --method llm         # LLM picks split points (CHUNK_MODEL)
../../.venv/bin/python ingest.py --method whole_summary   # whole + LLM headline/summary (SUMMARY_MODEL)
../../.venv/bin/python ingest.py --method all         # build all five collections
```

Chunk size and overlap for `recursive` are **module constants** at the top of
`ingest.py`, not flags:

```python
CHUNK_SIZE = 500        # characters per chunk        (recursive only)
CHUNK_OVERLAP = 100     # characters carried forward  (recursive only)
AVERAGE_CHUNK_SIZE = 1000   # target size the llm method is asked to aim for
WORKERS = 10            # parallel LLM calls; set to 1 on rate-limit errors
```

Edit, re-run, and the collection name records what you used
(`recursive_Chunksize_500_Overlap_100`), so the old run is not overwritten.

#### 23.2.4 Turning rewrite and rerank on or off

These are **query-time** switches, so no re-ingest is needed. Three ways to set
them:

**In the dashboard** — two checkboxes at the top of `evaluator.py`. This is the
normal way, and the only one that lets you compare configurations in one
session.

**In code** — build a `RagConfig` and pass it in:

```python
from faq_bot.pro_implementation.answer import RagConfig, answer_question

answer, chunks = answer_question(
    "wann ist die Hallenbadsaison?",
    config=RagConfig(collection="llm_chunks", rewrite=True, rerank=True),
)
```

**As the default for everything** — edit the class in `answer.py`:

```python
class RagConfig(BaseModel):
    collection: str = "whole_file"    # which store app.py and any caller uses
    rewrite: bool = False
    rerank: bool = False
```

`app.py` passes no config, so it always uses these defaults — **editing this
class is how you change what the chat UI does.** What each switch costs and buys
is in §23.7.5.

#### 23.2.5 Editing the test set

`evaluation/tests.jsonl` — one JSON object per line, 82 lines. Add a question by
appending a line:

```json
{"id": 83, "question": "Was kostet eine Monatskarte?", "keywords": ["Monatskarte", "45,00"], "reference_answer": "Eine Monatskarte kostet …", "category": "numerical", "source": ["Privatkunden_Baeder"], "answerable": true, "behaviour_test": false, "tester": "Zeyuan", "tester_result": "", "note": ""}
```

Only four fields are read by the code (`evaluation/test.py`): `question`,
`keywords`, `reference_answer`, `category`. The rest document the question and
are there for metrics that don't exist yet. Rules that matter:

- **`keywords` drive the retrieval score.** They are matched case-insensitively
  as substrings of the retrieved chunks, so pick 2-5 terms that must appear in a
  correct answer — including numbers exactly as written in the corpus
  (`16. September`, `13,80`). A keyword that appears nowhere in `outputs/clean/`
  scores 0 for ever and quietly drags the average down.
- **`reference_answer` is what the LLM judge compares against.** Ground it in
  the corpus; never invent a fact (§24.2.2).
- **`category` is the x-axis of both bar charts**, so a typo creates a new bar.
  The eight values are listed in §24.2.6.
- **Questions are kept verbatim, typos included** — real users type that way.
- The file must be **UTF-8** and every line valid JSON. `load_tests()` fails on
  the first bad line.

Full field reference and the composition of the current 82: §24.1–§24.2.

#### 23.2.6 Where the prompts are — and which one you may edit

Five prompts drive the system. **Only the first is meant to be edited.**

| Prompt | Where | Runs when | Edit it? |
|---|---|---|---|
| **Answering** | **`pro_implementation/prompts.py`** | every question | **yes — this is the knob** |
| Query rewriting | `pro_implementation/answer.py:rewrite_query` | `rewrite=True` | no |
| Reranking | `pro_implementation/answer.py:rerank` | `rerank=True` | no |
| LLM chunking | `pro_implementation/ingest.py:make_prompt` | `--method llm` | no |
| LLM judging | `evaluation/eval.py:evaluate_answer` | Answer Evaluation tab | no |

**The answering prompt** sets tone, what the bot does when the context does not
contain the answer, and which company it represents. Editing it changes answers
immediately — no re-ingest, no restart beyond the app itself. This is where to
fix behaviour like *"the bot points at a PDF instead of quoting it"*
(§24.3.3).

**The other four are machinery, not phrasing.** Their replies are parsed into
`RankOrder`, `Chunks` and `AnswerEval` objects, so a reworded instruction can
break the parsing rather than merely change the output. Two specific traps: the
rewrite prompt must keep its *"in the SAME language as the user's question"*
line (§23.7.7), and the chunking prompt must keep demanding the original text back
unchanged — without it, `--method llm` would store a summary instead of the
document. Editing the chunking prompt also means re-running
`ingest.py --method llm`, because it changes what is *stored*, not what is said.

The answering prompt was moved into `pro_implementation/prompts.py` on
2026-09-12 for exactly this reason; §23.8.1 explains the decision and why the other
four stay where they are. `answer.py` imports it with a `try/except` that covers
both import styles (plain when run from inside the folder, relative when
imported as `faq_bot.pro_implementation.answer` by the evaluation) — a bare
`from prompts import …` breaks the second case with `ModuleNotFoundError`.

---

### 23.3 Current settings

**`pro_implementation/` (current).** Every value below is a module constant —
this table is the complete list of knobs:

| Setting | Value | Where |
|---|---|---|
| Embedding model | `text-embedding-3-large` (3072 dims) | `ingest.py`, `answer.py` — **must match** |
| Chunking / rewrite / rerank / answer model | `openai/gpt-4.1-nano` — ⚠️ **retired by OpenAI, must be replaced** (§23.4.9) | `ingest.py:MODEL`, `answer.py:MODEL` |
| LLM judge model | `openai/gpt-4.1` (deliberately stronger) | `evaluation/eval.py:MODEL` |
| Chunk size / overlap | 500 / 100 — **`recursive` only** | `ingest.py:CHUNK_SIZE`, `CHUNK_OVERLAP` |
| LLM chunk target | 1000 chars | `ingest.py:AVERAGE_CHUNK_SIZE` |
| Parallel chunking workers | 10 | `ingest.py:WORKERS` |
| Retrieved per search | `RETRIEVAL_K = 10` | `answer.py` |
| Passed to the model | `FINAL_K = 5` | `answer.py` |
| Default store / switches | `whole_file`, rewrite off, rerank off | `answer.py:RagConfig` |
| Store | Chroma, `pro_implementation/preprocessed_db`, 4 collections | both |
| Retry policy | 3 attempts, exponential 10 s → 240 s | `answer.py:@retry` |
| Retry policy, LLM chunking | ⚠️ **no `stop` — retries for ever** (§23.4.9) | `ingest.py:process_document` |
| Embedding cap | 8,192 tokens per input; a larger page is split on headings into ~20,000-char pieces | `ingest.py:MAX_EMBED_TOKENS`, `WHOLE_FALLBACK_CHARS` |

The `openai/` prefix is **litellm** syntax. `pro_implementation` calls litellm,
so it needs the prefix; `implementation/` calls LangChain's `ChatOpenAI`, which
needs the bare name `gpt-4.1-nano`. Copying a model string between the two files
without adjusting the prefix produces a 404.

**`implementation/` (the simpler course version).**

| Setting | Value | Where |
|---|---|---|
| Embedding model | `text-embedding-3-large` (3072 dims) | `ingest.py`, `answer.py` — **must match** |
| Answer model | `gpt-4.1-nano`, `temperature=0` — ⚠️ retired, must be replaced (§23.4.9) | `answer.py` |
| Splitter | `MarkdownTextSplitter()` — defaults: 4000 chars, 200 overlap | `ingest.py` |
| Retrieval | `k = 5` | `answer.py:RETRIEVAL_K` |
| Store | Chroma, `faq_bot/vector_db`, collection `normal_chunks` | both |

Measured on the current store (82 source files → **132 chunks**):

| | chars |
|---|---|
| min | 64 |
| p25 | 918 |
| median | 1735 |
| p75 | 3362 |
| max | 3999 |
| mean | 1990 |

96 of 132 chunks are over 1000 characters. Worth knowing before tuning
retrieval: with a 4000-character budget and markdown-aware splitting, most pages
become one or two large chunks, and only the biggest pages split further. An
earlier `RecursiveCharacterTextSplitter(500, 200)` produced **803** chunks from
the same corpus — a six-fold difference from one line of configuration.

`CHUNK_SIZE = 500` / `CHUNK_OVERLAP = 200` are still defined in `ingest.py` but
are **not used** — the active splitter takes its own defaults. Leftovers from
that experiment, together with unused imports (`glob`, `MODEL`,
`RecursiveCharacterTextSplitter`).

---

### 23.4 Problems met, and how they were solved

#### 23.4.1 Two Python environments, silently

The notebook ran on a conda env (`llms`, Python 3.11) while the project's own
`.venv` (3.12, created by `uv`) was what `uv run` used. Symptom: `chromadb` and
`gradio` imported fine in the notebook but not in a script, and versions
differed (pydantic 2.11 vs 2.13, openai 2.5 vs 2.43).

**Cause:** the project `.venv` had no `ipykernel`, so the notebook *could not*
use it and silently fell back to the only environment that had a kernel.

**Solution:** one environment for everything. `ipykernel` was added to a new
`bot` dependency group in `pyproject.toml`, and the notebook kernel switched to
`.venv`. `import sys; print(sys.executable)` in a cell is the check — it must
print a path inside the project.

#### 23.4.2 The pillow conflict: `uv sync` had no solution

Adding the tutorial's dependency list produced:

```
pdfplumber 0.11.10  needs  pillow >= 12.2.0
gradio     < 6.0    needs  pillow >= 8.0, < 12.0
```

uv resolves **all dependency groups into one lockfile**, so the crawler's
`convert` group and the new `bot` group had to agree on pillow, and could not.

**Solution:** drop the `<6.0` cap and move to Gradio 6. The cap came from the
tutorial's own `pyproject.toml`, where it protects *their* project. Loosening
`pdfplumber` was the wrong direction: it is what extracts the tariff prices, and
`DEVLOG.md` §19 pins it deliberately because an extraction library that changes
behaviour between runs would silently rewrite prices.

**Considered and rejected:** declaring the groups incompatible —

```toml
[tool.uv]
conflicts = [[{ group = "convert" }, { group = "bot" }]]
```

This resolves (tested: gradio 5.50.0 with pillow 11.3.0), but the two groups can
then never be installed together, which breaks `uv run pytest` for
`tests/test_convert.py` and any session that needs both.

**The general lesson:** copy package *names* out of a tutorial, not its version
pins.

**Consequence:** Gradio 6 removed `type` from `gr.Chatbot` (`type="messages"` is
now the only behaviour, so the argument is gone). Expect more 5→6 differences.
The debugging method that works every time:

```python
import inspect, gradio as gr
inspect.signature(gr.Chatbot.__init__).parameters      # what THIS version accepts
```

Checked and still valid in 6.26: `gr.Markdown(height=, container=)`,
`gr.BarPlot(y_lim=)`, `gr.Plot`.

#### 23.4.3 "nbformat is not installed" when it was

Plotly raised `Mime type rendering requires nbformat>=4.2.0 but it is not
installed` although `nbformat 5.11.1` was in the venv.

**Cause:** `plotly/io/_renderers.py` line 33 resolves nbformat **once, at import
time**, and caches the result. The kernel had imported plotly *before*
`uv sync` installed nbformat, so the cached value stayed `None` for the life of
the process.

**Solution:** restart the kernel. The diagnostic that distinguishes this from a
real missing package:

```python
import plotly.io._renderers as r
print(r.nbformat)        # None → stale process, not a missing package
```

**The rule:** after every `uv sync` / `uv add`, restart the kernel or the app.

#### 23.4.4 The Gradio message format leaked into the RAG core

The one that cost the most time. `answer_question` worked from `app.py` but
raised `TypeError: string indices must be integers, not 'str'` from the notebook
and from `evaluation/eval.py`.

**Cause:** Gradio 6's `Chatbot` **rewrites `content`** as the value passes
through the component:

```
put in :  {'role': 'user', 'content': 'wann ist die Hallenbadsaison?'}
read out: {'role': 'user', 'content': [{'text': 'wann ist die …', 'type': 'text'}]}
                                       ↑ a list of parts
```

`combined_question` was written against that parts format
(`question[-1]["text"]`, `m["content"][0]["text"]`). `app.py` reads history back
*out of* the component, so it got lists and worked. The evaluation passes an
ordinary Python string, so `question[-1]` was the last *character* and indexing
it with `["text"]` threw. The type hints already said `question: str` — the body
disagreed with them.

| Caller | `question` is | Result |
|---|---|---|
| `app.py` (value from the Chatbot) | list of parts | worked |
| notebook / `eval.py` | `str` | `TypeError` |

**Solution:** the RAG core takes plain strings — what its own type hints
promised — and the flattening moved to `app.py`, where Gradio belongs
(`message_text`, `plain_history`). Both callers now produce an identical
combined query, verified against the real component via
`Chatbot.postprocess` → `preprocess`.

**Why not accept both shapes in the core?** Because then the evaluation could
never test the core without knowing about Gradio, and the same question would
come back at every UI change. A UI framework's internal types should stop at the
UI layer.

#### 23.4.5 Relative paths differ between a notebook and a script

`glob.glob("../outputs/clean/*.md")` works in a notebook (whose working
directory is its own folder) and breaks in a script run from the repo root.
`ingest.py` therefore derives its paths from `__file__` instead. Related:
`glob.glob` has **no guaranteed order**, so anything that depends on file order
must sort — otherwise a rebuild is not reproducible.

#### 23.4.6 Stale files in `outputs/clean/` poison the vector store

`outputs/` is gitignored and never cleaned; `crawler/main.py` only *overwrites* the files
it produces. So the folder accumulated output from other work — including
`Service_Abfall-ABC.md`, **64,778 characters of waste-disposal information for
Heidekreis** from the `crawler-ahk` branch. It was embedded into an earlier DB
(177 metadata rows matched "Abfall"), where it would have answered Waiblingen
questions with content from a different company.

**Solution:** delete `outputs/` and re-crawl before building an index. It is
fully rebuildable.

A second leftover was found the same way: the folder held 82 files where the
corpus is 62 crawled + 19 static = 81. The extra one was
`Privatkunden_Baeder_Nutzungsbedingungen_Gaeste-WLAN_Baeder.md` (dated 13
August), from before that document was split into `Teil_1`/`Teil_2` — so the WLAN
terms were embedded twice, whole and split. Deleted 2026-09-04 after verifying
the two parts cover all 14 sections (8 + 6, no heading missing). It was not in
`static/` and not tracked in git, so nothing regenerates it.

**The lesson for both cases:** the upload path is safe from this — `crawler/main.py`
builds its list from the run's own results plus `static/*.md` names, never by
globbing the folder — but *anything that globs `outputs/clean/`* inherits every
leftover. Delete `outputs/` and re-crawl before an ingest run.

#### 23.4.7 The embedding model must match the store

The notebook first built the DB with `text-embedding-3-small` (1536 dims); the
code then moved to `text-embedding-3-large` (3072). Querying a store with a
different model than it was built with is a dimension mismatch — a loud error if
the sizes differ, and silently wrong results if they happen to match.

**Check before debugging retrieval quality:**

```bash
sqlite3 faq_bot/pro_implementation/preprocessed_db/chroma.sqlite3 "select name, dimension from collections;"
```

(`faq_bot/vector_db/` for `implementation/`.)

3072 = `-large`. Both `ingest.py` and `answer.py` name the model, and they must
agree; a shared constant would be better than two literals.

#### 23.4.8 Committing the vector store — undone

`vector_db/` was first committed on purpose, to avoid re-paying for embeddings.
It was **removed from git in `c175809`** (2026-09-06), and both stores are now
gitignored (`vector_db*/`, `preprocessed_db/`). **A fresh clone or checkout
therefore has no vector store at all** — run `ingest.py` once per method before
`app.py` or the dashboard (on 2026-11-01 `preprocessed_db/` on this laptop held
zero collections). Why committing was a weak deal:

- The full corpus is ~317,000 characters ≈ 108,000 tokens, so a **complete
  rebuild costs about $0.017** with `text-embedding-3-large` — less than two
  cents.
- `chroma.sqlite3` is **binary**: git cannot store a delta, so every rebuild adds
  a whole new copy to history for ever. The first commit already contained
  **three** collection directories from three earlier rebuilds, and the file is
  now ~18 MB.

If the goal is only "do not lose it", a copy outside the repo achieves the same
thing without the permanent history cost. (The old copies are still in git
history; only a history rewrite would reclaim that space.)

#### 23.4.9 `gpt-4.1-nano` was retired — and `--method llm` hung instead of failing (2026-11-01)

**Symptom:** `ingest.py --method llm` printed `Loaded 83 documents` and then
nothing, for minutes. It looked as if `llm` were not a valid method. It is —
`--method` accepts all four values.

**Cause, two layers:**

1. OpenAI **retired `gpt-4.1-nano`**. Every call now returns
   `NotFoundError: The model gpt-4.1-nano has been deprecated`. It is the
   `MODEL` of both `ingest.py` files and both `answer.py` files, so chunking with
   `llm`, rewrite, rerank *and* answering are all broken — not just ingest.
2. `ingest.process_document` carries `@retry(wait=wait)` **with no `stop`** —
   the exact bug §23.7.7 fixed in `answer.py` but not here. tenacity retries the
   `NotFoundError` for ever with a 10 s → 240 s backoff, inside 10 pool
   workers, and prints nothing. A permanent error became a silent hang.

**How it was found:** calling the function *without* its decorator, which
tenacity exposes as `__wrapped__`, on the smallest page:

```bash
cd faq_bot/pro_implementation
uv run python -c "import ingest; d = sorted(ingest.fetch_documents(), key=lambda d: len(d['text']))[0]; ingest.process_document.__wrapped__(d)"
```

**Fix (to do):** set `MODEL` to a live model in all four files — checked
working on 2026-11-01: `gpt-4.1-mini`, `gpt-4.1`, `gpt-5-nano`, `gpt-5-mini`
(litellm needs the `openai/` prefix, LangChain the bare name, §23.3); and add
`stop=stop_after_attempt(3)` to `process_document`. A model change also changes
the `llm` chunks and every answer, so earlier scores are not comparable with
later ones. The judge (`gpt-4.1`) still works.

---

### 23.5 Known limitations

- **Chunk sizes are very uneven** (64 → 3999 chars, median 1735). The splitter
  runs on its default 4000-character budget, so most pages survive as one or two
  large chunks. Large chunks average many topics into one vector, which is
  exactly what §24.2.1 argued against for the Excel pages.
- **`k = 10` is fixed** and never tuned against the test set.
- **No store survives a clone** — both are gitignored (§23.4.8); rebuild with
  `ingest.py` first.
- **`share=True`** in `app.py` and `evaluation/evaluator.py`: while either runs,
  anyone with the printed `gradio.live` link can use it on your OpenAI key.
  Stop it with `Ctrl+C` when done.
- **Retrieval is scored by keyword presence, not by source file.** The test set
  carries a `source` field precisely so retrieval can be judged on whether the
  *right document* came back; `eval.py` does not use it yet.
- **Undeclared dependencies:** `pandas` and `litellm` are imported directly but
  are not in `pyproject.toml` — they work only because gradio and other packages
  pull them in.
- **No tests.** The crawler's pure functions are unit-tested; nothing here is.
  `create_chunks_whole_file`, `merge_chunks` and `collection_for` are pure
  functions and would be the obvious first three.
- **Small cleanups pending:** unused imports and the dead
  `CHUNK_SIZE`/`CHUNK_OVERLAP` in `ingest.py`; `model_name=` in `answer.py` is
  the legacy alias for `model=`; `evaluation/eval.py` shadows the builtin `eval`.

---

### 23.6 Next steps

Chunking, query rewriting and reranking were the three items on this list, and
all three now exist as **switches** in `pro_implementation/` (§23.7) rather than as
opinions. What is still open:

- **First: replace the retired `gpt-4.1-nano`** (§23.4.9). Nothing that calls an
  LLM works until then; retrieval with both switches off still does.

- **Hybrid retrieval**: BM25 alongside the dense vectors. The two
  `relationship` questions (Schorndorf → outside the supply area, eAnwälte →
  E-Rechtsanwälte) are exactly where lexical matching helps.
- **Metadata filtering** on `doc_type`, which is already stored on every chunk.
- **Parent-document retrieval** — embed small, return the whole page. The
  `spanning` vs `direct_fact` split in the test set is the evidence for it.
- **Tuning `RETRIEVAL_K` / `FINAL_K`** (10 and 5). Never varied against the
  test set; `FINAL_K` in particular decides how many chunks the answering model
  ever sees, and the keyword metric is measured against that same cut-off.
- **Running the four methods through the dashboard** and writing the numbers
  down. Everything in §23.7 is built and verified to run; none of it is *measured*
  yet.

Baseline to beat: the colleagues' manual results — 54 success, 14 suggestion,
14 failure — and in particular the 5 failures whose answer is already in the
corpus (§24.3.3).


---

### 23.7 The advanced implementation (`pro_implementation/`, 2026-09-11)

`implementation/` is the plain LangChain version from the course material.
`pro_implementation/` is a second, independent implementation of the same two
files that does the retrieval work itself (Chroma and the OpenAI client
directly, LangChain only for its text splitters) — and, more importantly, makes
every design decision a **parameter** instead of a line of code to edit.

The point is not that it is cleverer. It is that
`whole vs markdown vs recursive vs llm` and `rewrite on/off`, `rerank on/off`
can now be *compared* on `evaluation/tests.jsonl` rather than chosen by taste.

```
outputs/clean/*.md   (81 files, 252,003 characters)
        │
        ▼
pro_implementation/ingest.py     --method {whole,recursive,markdown,llm}
        │                        one collection per method
        ▼
pro_implementation/preprocessed_db/     Chroma, 4 collections side by side
        │
        ▼
pro_implementation/answer.py     RagConfig(collection, rewrite, rerank)
        │                        embed → [rewrite+2nd search] → [rerank] → top FINAL_K
        ▼
evaluation/evaluator.py          three UI fields feed a RagConfig into every run
```

#### 23.7.1 The four chunking methods

| `--method` | how it splits | chunks | min | median | max |
|---|---|---:|---:|---:|---:|
| `whole` | no splitting: one file = one chunk (own code) | 81 | 307 | 1,961 | 17,989 |
| `markdown` | `MarkdownTextSplitter()` — LangChain defaults (4000/200) | 128 | 64 | 1,733 | 3,999 |
| `whole_summary` (2026-11-09) | `whole` + LLM headline and summary (German) on top of each page; page text untouched → collection `whole_file_summary` | 83 | — | — | — |
| `whole_questions` (2026-11-09) | `whole` + LLM headline and 3–5 example customer questions (German, no numbers) on top of each page → collection `whole_file_questions` | 83 | — | — | — |
| `recursive` | `RecursiveCharacterTextSplitter(500, 100)` | 738 | 3 | 401 | 500 |
| `llm` | `gpt-4.1-nano` writes headline + summary + original text | ~298 | — | — | — |

Measured on the 81 files in `outputs/clean/` on 2026-09-11.

**Why `recursive` does not use LangChain's defaults.** It was tried: at the
default 4,000/200 it produces output **byte-identical** to `markdown` — 128
chunks, 0 differing. LangChain's default budget is larger than most of these
pages (median ~1,700 characters), so most pages never split, and the markdown
separators never get a chance to matter. Running `recursive` at 500/100 is what
makes the pair a real comparison: *small chunks* against *page-sized chunks*.

**What `llm` actually changes.** Its `page_content` is
`headline + "\n\n" + summary + "\n\n" + original_text`, so it changes **two**
variables at once — where the boundaries fall *and* extra LLM-written text that
is full of question-like wording. If it wins, the summaries are the more likely
cause than the boundaries. Splitting that into its own switch is left for later.

#### 23.7.2 Heading-only chunks, and why overlap does not prevent them

`recursive` at 500/100 produces **19 chunks under 50 characters that are only a
heading**:

```
'# Netze - Messstellenbetrieb'
'# Geschäftskunden - Erdgas - Grundversorgung'
'###  Technische Anschlussbedingungen'
```

They appear because the splitter cuts at `\n\n` and then merges pieces greedily
up to `chunk_size`: a 28-character heading followed by a 480-character paragraph
is 508 > 500, so the heading is flushed alone.

They are actively harmful. A heading is the purest possible keyword text, so it
scores *higher* similarity than the paragraph that actually answers the
question, wins one of the `FINAL_K = 5` slots, and contributes nothing. The
keyword metric even counts it as a hit — which is the failure mode to watch for:
`recursive` scoring well on retrieval and badly on answers.

**`chunk_overlap` does not fix this.** Overlap copies the *tail of the previous
chunk forward*; it never merges a small chunk backwards and never removes one
already emitted. And it is best-effort: LangChain drops the carried-over text
whenever `total + next_piece > chunk_size`. Verified on this corpus — chunk 53
does **not** repeat the heading that is chunk 52, despite `chunk_overlap=100`,
because keeping it would have overflowed the 500-character budget.

What would help: a bigger `chunk_size` (why `markdown` has none),
`MarkdownHeaderTextSplitter` (heading becomes metadata, never content), or a
contextual prefix on every chunk — which is what `llm` already does in spirit.

#### 23.7.3 The CLI

```bash
cd faq_bot/pro_implementation

../../.venv/bin/python ingest.py --help
../../.venv/bin/python ingest.py --method whole        # seconds, ~2¢ of embeddings
../../.venv/bin/python ingest.py --method markdown
../../.venv/bin/python ingest.py --method recursive
../../.venv/bin/python ingest.py --method llm          # ~2 min, 81 nano calls, 10 workers
../../.venv/bin/python ingest.py --method all          # every method in METHODS, one collection each

../../.venv/bin/python ingest.py --list                # what is in the store
../../.venv/bin/python ingest.py --delete markdown     # remove one collection
```

`--list` prints the name and chunk count of every collection; `--delete` prints
the count before removing, and refuses an unknown name by listing what exists.
`argparse`'s `choices=` rejects a mistyped method instead of silently doing
nothing.

#### 23.7.4 One collection per method, and the chunk count as a fingerprint

```
whole_file                             81 chunks
markdown                              128 chunks
recursive_Chunksize_500_Overlap_100   738 chunks
llm_chunks                            298 chunks
```

All four live in the same `preprocessed_db/` folder. A Chroma *collection* is
the equivalent of a table: independent contents, independent dimensions, one
`delete`/`rebuild` per collection. Keeping them side by side is what allows a
method to be re-measured later without paying for the other three again.

**The name records the parameters** (`recursive_Chunksize_500_Overlap_100`) so a
run at another chunk size cannot silently overwrite this one. `markdown` and
`whole` take no parameters, so their names carry none.

**The chunk count is the fingerprint.** This is not decoration: a collection
named `llm_chunks` was found holding **128** chunks — the markdown count —
left over from a run made before the naming was settled. Reading the counts is
how it was caught. When in doubt, `--list` first.

#### 23.7.5 The retrieval switches: `RagConfig`

```python
class RagConfig(BaseModel):
    collection: str = "whole_file"
    rewrite: bool = False
    rerank: bool = False
```

The caller builds one and passes it down; `fetch_context` reads it:

```python
chunks = fetch_context_unranked(question, config.collection)   # always
if config.rewrite:   # + 1 LLM call: rewrite, search again, merge the two hit lists
if config.rerank:    # + 1 LLM call: reorder everything by relevance
return chunks[:FINAL_K]
```

| | what it costs | what it buys |
|---|---|---|
| `rewrite=False, rerank=False` | **no LLM call at all** | pure vector search — free and fast to iterate on |
| `rewrite=True` | 1 call/question | a second, contextual query; helps follow-up questions |
| `rerank=True` | 1 call/question | the top `FINAL_K` chosen by a model, not by cosine |

`config=None` means "use the defaults", so `app.py` calls the core exactly as
before and is unaffected by any of this.

The collection is opened through a cached lookup rather than at import:

```python
@lru_cache
def get_collection(name):
    return PersistentClient(path=DB_NAME).get_collection(name)
```

`lru_cache` so 82 evaluation questions do not open 82 SQLite connections, and
**`get_collection`, not `get_or_create_collection`** — the reader must fail
loudly on an unknown name instead of silently receiving an empty collection.
(`ingest.py`, the writer, legitimately keeps `get_or_create`.) One caveat: the
cache holds the handle for the life of the process, so restart the dashboard
after re-ingesting a collection it has already read.

#### 23.7.6 How to run the whole thing

```bash
uv sync --group bot
cd faq_bot/pro_implementation
../../.venv/bin/python ingest.py --method whole      # build at least one store
cd ../../evaluation
../.venv/bin/python evaluator.py                     # pick store + switches in the UI
```

`app.py` imports `pro_implementation.answer`, so the chat UI uses this layer.
`implementation/` is commented out one line above — switching back needs its own
`ingest.py` run first, because the two use different stores.

#### 23.7.7 Problems met, and how they were solved

**Chroma rejects descriptive collection names.** `recursive -> Chunksize: 500,
Overlap: 100` fails with `InvalidArgumentError: Expected a name containing 3-512
characters from [a-zA-Z0-9._-], starting and ending with a character in
[a-zA-Z0-9]`. A collection name is an identifier, not a label. Worse, the
failure arrived *after* the embeddings had been paid for, because
`create_embeddings` called the API before `get_or_create_collection`. Creating
the collection **first** turns a 30-second wasted spend into a 0.1-second error
— the same "fail before doing damage" rule as the crawler's converters
(`CLAUDE.md`).

**The query rewriter answered in English.** `wann ist die Hallenbadsaison?` came
back as *"When is the indoor swimming pool season?"* — an English query against
an entirely German corpus. The prompt and its example were English, so the model
followed suit. It was invisible because `fetch_context` also searches with the
original question and merges both hit lists, so half the retrieval was simply
wasted. Fixed by requiring the answer *in the SAME language as the user's
question*. Found by reading the `print(rewritten_question)` output during a
smoke test — the one time that print earned its keep.

**`@retry` without `stop` is infinite.** tenacity's default is
`stop=stop_never`, so a plain `TypeError` in `answer_question` was caught and
retried for ever with 10 s → 240 s backoff. The symptom was not an error but a
**hang**: a test run had to be killed after two minutes. All three decorated
functions in `answer.py` now carry `stop=stop_after_attempt(3)` — but
`ingest.py:process_document` was missed (§23.4.9). A retry policy is for
transient failures; without a stop it also "retries" your bugs.

**`llm` chunking corrupted the text it copied (2026-11-09).** The LLM retyped
every page into `original_text`; 30 of 243 chunks were not verbatim, 15 with
real damage: a whole page with umlauts turned into `\x00e4` (broken JSON
escapes), invisible soft hyphens inside words, invented typos. Fix: the LLM now
returns only `headline`, `summary` and `start` (the chunk's first words);
`cut_chunks` locates each start in the page and cuts the text itself, adding
`OVERLAP_CHARS` (200) of overlap. Copy errors are impossible by construction; a
start that is not found makes `@retry` ask again, and after 3 failures that one
page falls back to the markdown splitter. Headline and summary are requested in
German — in English they would not match German questions.

**A config passed positionally landed in `history`.** `answer_question(question,
history=None, config=None)` called as `answer_question(q, config)` puts the
config in `history`. In `fetch_context` that was silent — the defaults were used
and every configuration produced identical scores; in `answer_question` it
crashed inside `make_rag_messages` (`[system] + history + [user]`), and then hung
on the retry loop above. Across a four-level call chain, pass by keyword:
`config=config`.

**Mutable default arguments.** `history: list[dict] = []` and
`config=RagConfig()` are evaluated **once, at `def` time**, and shared by every
later call — a pydantic model is mutable, so one `config.rerank = False`
anywhere would change the default for the whole process. The fix is always
`=None` plus `history = history or []` in the body. Half of it is not enough:
changing the default to `None` without adding the body line moved the crash from
"shared state" to `TypeError: can only concatenate list (not "NoneType") to
list`.

**Import-time side effects break renames.** `answer.py` opened its collection at
module level (`collection = get_collection("docs")`). Renaming that collection
made `import answer` itself fail. Nothing that runs at import should depend on
the *contents* of a database.

**`multiprocessing.Pool` workers cannot see runtime values.** `--method llm`
runs 10 workers, and macOS spawns them: each child re-imports `ingest.py`, so it
sees module-level constants only — never anything assigned inside
`if __name__ == "__main__":`. This is why `AVERAGE_CHUNK_SIZE` is a constant and
not a CLI flag: wiring an argparse value into a worker silently keeps the old
value. If it is ever needed there, pass it as data (`functools.partial`, or a
field on each item), never as a global.

#### 23.7.8 Known limitations

- **`llm` chunking hangs instead of failing** on any permanent error —
  `process_document`'s `@retry` has no `stop` (§23.4.9).
- **Nothing here is measured yet.** All four collections were built (2026-09-11) and the
  pipeline is verified end to end on single questions; no full run over the 82
  test questions has been recorded.
- **`llm` chunking is not reproducible.** Two runs of the same corpus gave 300
  and 298 chunks. Never compare a score taken before a rebuild with one after.
- **The 19 heading-only chunks are embedded as they are** — no minimum-length
  filter. Deliberate: the dashboard should *show* the cost before it is fixed.
- **`whole` stores a 17,989-character page as one vector** (~4,500 tokens, below
  the 8,191-token embedding limit, so nothing is truncated — but one vector has
  to represent a whole page).
- **Chunk size and overlap are module constants**, not CLI flags. Changing them
  means editing `ingest.py`; only `recursive` records them in its collection
  name, so a re-run at another size is safe only for that method.
- **All chunks are embedded in a single API call.** Fine at 738 chunks
  (~63,000 tokens against limits of 2,048 inputs / ~300,000 tokens), but it
  breaks below roughly 150 characters per chunk. Batching in slices of ~500 is
  the fix when that day comes.
- **Two return types.** `whole` and `llm` return `Result`; `recursive` and
  `markdown` return LangChain `Document`s. It works only because both types
  happen to expose `.page_content` and `.metadata` — rename a field in `Result`
  and the splitter paths break silently.
- **`DB_NAME` and the collection name live in two files** (`ingest.py` writes,
  `answer.py` reads) with nothing enforcing agreement. A shared `store.py` would
  end that whole class of bug, and the same applies to the embedding model name.
- **Dead constants** in `answer.py`: `KNOWLEDGE_BASE_PATH` and `SUMMARIES_PATH`
  (the latter also points at a misspelt folder that does not exist).

---

### 23.8 Toward a reusable RAG test harness (sketch, not built)

The layer has quietly become a small experiment framework: a corpus goes in,
four chunking methods and two retrieval switches can be combined, and 82 graded
questions come out as numbers. Everything that is *specific to Stadtwerke
Waiblingen* is now a small minority of the code. This section records what it
would take to make that reusable, and in what order — **none of it is built**.

#### 23.8.1 Move the answering prompt out of the code — **done 2026-09-12**

The first step, and the cheapest. It is now
`faq_bot/pro_implementation/prompts.py`; the rest of this section is the
reasoning, kept because it applies to the next prompt that becomes a knob.

Of the five prompts (§23.2.6), only **one is meant to be edited**: the answering
`SYSTEM_PROMPT`. It decides tone, how the bot behaves when the context does not
contain the answer, and which company it says it represents. The other four —
rewrite, rerank, chunking, judge — are **machinery**: their output is parsed
into `RankOrder`, `Chunks` or `AnswerEval`, so changing their wording can break
the code, not just the answers.

So the design is *not* one file with five strings. It is one file holding the
single user-editable prompt, and the other four left where they are used:

```python
# faq_bot/pro_implementation/prompts.py
#
# The one prompt intended to be edited. The rewrite / rerank / chunking / judge
# prompts stay next to the code that parses their output — changing those can
# break parsing, not just phrasing.
#
# {context} is filled in with the retrieved chunks. Any other literal brace
# must be doubled ({{ }}), because this string is passed to .format().

SYSTEM_PROMPT = """
You are a knowledgeable, friendly assistant representing {company}.
...
Context:
{context}
"""
```

This is both less work and a better boundary than moving all five: the four
internal prompts are **f-strings** (`answer.py:101`, `ingest.py:72`,
`eval.py:133`), and an f-string cannot be moved to another file — it is
evaluated where it is written, so it would raise `NameError` on import. Moving
them would mean rewriting each as a plain string plus a `.format()` call, and
`{document["text"]}` would have to be renamed because `.format()` cannot do a
quoted subscript. All that risk buys nothing, because nobody should be editing
them.

The answering prompt has none of that trouble: it is already a plain string
used with `.format(context=…)`, so moving it is a cut, a paste and an import.

Four reasons it is worth doing:

1. **It is the site-specific part.** "representing the company SW Waiblingen"
   was hardcoded in `answer.py`. Pointing this at another corpus should mean
   editing text, not Python — and with a `{company}` placeholder, one variable.
2. **A prompt is a parameter, and every other parameter is already switchable.**
   The English-rewrite bug (§23.7.7) was a *prompt* defect that silently halved
   retrieval quality. Once the answering prompt is a named constant in its own
   file, a `prompt_variant` field on `RagConfig` could put version A against
   version B over the same 82 questions.
3. **Prompts change far more often than code.** In its own file, a prompt change
   stops looking like a code change in `git diff`.
4. **Non-programmers can edit a text file.** The same argument `HANDOVER.md`
   makes for the crawler's source documents: a colleague from the Kunden-Center
   could improve the answering tone without being shown a Python file.

When the three-way split (§23.8.4) happens, this file belongs to part 2. The judge
prompt, if it is ever extracted, belongs to part 3 — not to a shared prompts
file, or part 3 would depend on part 2 for a string.

---

*The original sketch below proposed collecting all five; it is kept because the
`{company}` placeholder idea still applies to the answering prompt.*

Collected into one file, a prompt becomes the thing you tune, in one place,
without touching logic:

```python
# prompts.py
ANSWER = """
You are a knowledgeable, friendly assistant representing {company}…
Context:
{context}
"""
REWRITE  = """…"""
RERANK   = """…"""
CHUNKING = """…"""
JUDGE    = """…"""
```

#### 23.8.2 The other steps, in order

| Step | What it means | Why it is next |
|---|---|---|
| **Extract the config** | corpus path, DB path, company name, model names, `K`s into one `settings.py` or a YAML file | the same "data separate from code" rule the crawler follows (`CLAUDE.md`); it is what makes a second corpus possible at all |
| **Record every run** | append `{timestamp, config, metrics}` to a CSV/JSONL after each evaluation | today results live only in the browser; comparing four collections means four screenshots, and nothing survives a page reload |
| **Sweep instead of clicking** | a `compare.py` that loops over `[collections] × [rewrite] × [rerank]` and prints one table | the manual dashboard is fine for 4 runs, not for 16 |
| **Score retrieval by source, not keywords** | every test row already carries `source`; use it | keyword matching cannot distinguish "found the right page" from "found a page containing the word" (§24.6.5) |
| **Generalise the test set** | `tests.jsonl` + a loader is already corpus-agnostic; the *categories* are not | `procedural` was added for a utility's FAQ; another domain needs its own mix (§24.2.6) |
| **Separate the layers properly** | `core/` (corpus-agnostic) vs `sites/waiblingen/` (prompts, corpus, tests) | the point of the exercise: swapping the site should touch no Python |

#### 23.8.3 What is already reusable, and what is not

**Already generic:** the chunking methods, `RagConfig` and the switch plumbing,
the Chroma collection-per-experiment layout, the metrics (MRR / nDCG / coverage
/ LLM-judge), the dashboard, `tests.jsonl` and its loader.

**Still Waiblingen-specific:** all five prompts (company name, German),
`KNOWLEDGE_BASE_PATH` pointing at `outputs/clean`, the chunk-map category
convention (`Section_Subsection_Page.md`), the eight question categories, and
the keyword lists.

So the ratio is already favourable — which is exactly why §23.8.1 is worth doing
before the code grows further.

#### 23.8.4 The three-part split, and where each thing belongs

The planned reorganisation is three parts with **one-way arrows**:

```
part 1  ACQUISITION                 part 2  FAQ BOT            part 3  EVALUATION
┌──────────────────────────┐        ┌──────────────────┐       ┌────────────────┐
│ crawler (crawl4ai)       │        │ ingest.py        │       │ tests.jsonl    │
│ PDFs/pdf2md.py           │ writes │ answer.py        │ reads │ eval.py        │
│ Excels/xlsx2md.py        │ ─────► │ app.py           │ ◄──── │ evaluator.py   │
│ static/ (hand-written)   │        │ prompts          │       │ judge prompt   │
└──────────────────────────┘        └──────────────────┘       └────────────────┘
              │                              ▲
              └──► outputs/clean/*.md ───────┘
                   THE CONTRACT: a folder of markdown files
```

**The seam is a folder of `.md` files.** Everything that *produces* that folder
is part 1; everything that *consumes* it is part 2. Make the folder path a
config value and parts 2 and 3 stop being Waiblingen-specific at all.

**The converters belong to part 1, not next to `ingest.py`.** This was an open
question; the reasoning for the answer:

1. **`ingest.py` reads the corpus, a converter writes it.** Putting them in the
   same folder gives that folder two jobs — exactly what the split is meant to
   remove.
2. **The converters are the least portable code in the repo** — German tariff
   PDFs, the `TABLE_REQUIRED` degradation guard, the yearly `2026`→`2027`
   rename. Part 2 is the layer that should become corpus-agnostic. Putting the
   least-generic code inside the most-generic layer is backwards.
3. **Dependency direction.** `crawler/main.py` copies `static/` into `outputs/clean/`.
   If the converters moved under `faq_bot/`, the crawler would depend on the bot
   folder — an arrow pointing the wrong way, and the thing that makes a split
   stop being a split.

So: group them as sources of the corpus (`PDFs/`, `Excels/`, `static/` beside
the crawler), and leave `faq_bot/` with nothing but corpus-in, answers-out.

**The converters already run standalone**, which is what makes part 1 divisible
later:

```bash
uv run python PDFs/pdf2md.py      # PDFs/*.pdf   -> static/*.md
uv run python Excels/xlsx2md.py   # Excels/*.xlsx -> static/*.md
```

Neither needs the crawler, a browser, or a network. Each rewrites only its own
prefixed outputs (`Privatkunden_Baeder_*`, `Wissensdatenbank_*`) and exits
non-zero on a bad conversion. Their **only** tie to the crawler is
`from clean import slug, strip_links` — two pure text helpers. Moving those two
functions into a shared utility (or copying them) is all that stands between
the converters and full independence.

**One caveat when isolating the crawler on this branch.** `CLAUDE.md` §19
justifies three converter invariants — convert in memory then write, run before
the crawl, delete only your own prefix — by pointing at `prune_stale`, which
deletes knowledge-base documents that no longer exist locally. **That upload
does not exist on `main`.** Here a half-failed conversion produces a bad local
file and nothing more; there, it deletes live customer-facing documents. Keep
the invariants (they are good practice either way), but do not carry over
severity that belongs to the other branch — and do not assume a guard is load-
bearing here just because `CLAUDE.md` says it is.

---

## 24. FAQ-Bot test set

This folder holds the **evaluation data** for the FAQ bot: the colleagues' manual
test log (`SW_Waiblingen_FAQ_Testing.xlsx`) and the machine-readable test set
derived from it (`tests.jsonl`). Nothing here is part of the crawler — the
crawler produces the knowledge base, this folder measures whether a bot built on
top of it answers correctly.

Written 2026-08-29; the evaluation code followed on 2026-09-04 (§24.6) and became
configurable from the dashboard on 2026-09-11 (§24.6.2). Companion to
DEVLOG §21 and DEVLOG §22, which document the two source
converters in the same style, and to §23.7, which documents the
chunking methods and retrieval switches this dashboard selects between.

| File | What it is |
|---|---|
| `SW_Waiblingen_FAQ_Testing.xlsx` | The colleagues' manual test log — 82 questions asked against the live bot, with a verdict and remarks |
| `tests.jsonl` | The test set: one JSON object per line, **151 lines** (82 from the Excel, 22 from the Oct/Nov 2026 customer tests, 47 generated — §24.7), grounded reference answers |

---

### 24.1 What was done

`tests.jsonl` originally contained the LLM-course tutorial's fictional insurance
company (Insurellm). It was replaced with the real Stadtwerke Waiblingen test
questions so that retrieval quality can be measured against the actual knowledge
base.

Source of each field:

| Field | Where it comes from |
|---|---|
| `question` | The Excel column `Testsatz`, **verbatim** |
| `tester`, `tester_result` | Excel columns `Tester` and `Testergebnis` |
| `note` | Excel columns `Bemerkungen` / `Wo ist die Antwort zu finden`, condensed |
| `reference_answer` | Written by hand from `outputs/clean/*.md` and the updated `Excels/Knowledge Base.xlsx` |
| `keywords`, `category`, `source`, `answerable`, `behaviour_test` | Derived while writing the answer |

Line format:

```json
{"id": 1,
 "question": "wann ist die Hallenbadsaison?",
 "keywords": ["Hallenbadsaison", "16. September", "30. April"],
 "reference_answer": "Die Hallenbadsaison dauert vom 16. September bis zum 30. April. …",
 "category": "temporal",
 "source": ["Privatkunden_Baeder"],
 "answerable": true,
 "behaviour_test": false,
 "tester": "Zeyuan",
 "tester_result": "success",
 "note": ""}
```

#### Field reference

- **`question`** — byte-identical to the Excel, including typos.
- **`keywords`** — 2–5 terms a correct answer must contain. Cheap first metric;
  not a substitute for judging the answer.
- **`reference_answer`** — German, grounded. Where the knowledge base has no
  answer it states the *correct behaviour* instead (admit the gap, name the right
  phone number) rather than an invented fact.
- **`category`** — what *kind* of question it is, and therefore how hard it is to
  retrieve. Eight values: the seven standard RAG-evaluation types
  (`direct_fact`, `temporal`, `spanning`, `comparative`, `numerical`,
  `relationship`, `holistic`) plus `procedural`. See §24.2.6 for why the eighth
  exists and why `holistic` never occurs.
- **`source`** — the `outputs/clean/` file(s) the answer comes from, so
  **retrieval** can be scored separately from **generation**. A value of
  `"Knowledge Base.xlsx: <Kategorie>"` means the text exists only in the updated
  Excel and is *not yet* in the corpus — see §24.3.
- **`answerable`** — `false` when no source in the KB supports the answer. This
  is a *separate axis* from `category`: a question can be `numerical` **and**
  unanswerable (id 36, the number of pools).
- **`behaviour_test`** — `true` for the five rows that test tone and handling
  rather than knowledge. Exclude them from any retrieval score.
- **`tester_result`** — the colleagues' verdict on the **old** bot: a baseline to
  beat, not ground truth about the content.
- **`note`** — what the colleague expected instead. The most valuable column in
  the Excel; 37 of 82 rows have one.

#### Composition

*As of 2026-08-29 (82 rows). For the current 151 rows see §24.7.4.*


| Category | n | Retrieval needs | | Other axes | n |
|---|---|---|---|---|---|
| `procedural` | 31 | a whole procedure, usually one section | | success / suggestion / failure | 54 / 14 / 14 |
| `direct_fact` | 21 | one sentence | | `answerable` true / false | 70 / 12 |
| `spanning` | 11 | several parts of one page | | `behaviour_test` true | 5 |
| `temporal` | 9 | one sentence | | | |
| `numerical` | 6 | one table row | | | |
| `comparative` | 2 | two facts, usually adjacent | | | |
| `relationship` | 2 | two facts in different places | | | |
| `holistic` | **0** | many documents at once | | | |

Testers: Ines 71, Bettina 8, Nicklas 2, Zeyuan 1.
**16 of the 82 questions need more than one source file** — the hardest
retrieval cases regardless of their label.

---

### 24.2 Decisions, and why

#### 24.2.1 Questions are verbatim, typos included

`unterschief`, `Wäremstrom`, `funktionert`, `Bezugsseite` (for *Bezugsstelle*),
`eAnwälte` (for the law firm *E-Rechtsanwälte*) are all kept. Real users type
like this, and one colleague explicitly praised the bot for handling
`Wäremstrom`. Silently correcting them would delete a real test. A checked
comparison against the Excel is part of the regeneration procedure (§24.5).

#### 24.2.2 No answer was invented

The requirement was reference answers grounded in the knowledge base. Where the
KB says nothing, an answer was **not** made up — `answerable` is set to
`false` and the reference answer describes the correct behaviour. Inventing
plausible-sounding facts about tariffs and opening hours is exactly the failure
mode §21.2.1 warns about (`13,80€` becoming `1380€`): it looks fine
and is wrong. A test set that contains invented facts silently teaches the bot to
be wrong.

#### 24.2.3 `source` was added so retrieval can be scored separately

A wrong answer has two very different causes: the text was missing, or the text
was there and was not retrieved. Without a source field the two look identical in
the results. With it, retrieval accuracy (did the right file come back?) can be
measured independently of answer quality — and §24.3 shows the distinction is not
academic here.

#### 24.2.4 Tone tests are flagged, not deleted

Five rows (75, 77, 78, 81, 82) are barely questions: an angry customer demanding
the management, a complaint letter with a lawyer threat, a multi-part request.
They test tone, not knowledge, and no retrieval metric applies to them. They are
kept because they document real requirements — *do not interrupt a long
utterance*, *do not send a caller to the phone number they are already calling* —
and carry `behaviour_test: true` so they can be filtered out of retrieval
scoring.

#### 24.2.5 The old tutorial file is gone

The Insurellm data was kept for a while as `tests_tutorial_insurellm.jsonl.bak`,
on the reasoning that the course's own notebooks would need it. It is **no
longer in the repository** — it was worth nothing for this project, and the
course material can be re-downloaded. Noted here so the missing file is not
mistaken for an accident.

#### 24.2.6 The category set: seven standard types plus `procedural`

`tests.jsonl` uses the seven RAG-evaluation question types that the course
material uses — `direct_fact`, `temporal`, `spanning`, `comparative`,
`numerical`, `relationship`, `holistic` — plus one addition.

**Why adopt the standard seven.** They classify questions by *how hard they are
to retrieve*, which is the property this project is actually designing for. That
makes the distribution a specification for chunking (§24.2.7), and it keeps results
comparable with other RAG work instead of using a private vocabulary.

**Why `procedural` was added.** It is the largest group here: **31 of 82**.
"What must I do to change my bank details / report a meter reading / cancel a
tariff change" fits none of the seven. That is not a defect in the standard list;
it reflects what the corpus is. The tutorial's list was built for a company's
product and personnel documents, which are mostly *facts*. A utility's customer
FAQ is mostly *processes*. Deleting `procedural` would push 31 questions into
`direct_fact` and hide the single most characteristic property of this data.

**Why `holistic` has zero rows.** Not an oversight — there is genuinely no
holistic question in the set. The questions come from colleagues phoning the bot
as customers would, and real callers ask narrow things ("what does a ticket
cost"), never "compare all your baths" or "what subjects do you cover". This is
a **gap in the test set, not in the labelling**: holistic questions would have to
be written by hand, they will never arrive from the test log.

Two judgement calls worth knowing: ids 9 ("was habt ihr für stromtarife?") and 30
("was für Bäder gibt es") *ask* for a complete enumeration and look holistic, but
one page happens to list everything, so they are labelled `spanning`. If the site
ever splits those overviews, they become genuinely holistic.

**An earlier version of this file mixed two dimensions into `category`** — it had
`out_of_scope` and `behaviour` as if they were question types. They are not: a
question can be `numerical` *and* unanswerable (id 36). They were split out into
the `answerable` and `behaviour_test` fields.

#### 24.2.7 What the distribution says about chunk size

The category mix is evidence for the open chunking question, so it is worth
reading as such:

| Signal | Count | What it argues for |
|---|---|---|
| `spanning` | 11 | keeping a page whole — the answer is scattered across it |
| needs >1 source file | 16 | retrieving several chunks, and generous `k` |
| `relationship` | 2 | the same |
| `direct_fact` + `numerical` + `temporal` | 36 | smaller chunks would be sharper |
| `holistic` | 0 | nothing here demands whole-corpus reasoning |

So the corpus is split roughly evenly between "one small fact" and "needs
context", with nothing requiring the whole corpus at once. That supports keeping
the crawler's existing *one page = one chunk* granularity as the starting point,
and reaching for smaller chunks with a title prefix only if the 36 fact-style
questions retrieve badly. It is also a warning: pages vary from 229 to 18,175
characters, so a single 18k page as one vector will serve those 36 questions
poorly no matter what.

---

### 24.3 What the test set revealed

Writing the answers meant reading every question against the corpus. Three
findings came out of it that matter more than the file itself.

#### 24.3.1 Twelve questions have no answer in the knowledge base

| id | Question | Gap |
|---|---|---|
| 4, 7 | Was ist ein Eintarifzähler / Unterschied zum Zweitarifzähler | No definition anywhere |
| 27 | Wann haben die Kassen auf bei den Freibädern | Only bathing hours are documented |
| 36 | Wie viele Becken gibt es im Hallenbad Waiblingen | Sub-page not crawled |
| 39, 40 | Parken / Rauchen im Freibad Bittenfeld | Sub-page not crawled |
| 79 | E-Mail-Adresse ändern | Excel covers name and postal address only |
| 80 | Fälschlich eingeleiteter Lieferantenwechsel | Only the reverse case is covered |
| 75, 77, 78, 82 | Complaint / escalation handling | Not a knowledge question (`behaviour_test`) |

**Four of them (7, 36, 39, 40) were marked `success` by the testers.** If the bot
answered "how many pools does the Hallenbad have", that answer did not come from
this knowledge base. Worth knowing before trusting the 54 successes as a baseline.

Three of the gaps are cheap to close: `Privatkunden/Baeder` is crawled but its
sub-pages *Hallenbad Waiblingen*, *Freibad Waiblingen* and *Waldfreibad
Bittenfeld* are not listed in `sites/waiblingen.yaml`. Adding those three labels
would likely answer 27, 36, 39 and 40. The rest (Eintarifzähler, e-mail change)
belong in the Excel knowledge base.

#### 24.3.2 Thirteen answers exist only in the *updated* Excel

The updated `Excels/Knowledge Base.xlsx` (33 rows, uncommitted at the time of
writing) adds five whole groups that the committed 18-row version does not have:

```
Guthaben    Abschaltung    Inkasso    Mahnung    Ratenzahlung
```

Rows 65–74 and 78 depend on them, and rows 37 and 42 on two new Bäder entries
(Badeschluss 19:40, no reduction for pensioners). Their `source` therefore reads
`Knowledge Base.xlsx: …`, not a file name.

**Until `Excels/xlsx2md.py` is re-run and the pages re-uploaded, those 13
questions cannot be answered by any bot, however good the retrieval.** This is
what the testers meant by the repeated note *"To Do: bitte die Excel neu
einspielen :)"*.

#### 24.3.3 Most failures were retrieval, not missing knowledge

Of the 14 `failure` rows, 13 have the answer available somewhere:

- **5** where the text is already in the current corpus — ids 26, 28, 33, 43, 53.
  Freibad opening hours, entrance prices, reductions, gas outside the supply area,
  PV billing period. The content sits in `outputs/clean/`, and the bot did not
  find it or refused to quote a PDF's content.
- **8** blocked on §24.3.2.

Ids 26, 28 and 33 share one pattern the colleagues named explicitly: the bot
pointed at a document ("das steht im PDF") instead of answering. Since the
converters already turn those PDFs into ordinary KB pages, there is no reason to
deflect — a good sign that the failure is in the prompt, not the corpus.

**Those five rows are the highest-value tests in the file.** They are where a
better chunking and retrieval design should visibly win.

---

### 24.4 What was verified

- **All 82 questions match the Excel** exactly (whitespace-normalised compare, 0
  mismatches), and 82 rows in, 82 lines out — nothing dropped or duplicated.
- **Every line is valid JSON**, no empty `question` or `reference_answer`, all 82
  questions distinct.
- **Prices and figures cross-checked** against their sources: Freibad 5,00 / 3,50
  / 2,00 / 8,90 / 13,80 €, Hallenbad 5,70 / 4,60 €, Mahngebühr 4,00 €, Zählercode
  2.8.0, Badeschluss 19:40, water hardness 9,1–13,7 °dH.
- **UTF-8 with real German spelling** (`ä ö ü ß`, `€`), written with
  `ensure_ascii=False` so the file stays readable in an editor.

---

### 24.5 If a new test round arrives

The colleagues keep testing, so the Excel will grow. To regenerate:

1. Replace `SW_Waiblingen_FAQ_Testing.xlsx`.
2. Re-read the new rows and write `reference_answer` / `keywords` / `source` for
   them, grounded in the **current** `outputs/clean/`.
3. Verify the questions against the Excel before committing — the row count and
   the verbatim text. Both were checked when this file was built and both
   passed; keep the check anyway, because a silently shifted column produces a
   file that still looks correct.

**Two traps when re-reading the Excel:**

- The table does not start at A1 and the columns are found by position
  (`Testsatz` is index 4, `Testergebnis` 6). Unlike `Excels/xlsx2md.py`, which
  locates its header by label, this was a one-off read. If a column is inserted,
  the positions shift silently. Locate the header row by its labels when
  automating this.
- Rows without a `Testsatz` must be skipped — the sheet has 168 rows but only 82
  questions; the rest are empty rows and a stray dropdown-validation column.

**And one trap in the corpus:** `outputs/` is never cleaned, so
`outputs/clean/` held 78 files while the site has ~62 pages + 14 static. Among
them was `Service_Abfall-ABC.md` — 64,778 characters of waste-disposal
information from **ahk-heidekreis.de**, left over from the `crawler-ahk` branch,
plus an older un-split copy of the WLAN terms. Neither was used for any answer
here, but both would silently pollute a vector database built by globbing that
folder. Delete `outputs/` and re-crawl before building an index.

#### Known limitations

- **Reference answers are one person's reading of the sources.** They are
  grounded, but wording and level of detail are a judgement call; a colleague
  from the Kunden-Center should review the 14 `failure` rows in particular.
- **`keywords` are a weak metric.** They catch a missing fact, not a wrong or
  badly-worded one. Treat them as a smoke test and judge the answer separately.
- **`tester_result` is a baseline, not ground truth.** It records how the *old*
  bot did, and §24.3.1 shows four of those verdicts cannot be right.
- **The 5 `behaviour_test` rows have no objective answer.** Exclude them from any
  retrieval score.
- **The test set does not cover Geschäftskunden or Netze at all** — 17 and 19
  pages of the corpus with not a single question. The colleagues tested what
  customers ask on the phone; a retrieval benchmark would want coverage there too.

---

### 24.6 The evaluation code (added 2026-09-04)

The folder started as data only; it now holds the code that runs the test set.
Per the repo's convention (`PDFs/`, `Excels/`), data and the script that works on
it live together.

| File | Role |
|---|---|
| `test.py` | `TestQuestion` model + `load_tests()` — reads `tests.jsonl` |
| `eval.py` | The metrics: MRR, nDCG, keyword coverage, and LLM-as-a-judge answer scoring |
| `evaluator.py` | A Gradio dashboard: retrieval metrics, answer metrics, and a chunk map |
| `explore_evaluation.ipynb` | Scratch notebook the metrics grew out of |

#### 24.6.1 What is measured

**Retrieval** (`evaluate_retrieval`, no LLM, free and fast):

- **MRR** — for each keyword, `1/rank` of the first retrieved chunk containing
  it, averaged over the question's keywords. Note this is a per-keyword
  adaptation, not the textbook per-query MRR.
- **nDCG** — binary relevance (keyword present or not) with a log rank discount.
- **Keyword coverage** — the percentage of a question's keywords found anywhere
  in the top-k.

**Answers** (`evaluate_answer`, one LLM call per question): an LLM judge scores
`accuracy`, `completeness` and `relevance` from 1–5 against the
`reference_answer`, and returns written feedback. The rubric is in the
`AnswerEval` field descriptions — notably *"any wrong answer must score 1"*,
which stops a fluent-but-wrong answer from scoring 3.

#### 24.6.2 The dashboard

Run it **as a script**, from this folder (§24.6.3 explains why):

```bash
cd evaluation
../.venv/bin/python evaluator.py
```

At the top sits one **configuration row that the whole page shares**:

| field | what it selects |
|---|---|
| 🗄️ **Vector store** | which collection to evaluate — `whole_file (81 chunks)`, `markdown (128)`, `recursive_Chunksize_500_Overlap_100 (738)`, `llm_chunks (298)` |
| ✍️ **Query rewriting** | retrieve with an LLM-rewritten question as well as the original |
| 🔀 **Reranking** | let the LLM reorder the retrieved chunks before the cut-off |

The three fields become a `RagConfig` (§23.7.5) that is passed
down through `eval.py` into `answer.py`. Every result block is **stamped with
the configuration it came from** (`llm_chunks · rewrite off · rerank on`),
because a page of near-identical screenshots is otherwise unreadable a week
later.

The dropdown is filled from Chroma at start-up, not hardcoded, so it can only
offer collections that exist — and the chunk count in each label is the
fingerprint that identifies the chunking method (81 = whole file, 128 =
markdown, 738 = recursive, ~300 = llm). That is how a mislabelled collection was
caught; see §23.7.4.

Three sections read that configuration:

1. **Retrieval Evaluation** — average MRR / nDCG / coverage, colour-coded
   against the thresholds at the top of the file, plus a bar chart of MRR by
   category. This is where the `category` field pays off: a low `spanning` score
   with a high `direct_fact` score means the chunks are too small, and the
   reverse means they are too large.
2. **Answer Evaluation** — the three judge scores and accuracy by category.
   Adds one answer call plus one judge call per question, so ~164 calls per run.
3. **Chunk Map** — every chunk in the **selected** collection projected to 2D
   with t-SNE, one colour per category (the first two segments of the source
   filename). Ported from `faq_bot/explore_chunks.ipynb`. Chunks that sit
   together were embedded as similar text; a chunk far from its own colour is
   usually a chunking problem. It is behind a button so it never runs on page
   load, and takes ~15 s for ~130 chunks — noticeably longer for the 738-chunk
   `recursive` store.

**What a run costs.** Retrieval with both switches off makes *no LLM calls at
all* — only embeddings — which makes it the cheap way to compare the four
chunking methods:

| configuration | LLM calls, retrieval tab, 82 questions |
|---|---|
| rewrite off, rerank off | **0** |
| rewrite on | 82 |
| rerank on | 82 |
| both on | 164 |

So the sensible order is: sweep the four collections with both switches off,
then take the winner and try the four switch combinations on it.

The chunk map reads the collection through `answer.get_collection(name)`, which
returns the chromadb collection object directly. (It previously reached into
`vectorstore._collection`, a private LangChain attribute, because
`langchain-chroma` exposes no public way to read stored vectors back out; the
`pro_implementation` layer uses chromadb directly and needs no such trick.)

#### 24.6.3 Running it, and editing the test set

```bash
cd evaluation
../.venv/bin/python evaluator.py          # the dashboard
../.venv/bin/python eval.py 3             # one question, printed to the terminal
```

Both must be run **from this folder** — they import `test`/`eval` as siblings.
`eval.py <n>` is the cheap way to check a change: it prints the question, the
retrieval metrics, the generated answer and the judge's feedback for test `n`
(0-based), for the price of two LLM calls.

**Adding a question** means appending one line to `tests.jsonl`. Only four
fields are read by the code (`test.py`): `question`, `keywords`,
`reference_answer`, `category`; the others document the row. The field reference
is §24.1, the rules that bite are in §23.2.5, and the regeneration
procedure for a whole new test round is §24.5.

**Changing the judge.** The model is `evaluation/eval.py:MODEL`
(`openai/gpt-4.1` — deliberately stronger than the `gpt-4.1-nano` that answers,
so the judge is not marking its own homework). `gpt-4.1-nano` was **retired by
OpenAI** (found 2026-11-01, §23.4.9): until the answer model is
replaced, the **Answer** tab and any retrieval run with rewrite/rerank on fail.
Keep the judge stronger than whatever replaces it. Scores taken with a different
answer model are not comparable. The rubric lives in two places
that must agree: the `AnswerEval` field descriptions and the user message in
`evaluate_answer`.

#### 24.6.4 Problems met

- **`answer_question` could not be called at all** from `eval.py` or the
  notebook: it only accepted Gradio's message-parts format, so a plain string
  raised `TypeError: string indices must be integers`. Fixed by making the RAG
  core take strings and moving the Gradio-specific flattening into `app.py` —
  full write-up in §23.4.4. This is the concrete reason the
  layer boundary matters: an evaluation that cannot call the core without the UI
  is not an evaluation of the core.
- **Import layout.** `eval.py` inserts the repo root into `sys.path` so it can
  `import faq_bot.pro_implementation.answer`, while `evaluator.py` imports `eval`
  as a sibling. Both work when run **as scripts**; `python -m evaluation.evaluator`
  does not. A module named `eval` also shadows the Python builtin — `metrics.py`
  would avoid both problems.
- **`sys.path` is set up by the wrong module.** When `evaluator.py` grew its own
  `from faq_bot…` import, it failed with `ModuleNotFoundError: No module named
  'faq_bot'` — the `sys.path.insert` it relied on lives in `eval.py`, which is
  imported on the *next* line. Running a script puts the script's own folder on
  the path, not the repo root. `evaluator.py` now inserts the root itself rather
  than depending on import order.
- **Threading the config through silently did nothing.** `evaluate_retrieval`
  and `evaluate_answer` pass the config on to `fetch_context` /
  `answer_question`, whose second parameter is `history`. Called positionally,
  the config landed in `history`: the retrieval path then quietly used the
  defaults — four different UI configurations producing four identical scores —
  and the answer path crashed and then hung on a retry loop. Full write-up in
  §23.7.7; the rule is to pass by keyword (`config=config`)
  once a value has to cross more than one function boundary.

#### 24.6.5 Known limitations of the metrics

- **Retrieval is scored on keyword presence, not on the source document.**
  Every test row carries a `source` field for exactly this purpose (§24.2.3) and
  `eval.py` ignores it. Keyword matching cannot tell "found the right page" from
  "found a page that happens to contain the word".
- **`evaluate_retrieval(test, k=…)` does not change how much is retrieved.**
  `k` is used only in the nDCG cut-off; retrieval always returns
  `answer.RETRIEVAL_K` documents. Consistent at the default (both 10),
  misleading at any other value.
- **`ZeroDivisionError` on an empty run.** Both dashboard functions compute
  `total / count` with no guard, so a wrong path to `tests.jsonl` surfaces as a
  division error rather than "no tests loaded".
- **A typo swallows a field description:** `total_keywords` uses
  `Field(descriiption=...)`, so the LLM-visible description is lost.
- **Two docstrings still say the functions "yield updates"** when they
  `return`. (The tutorial's *"Insurellm RAG system"* subtitle is gone.)
- **Nothing records a run.** Results live only in the browser until the page is
  reloaded — there is no results file, so comparing four collections means four
  screenshots. A small CSV append per run would make the comparison a table.
- **`k` and the retrieval depth are still independent.** The dashboard exposes
  the collection and the two switches, but not `RETRIEVAL_K` (10) or `FINAL_K`
  (5), and `FINAL_K` is what decides how many chunks the metrics see at all.
- **The 5 `behaviour_test` rows and the 12 unanswerable ones are not excluded**
  from the averages. Both drag the scores down for reasons that have nothing to
  do with retrieval quality — they should be filtered or reported separately.

---

### 24.7 Revision 2026-11-06: checked against the KB, extended to 151, chatbot-only

The file had grown to 104 rows (ids 83–104 came from the customer tests of
26.10.–06.11.2026). Every row was re-checked against **`outputs/clean/`** — the
29.10.2026 crawl from `crawler-sw-waiblingen`, including the new Bäder sub-pages
and the current Excel — and 47 generated rows were added.

**The bot is now treated as a text chatbot, not a voice bot.** Voice-only
requirements (pronouncing times and numbers, reading out e-mail addresses,
interrupting long utterances, "the caller is already on the phone, so don't
give the Kunden-Center number", transferring the call) were removed from
`reference_answer`, `keywords` and `behaviour_test`. The `note` fields were
**left untouched** as test history; voice remarks there are not requirements.

#### 24.7.1 Structural fixes (all rows)

- **`source` must be a file name in `outputs/clean/`.** 13 distinct values were
  not: `Knowledge Base.xlsx: <Kategorie>` → the generated `Wissensdatenbank_*`
  page (the Excel is now in the corpus); `…_Hallenbad_Waiblingen` →
  `…_Hallenbad-Waiblingen`; `…_Freibad_WN_und_BF` → `…_Freibad-WN-und-BF`;
  `Notfallnummern` → `Störung` (the page's file name; its title is
  "Notfallnummern").
- **Every keyword of an answerable row now occurs verbatim in its source
  files.** The retrieval metric is a case-insensitive substring match, so a
  paraphrased keyword (`richtiges Konto` vs. the text's `richtige Konto`,
  `21:00` derived from "22:00, Kassenschluss 1 Stunde vorher") could never be
  found and silently lowered keyword coverage. Keywords in 21 existing rows
  were changed to the KB's wording (checked by script: no answerable row has a
  keyword missing from its sources).

#### 24.7.2 Content fixes

| id | Change | Why |
|---|---|---|
| 5 | Removed "Hochtarif/Niedertarif, zwei Zählwerke" | Not in the KB — only "HT/NT, beide Stände getrennt erfassen" |
| 9 | + source `Privatkunden_Strom_Ökostromtarif` | "toptarif-KLIMA plus" is only on that page |
| 18 | Removed "umweltschonend, 25 % weniger CO2" | The Erdgas page no longer says it; it now lists platzsparend, modern, sicher |
| 40 | Keyword `Kioskterrassen` → `Kiosk` | KB: "Terrassenbereiche am Kiosk" |
| 45–47 | Reference rewritten, `behaviour_test` → false | "Bleiben Sie in der Leitung / ich verbinde Sie" is phone-only; chat answer = individual check by Vertrieb, e-mail to vertrieb@, no percentages |
| 60, 86, 97 | `behaviour_test` → false | The behaviour part was voice-only (call transfer, speaking speed) |
| 75, 77, 80, 85, 104 | Voice parts removed from the reference | see above |
| 76 | Added the switching procedure (SWW cancels the old contract, own cancellation with a Sonderkündigungsrecht, start-date confirmation) | It is in `Privatkunden_Service_Umzugsservice` and was missing |
| 84 | `temporal` → `relationship` | Needs two facts from two files ("same hours for both Freibäder" + "Freibad Waiblingen 07–20 Uhr") |
| 87 | Reference rewritten: 14 days ahead, not retroactive, meter reading on moving day | The old list (Einzugsdatum, Anschrift, Zählernummer) was not in the KB, as the note itself asked to check |
| 88 | `answerable` → false | The KB has no typical household consumption values |
| 97 | Added "check the fuses, ask the neighbours" | KB `Störung` → "Allgemeine Hinweise zum Stromausfall" |
| 101 | + sources for "Messstellenbetreiber" | Term is defined in the KB; the phrase "abgelesen von MSB" is not |
| 102 | Kundencenter alternative grounded in `Wissensdatenbank_Abschaltung`; "per Post" dropped | Post was not in the KB |
| 103 | `answerable` → true, concrete answer | KB covers paying in the Kundencenter, the "already paid" check and Forderungsmanagement 131-160 |

#### 24.7.3 The 47 generated rows (ids 105–151)

`tester: "generated"`, `tester_result: ""` — filter on `tester` to score them
separately from the real customer questions. Written to fill the gaps of §24.2.6
and §24.3, each answer grounded in the cited files:

- **Pages no test covered before:** E-Mobilität/Carsharing, Wallbox, Balkon-PV,
  EEG & Einspeiser, Gasnetz, Planauskunft, Grund-/Ersatzversorgung, Baustrom,
  Fertigmeldung, Messstellenbetrieb/Smart Meter, Aktuelles, Betrugswarnung,
  Karriere, Umzug, Abschläge, Zahlung, Gasabrechnung, Preisinformation,
  Stromkennzeichnung, Trinkwasser (PFAS, Enthärtung, Löschwasser), mobile
  Heizzentralen, Contracting, Fernwärmeanschluss, Hallenbad (Parken, Wellpass),
  Gäste-WLAN, Abschaltung (Gas), Unternehmen.
- **Missing question types:** 4 `holistic` (all services, all contact routes,
  e-mail address per concern, all online services), plus `comparative` and
  `relationship` rows.
- **5 unanswerable rows** (prices for mobile Heizzentralen and the toptarif,
  Glasfaser for private homes, course dates) — the correct behaviour is to say
  the KB has no answer instead of inventing one.

#### 24.7.4 Composition now (151 rows)

| Category | n | | Other axes | n |
|---|---|---|---|---|
| `procedural` | 57 | | `answerable` true / false | 134 / 17 |
| `direct_fact` | 29 | | `behaviour_test` true | 10 |
| `temporal` | 16 | | `tester: generated` | 47 |
| `numerical` | 16 | | | |
| `spanning` | 15 | | | |
| `relationship` | 8 | | | |
| `comparative` | 6 | | | |
| `holistic` | 4 | | | |

#### 24.7.5 Contradictions found in the KB (not fixed here — fix at the source)

- **Bioerdgas.** `Privatkunden_Erdgas` says "Bioerdgasanteil von 10 %",
  `Geschäftskunden_Erdgas` says "10 % oder mehr" with the kunden-center
  address, while `Wissensdatenbank_Gasversorgung` and the testers (ids 45–47)
  want no percentages and vertrieb@. As long as the website text is in the
  corpus, a bot can retrieve the percentages.
- **Umzug deadline.** `Privatkunden_Service_Umzugsservice` gives both 23 May 2025
  and 6 June 2025 as the date from which retroactive moves stopped.
- **Headcount.** `Unternehmen`: 187 employees (31.12.2024); `Karriere`: "rund 180".
- **Two corpus pages were not refreshed by the 29.10. crawl**
  (`Netze_Stromnetz_Anmeldung_E-Ladestation`, 7 Sept;
  `Privatkunden_Strom_Ökostromtarif`, 14 Sept) — still in the allowlist, so the
  crawl probably failed on them. Re-crawl before trusting tests 9, 10, 107, 125.

---

## 25. Code Review Report — deterministic crawl4ai pipeline

**Branch:** `crawler-crawl4ai` (vs `main`) · **Reviewed:** 2026-07-08

**Scope:** `config.py`, `crawl.py`, `clean.py`, `monitor.py`, `uploader.py`,
`main.py`, `sites/waiblingen.yaml`, `tests/`, `.gitlab-ci.yml`, `pyproject.toml`,
docs. Method: five independent review passes (CLAUDE.md compliance, bug scan,
git-history audit, design-doc adherence, code-comment compliance), each finding
then verified against the code and against real crawl output in `outputs/raw/`.
Findings that did not survive verification were dropped.

### Summary

The rewrite is in good shape: the allowlist/Pydantic boundary, the pure
cleaning functions, the one-chunk-per-file upload contract, and the run
report all match their documentation and are unit-tested. The review found
**no bugs that affect a plain crawl run** on the current site. What it did
find: two real defects in the `--upload` failure paths that can silently
remove or gap knowledge-base content, two latent resolver/cleaner bugs that
only fire on *other* sites (relevant now that the tool is being shared), and
a handful of stale documentation references left over from the branch rename.

---

### High priority — upload failure paths

**1. A page that fails to crawl on a full `--upload` run is deleted from the
knowledge base** — `main.py:103` + `uploader.py:150` (`prune_stale`)

`main.py` builds the upload list from *this run's successes*
(`[p.name for p in pages if p.ok]`) and passes `prune=only is None`.
`prune_stale` treats every state key missing from that list as "removed from
the site YAML" and deletes the remote file. So one transient timeout on an
otherwise healthy page — on an unattended ~62-page scheduled run — wipes that
page's KB content and its `upload_state.json` entry. The docstring
("renamed/removed in the site YAML") describes intent; the implementation
conflates *not fetched this run* with *no longer wanted*. The KB stays gapped
until the next fully successful run (~24h with the scheduler).

*Fix:* only prune when the run had zero failures (`prune=only is None and not
failed`), or derive the prune set from the YAML allowlist instead of the run's
successes. **Status: fixed in this pass** (prune now skipped when any page
failed).

**2. Delete-then-upload leaves state pointing at a file that no longer
exists remotely** — `uploader.py:133` (`replace_upload`)

The old remote file is deleted first; if the subsequent upload fails twice,
`UploadHold` is raised with `state[page]` still recording the just-deleted
`file_id` and the *old* `sha256`. `main.py` persists that state. Until the
next run, the page has no content in the KB while `upload_state.json` claims
the old version is live — misleading for anyone inspecting the state file.
(It does self-heal on the next run: the sha differs, and re-deleting the gone
file_id is a tolerated 404.)

*Fix:* drop the state entry (or at least its `file_id`) immediately after a
successful delete, so persisted state always reflects what is actually live
remotely. **Status: fixed in this pass.**

**3. Risk note: `upload_state.json` lives in a GitLab CI *cache*, which is
best-effort** — `.gitlab-ci.yml:30`

If the cache is evicted, the next run sees no `file_id`s, skips every delete,
and re-uploads all ~62 pages — duplicating the whole site in the KB (the old
files can no longer be deleted because their ids are lost). Not a code bug,
but worth knowing operationally. *Mitigation ideas:* keep the artifact copy
(already exported, 30-day expiry) as a manual restore source; or add a
reconciliation step that lists remote KB files by name via the API, if the
API supports it. **Status: documented here; no code change.**

---

### Latent — will matter when colleagues point the tool at a new site

Both were verified as real against synthetic input and as *not occurring* on
any of the ~62 current Waiblingen pages.

**4. Link dedup keeps only the first anchor text per URL** —
`crawl.py:73-78` (`resolve_subpages`)

Links are deduplicated by `href`, first non-empty text wins. On a site where
a teaser card's first anchor to a page has generic text ("mehr", an image
caption) and a later anchor carries the real title, the matching text is
discarded and the YAML label reports `no link with text …` even though a
matching link exists. *Fix idea:* keep **all** (text, href) pairs and dedup
per label match instead of per href — `test_same_target_twice_is_not_ambiguous`
only covers the benign order. **Status: open.**

**5. Breadcrumb walk can merge two separate numbered lists** —
`clean.py:53-58` (`breadcrumb`)

Walking upward from the h1, the loop only stops at a non-blank, non-numbered
line *after* the first crumb is found; blank lines never stop it. Two numbered
lists separated by blanks (e.g. a numbered nav above the breadcrumb) are
concatenated into a false hierarchy. The Waiblingen template renders all nav
as bullets, so today's output is correct. *Fix idea:* stop the walk at the
first blank line once any crumb is collected, or bound it to N lines above
the h1. **Status: open.**

---

### Documentation — stale references (blocking for sharing)

**6. Phantom branch names.** `CLAUDE.md:13` says this branch is
`experiment/new-crawl-tool` and `CLAUDE.md:17` points to
`refactor/config-driven-crawler`; the branches were renamed and are now
`crawler-crawl4ai`, `crawler-llm-agent`, `main`. Same stale name in `PLAN.md:4`
and the `DEVLOG.md` §14 heading. A colleague following these pointers finds
nothing. **Status: fixed in this pass.**

**7. Wrong DEVLOG section number, twice.** `CLAUDE.md:19` and `CLAUDE.md:93`
cite `DEVLOG.md §13` for the crawl4ai rationale / PruningContentFilter
decision; that content is §14 (§13 is the upload stage). `README.md` cites §14
correctly. **Status: fixed in this pass.**

---

### Minor

**8. Stale docstring claims sub-pages are fetched concurrently** — `crawl.py:9`.
`crawl_section` awaits each fetch in a plain loop; sequential crawling is a
*deliberate* scope decision (PLAN.md: "correct first, fast later"), so the
docstring should say so rather than promise concurrency. **Status: fixed in
this pass.**

**9. `config.py` comments misattribute the h1 hierarchy** — `config.py:10,33`.
They say `path` "names outputs/hierarchy", but the clean file's `#` hierarchy
comes from the crawled page's own breadcrumb nav (`clean.breadcrumb`), falling
back to the URL path — never from `path`. `path` only names the output file.
**Status: fixed in this pass.**

**10. A missing `AIGATEWAY_KEY` reports as "failed twice"** — `uploader.py:87`.
`_headers()` raises inside the retry wrapper, so a plain configuration error
surfaces as `delete/upload failed twice: AIGATEWAY_KEY not set`. Cosmetic;
an upfront check in `upload_pages` would give a cleaner message.
**Status: open (cosmetic).**

---

### Test-coverage gaps (recommendations, not defects)

- `crawl._fetch` retry loop (`RETRIES = 1`, timestamps on failure vs success)
  is documented as a load-bearing reliability mechanism but has no test —
  `tests/test_crawl.py` only covers `resolve_subpages`.
- `main.py` has no tests: the `prune=…` wiring (finding 1) and the
  measure-old-before-overwrite ordering in `save_outputs` (the regression
  baseline) could both be silently broken by a refactor.

---

### What checked out (verified, no action needed)

- **Allowlist & validation:** targets live only in `sites/*.yaml`; Pydantic
  models use `extra="forbid"` and `load_site` fails loudly; covered by tests.
- **Cleaning:** `clean.py` is pure (no I/O); sentinels match the documented
  CMS template; links flattened, images dropped — no hyperlinks in clean
  output; clean h1 hierarchies verified correct across all current pages.
- **No `PruningContentFilter`/`fit_markdown`** anywhere, per the DEVLOG §14
  decision.
- **Chunking:** one chunk per file, `MAX_CHUNK=8192` / `SPLIT_OVERLAP=1000`,
  sha+params skip — matches docs, well-tested.
- **Logging:** single `logging.getLogger("crawler")`, `basicConfig` once in
  `main()`, no `print()`.
- **Dependencies:** `pyproject.toml` is minimal and matches actual imports;
  all LLM/MCP-era deps removed.
- **CI:** the updated `.gitlab-ci.yml` (commit `8f2190b`) matches the new
  pipeline — `uv sync --frozen`, `playwright install chromium`, `--sections`,
  masked `AIGATEWAY_KEY`, no `OPENAI_API_KEY`.

---

### Prioritized actions

1. ~~Stop pruning on runs with crawl failures~~ — **done** (finding 1).
2. ~~Clear stale `file_id` after delete succeeds in `replace_upload`~~ —
   **done** (finding 2).
3. ~~Fix stale branch names + DEVLOG §13→§14 references~~ — **done**
   (findings 6, 7).
4. ~~Fix stale/misleading comments in `crawl.py` and `config.py`~~ — **done**
   (findings 8, 9).
5. Harden `resolve_subpages` dedup and `breadcrumb` walk before onboarding a
   second site (findings 4, 5). **Effort: small.**
6. Add tests for `_fetch` retry and `main.py` wiring. **Effort: medium.**
7. Decide on an `upload_state.json` durability story for CI (finding 3).
   **Effort: decision + small.**

---

## 26. crawl4ai spike — summary & decisions

**Branch:** `crawler-crawl4ai` (formerly `experiment/new-crawl-tool`) · **Date:** 2026-07-06/07
**Goal:** evaluate [crawl4ai](https://docs.crawl4ai.com) (v0.9.0) as a
replacement for the current LLM-driven crawl layer (gpt-5-mini agent +
Playwright MCP), using the *Privatkunden/Strom* section as the test case —
**without any LLM and without any of our existing crawler code**.

### Motivation

- The current pipeline drives a browser with an LLM agent through the
  Playwright **MCP** subprocess. That brought recurring operational pain
  (CI browser installs, `PLAYWRIGHT_BROWSERS_PATH` stripping — see the last
  ~5 commits before this branch) and per-crawl LLM cost/nondeterminism.
- crawl4ai is a plain Python library (Playwright underneath, no MCP
  subprocess). Its **core is completely LLM-free**: fetch + render → HTML →
  markdown. LLM-based extraction exists (`LLMExtractionStrategy`) but is
  opt-in; we deliberately did not use it.
- Navigation in our pipeline had already been made deterministic
  (`subtopics` labels resolved to URLs), so the LLM's main remaining job —
  exploring — was no longer needed.

### What we built

```
experiments/
├── crawl_targets.yaml     # allowlist: which pages to crawl (data, not code)
├── crawl4ai_strom.py      # the whole spike (~190 lines, self-contained)
├── crawl4ai_out/
│   ├── raw/<page>.md      # full page as markdown, untouched conversion
│   └── clean/<page>.md    # noise-cut, link-free, hierarchy-titled
└── CRAWL4AI_SPIKE.md      # this file
```

Flow: load YAML → crawl each section's **base page** (saved as its own
output — it has real content) → resolve the listed sub-page labels against
the links crawl4ai extracted from that same fetch → `arun_many()` the
resolved URLs → write `raw/` + `clean/` markdown per page.

Dependency: `uv add crawl4ai` (pyproject + uv.lock updated on this branch);
browser via `uv run playwright install chromium`.

Result for Privatkunden/Strom: **6 pages in ~30 s, zero LLM calls** —
the base page + Ökostromtarif, Wärmestrom, Grundversorgung,
Preisinformation, Stromkennzeichnung.

### Key findings about crawl4ai

1. **Raw markdown quality is high.** Headings arrive as proper `#`/`##`/`###`
   in document order, download lists keep label + size, `tel:` links,
   addresses, opening hours and real markdown tables (Preisinformation's
   Umlagen table) all survive.
2. **Collapsed accordions come along for free.** crawl4ai converts the DOM,
   not the a11y snapshot, so Bootstrap-collapsed FAQ panels (questions *and*
   answers) are captured without our `scripts/expand_accordions.js`
   init-script hack. Verified on the Strom overview FAQ ("Wie setzt sich
   mein Strompreis zusammen?" etc. — full answers present).
3. **`PruningContentFilter` (fit_markdown) is not usable for this site.**
   See problem #1 below.
4. Deep-crawl (BFS + filters) works but we dropped it for an allowlist —
   see problem #3.

### Problems met & solutions

#### 26.1 `fit_markdown` pruned the wrong things
**Problem.** We first generated `fit_markdown` via
`PruningContentFilter(threshold=0.45, dynamic)`. It **dropped the section
headings** (`# Grundversorgung`, `## Downloads …`, `## Ersatzversorgung`)
but **kept the cookie-consent text**. Cause: the filter is a *statistical*
heuristic scoring DOM nodes by text/link density — headings are short and
download lists are link-dense (scored "noise"), while the cookie banner is a
fat prose block (scored "content"). Exactly backwards for a CMS site like
this; no threshold fixes a backwards signal, and it offers no "remove X"
rule.
**Solution.** Dropped `PruningContentFilter` entirely. Noise removal is done
by our own ~30-line `clean_markdown()` on the *raw* markdown, exploiting the
fact that every page of this one-template CMS renders as:
`[Sprungmarken/Menü/breadcrumb noise] → '# <title>' → ##/### sections →
[footer quick-links] → [cookie banner]`. We keep from the **first markdown
heading** to the **first footer/cookie sentinel** (`* [ Kontakt ](…/kontakt`
or "Wir nutzen Cookies und andere Technologien"). Deterministic, rule-based,
inspectable.

#### 26.2 Marketing h1s broke the page-hierarchy heading
**Problem.** Requirement: each output starts with its site hierarchy as the
single `#` heading (e.g. `# Privatkunden - Strom - Preisinformation`). First
attempt derived the last segment from the page's own `<h1>` — but some pages
use a *marketing* headline as h1 (`oekostrom` → "Unser bestes Angebot:
toptarif-KLIMA plus"), so "Ökostromtarif" vanished from the hierarchy.
**Solution.** The raw markdown contains the site's own breadcrumb nav as a
numbered list right above the h1 (`1. Startseite 2. Privatkunden 3. Strom
4. Ökostromtarif`). `breadcrumb()` walks up from the h1, collects that
block, drops "Startseite", joins with " - " (fallback: URL path segments).
The marketing h1, when it differs from the last crumb, is preserved as a
`##` directly below — no information lost, still exactly one h1 per file.

#### 26.3 Deep-crawl picked up unwanted pages (Kunden-Center)
**Problem.** The first version used `BFSDeepCrawlStrategy(max_depth=1)` with
a `*Privatkunden/Strom*` URL filter. It auto-discovered all 5 sub-pages —
but also `/Privatkunden/Strom/Kunden-Center-team774` (a contact-teaser
link). Excluding it with a reverse URL pattern would start an ever-growing
blocklist in code.
**Solution.** Replaced discovery with an **allowlist**:
`crawl_targets.yaml` claims `root_url`, each section's `path`, and its
`subpages` by **visible link text** (like the old `sites/*.yaml`, but
simpler). Labels are resolved deterministically against the links crawl4ai
already extracted from the base-page fetch (whitespace-collapsed,
case-insensitive match; unresolved labels print a loud
`!! no link with text … — skipped`). Unlisted pages are simply never
crawled. BFS/FilterChain code deleted.

#### 26.4 Base page crawled twice (depth 0 and 1)
**Problem.** Under BFS the start page also linked to itself (breadcrumb/nav
self-links), so it appeared in the results at depth 0 *and* depth 1.
**Solution.** Initially deduped by output filename; obsolete once the
allowlist flow landed (each URL is fetched exactly once, and the base-page
fetch doubles as content *and* link source).

#### 26.5 Hyperlinks/images unwanted in KB content
**Problem.** For a knowledge base, hypertext is noise: `[Kunden-Center](url)`
should be plain "Kunden-Center"; svg icon images and a glitchy empty link
`[](…)` (a duplicated `<a>` in the site's own HTML) polluted the output.
**Solution.** `strip_links()`: drop `![alt](url)` images entirely, flatten
`[text](url)` → `text`, remove bullets left empty, collapse blank runs.
The empty-link glitch disappears as a side effect ("" text → line removed).
**Trade-off, deliberate:** download sections now list *which* documents
exist but not their URLs. If the FAQ bot should hand out PDF links, add a
one-line exception in `strip_links` keeping links that point at
`/resources/`.

### Known leftovers (accepted for now)

- **Footer-template sections repeat per page.** `## So erreichen Sie uns` +
  `## Unsere Öffnungszeiten` are heading-led, so they're kept — identically
  on every page. Handle at chunking time (keep once per site or drop).
- **`Montag08:00 - 12:00 …`** — the `<dl>` day/time separator is lost in
  HTML→markdown conversion. Cosmetic; a small regex could reinsert `: `.
- **Stromkennzeichnung** leads with a long `©`-caption paragraph (the page
  presents its data as a chart with text fallback). Faithful to the page.
- Clean output is not yet chunked — plan is to split on `##`/`###` headings
  (the seams are already perfect for that), *later*, then feed the existing
  `uploader.py` mechanics.

### Verdict so far

For content-static pages, deterministic crawl4ai + rule-based cleaning
reproduced essentially everything the gpt-5-mini + `enrich.py` pipeline
recovers on this section — faster (~30 s), free, reproducible, and without
the MCP/CI browser plumbing. What crawl4ai does **not** replace: the
site-specific enrichment logic (we re-created a much smaller, markdown-level
version of it here) and the chunking/upload layer, which stays ours.

Next candidates: extend `crawl_targets.yaml` to more sections (Erdgas,
Netze, …), compare against `outputs/*.md` from the current pipeline,
decide on the `/resources/` link exception, then design the block chunker.

---

### Part 2 — productionization (2026-07-07)

The spike was promoted to **the** crawler on this branch; the LLM pipeline was
removed. The spike files referenced above no longer exist — the code moved to
the repo root:

| spike | production |
|---|---|
| `experiments/crawl4ai_strom.py` (one script) | `crawl.py` + `clean.py` + `main.py` |
| `experiments/crawl_targets.yaml` | `sites/waiblingen.yaml` (+ Pydantic `config.py`) |
| `experiments/crawl4ai_out/{raw,clean}/` | `outputs/{raw,clean}/` |
| `print()` progress | `logging` + `monitor.run_report` (Pushover) |

### Decisions finalized during productionization

1. **Filenames from YAML, not URLs.** Sub-pages living at off-section URLs
   (`/abschlag`, `/abrechnung-zahlung`, `/Netze/Stromnetz/Anmeldung-E-Ladestation`)
   are named `<section>_<label>.md` (e.g.
   `Privatkunden_Service_Abschläge_berechnen_verstehen.md`). The run log
   prints `name <- url` so off-section mappings stay visible.
2. **`url:` override per section.** Display path ≠ real URL for `Störung`
   (`/Störung` is 404; the page is `/notfallnummern`). Verified all standalone
   paths with curl; capitalized variants redirect fine.
3. **External pages excluded; Kundenportal is static.** The portal is a login
   app; the old LLM wrote a summary from its own knowledge. Now:
   hand-written `static/Kundenportal.md`, copied into `outputs/clean/` and
   uploaded like any page. Photovoltaik (new-waiblingen.de) and
   "Zur Planauskunft" (external form portal) stay excluded.
4. **Upload = one chunk per file, no overlap.** `chunk_params_for` returns the
   file's own length. The API hard-caps `max_characters` at **8192 chars**
   (discovered by a live test: 17990 → HTTP 422 `less_than_equal 8192`), so
   the ~4 oversized pages are sent at 8192 with a **1000-char overlap** and
   split by the API at structural boundaries. Verified live with `Kontakt.md`
   (1341 → one chunk) and `Netze_Uebersicht-Netze.md` (17990 → 8192/1000,
   re-uploaded on params change thanks to the sha+params skip).
5. **Pruning.** Full runs delete remote files whose local page vanished;
   partial runs (`--sections`) never prune (they'd wipe the rest of the KB).
   *Tightened 2026-07-08 (code review):* runs with **any failed page** don't
   prune either — a transient fetch failure is not "removed from the YAML".
6. **Retry + report.** Each fetch retried once; the run report lists every
   page ✓/✗/⚠ with reason, start time, duration, size — failures first so
   Pushover's 1024-char cap can't hide them. Full-site run: 62 ok / 0 failed
   in 123 s.
7. **`faq/` removed on this branch** (user decision); the old crawler and faq
   bot remain available on `main`.

Remaining docs: architecture + conventions in `CLAUDE.md`, history in
`DEVLOG.md` §14, current findings in DEVLOG §25.

---

## 27. November 2026: chunking, page headers, reranking, the Excel check

### 27.1 Test set: checked and extended to 151 (2026-11-06)

Detailed in §24.7: every row checked against `outputs/clean/`, `source` made a
real file name, keywords made verbatim (the metric is a substring match),
voice-only requirements removed (the bot is now evaluated as a text chatbot),
47 generated rows added (`tester: "generated"`), KB contradictions listed.

### 27.2 `llm` chunking: the LLM no longer copies text (2026-11-09)

The LLM retyped every page into `original_text`; 30 of 243 chunks were not
verbatim, 15 with real damage — one whole page with umlauts turned into
`\x00e4` (broken JSON escapes), soft hyphens inside words, invented typos.
Now the LLM returns only `headline`, `summary` and `start` (a chunk's first
words) and `cut_chunks` cuts the text from the page itself, adding
`OVERLAP_CHARS` overlap. `find_start` matches on words only (the LLM drops
`**`, `|`, `#` when copying a start) but still rejects broken characters. A
start that is not found makes `@retry` ask again; after 3 failures that page
falls back to the markdown splitter and the real reason is printed (not
`RetryError[<Future …>]`). First full run: 5 of 83 pages fell back; after the
words-only match, 4 of those 5 succeeded. Retrieval scores were no better than
the old `llm` method.

### 27.3 Two new methods: an LLM header on top of the whole page

`whole_summary` (headline + German summary) and `whole_questions` (headline +
3–5 customer questions, no numbers) put an LLM-written header above the
untouched `whole` page; the LLM never returns the page, so it cannot damage it.
All methods now live in one `METHODS` table in `ingest.py`; `--method all`
builds every collection. The two LLM methods have their own model constants,
`CHUNK_MODEL` and `SUMMARY_MODEL`.

Why not "bullet points of the exact content" in the summary: the content is
already in the chunk (no new signal for the embedding), and retyping prices
and numbers reintroduces exactly the copy errors of §27.2. Example questions
add what the page lacks — the customer's wording.

**Result (dashboard, 2026-11-09): `whole_summary` > `whole_questions` >
`whole`**, the first time a design change measurably beat the `whole`
baseline. Observed in the test: gpt-5-mini copied all prices into a summary
anyway — correct that time, but the risk the questions variant avoids.

### 27.4 Rewrite and rerank made results worse — why, and the fixes

- **Rewrite cannot change retrieval scores on its own.** `merge_chunks` appends
  the rewritten query's hits *after* the original 10 and `fetch_context` keeps
  `FINAL_K` = 5, so without rerank the top 5 are identical. Any difference seen
  with rewrite alone comes from answer generation, not retrieval.
- **Rerank** can only reshuffle a top 10 that is already good with
  `whole_summary` (it can only move a correct rank-1 page down); it was given
  10 whole pages (30–40k tokens) to rank; and its reply was not validated.
- **Fixed in `answer.py`:** `apply_order` drops unknown and repeated ids and
  appends chunks the reranker left out, so no chunk is lost or counted twice;
  `rerank_text` gives the reranker only the LLM header (everything before the
  page's `# ` title; ~10x shorter) and falls back to the first
  `RERANK_CHARS` (1500) characters for collections without a header;
  `RERANK_MODEL` lets the reranker model change without changing the answer
  model. Scores taken before these fixes are not comparable.

### 27.5 The Excel on `main` was older than production

Compared by git blob hash (`git hash-object` vs. `git rev-parse
<branch>:<path>`) and then cell by cell with openpyxl: `crawler-sw-waiblingen`
has 41 rows vs. 35 on `main` — new rows (Autostromvertrag with form,
Ladekarte, Kontaktdaten ändern, two Umzug rows, Rechnung fehlt,
Notfallnummern), the law firm "Dornbach" replaced by "Mack / Lang
Rechtsanwälte". **Bug in the production Excel: the "Mahnung – Mahngebühren"
cell holds the text of "Trotz regelmäßiger Abschlagszahlung"; the 4,00 Euro
fee is gone** — fix it in the Excel. Tests affected once the newer Excel is in
the corpus: 68–70 (law firms), 73 (Mahngebühren), 79 and 81 (become
answerable).

### 27.6 Evaluation runs are saved (2026-11-09)

Results used to live only in the browser. The dashboard's **Save results**
button (`evaluation/results.py`) writes each run to `evaluation/results/` as
JSON (the record, for comparing runs by script) plus Markdown (for reading,
worst questions first); PDF was rejected — not diffable, not machine-readable.
Per question it keeps the retrieved sources in rank order, missing keywords,
the generated answer and the judge's feedback: answers vary between runs and
the judge can be wrong, so a score without the answer text cannot be checked.
Each result is stored in `gr.State` *with the config it was run with*, so
changing the dropdown before saving cannot mislabel a file. `ingest.py` now
writes build info (method, time, LLM model) into the Chroma collection's
metadata, which the saved run reports. The folder is gitignored — worth
backing up, since it is the experiment history.

### 27.7 Multi-turn questions moved out of the test set

Every test is asked in a fresh session without history, so a question that
needs the previous turn can only fail — #34 ("kannst du mir die Erläuterung
ausgeben?") scored 0/5 keywords in every run. Three such questions were
found and moved to `evaluation/conversations.jsonl`, each with the turn it
depends on: #33→#34 (the reductions document), #57→#58 ("which meter value" —
alone it reads as a normal meter, where 2.8.0 is wrong), #87→#88 (household
consumption after the move-in registration; customer test Nr. 28 → Nr. 29).
`tests.jsonl`: 151 → 148. Ids were **not** renumbered, so saved results stay
comparable; because ids now have gaps, `eval.py` selects a test by `id`
instead of by 0-based position. The conversations are kept for a future
multi-turn runner (ask turns in order in one session, feed back the bot's own
answers, score each turn); the format is in `HANDOVER.md` Part D. Borderline
cases left in `tests.jsonl` because they name their topic: #10–12 ("sag mir
mehr zum …"), #56, #70, #92.

### 27.8 Docs merged into four files (2026-11-09)

Folder READMEs, `docs/code_review.md` and the spike doc drift out of date
separately and were hard to find; they became §21–26 here. `HANDOVER.md` is
now the user manual for every part, `README.md` the overview and setup.
`PLAN.md` moved to the personal, gitignored `learning/` folder.
