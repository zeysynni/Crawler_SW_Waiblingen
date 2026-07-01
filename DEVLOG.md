# DEVLOG — LLM Web Crawler

A detailed log of the work done on this crawler: what we built, the problems we
hit, the decisions we made, and *why*. For the roadmap see `PLAN.md`; for
day-to-day guidance see `CLAUDE.md`. This file keeps the details.

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

## 8. Commit reference (this work, on `refactor/config-driven-crawler`)

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
