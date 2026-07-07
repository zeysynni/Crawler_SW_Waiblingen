# crawl4ai spike — summary & decisions

**Branch:** `experiment/new-crawl-tool` · **Date:** 2026-07-06/07
**Goal:** evaluate [crawl4ai](https://docs.crawl4ai.com) (v0.9.0) as a
replacement for the current LLM-driven crawl layer (gpt-5-mini agent +
Playwright MCP), using the *Privatkunden/Strom* section as the test case —
**without any LLM and without any of our existing crawler code**.

## Motivation

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

## What we built

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

## Key findings about crawl4ai

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

## Problems met & solutions

### 1. `fit_markdown` pruned the wrong things
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

### 2. Marketing h1s broke the page-hierarchy heading
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

### 3. Deep-crawl picked up unwanted pages (Kunden-Center)
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

### 4. Base page crawled twice (depth 0 and 1)
**Problem.** Under BFS the start page also linked to itself (breadcrumb/nav
self-links), so it appeared in the results at depth 0 *and* depth 1.
**Solution.** Initially deduped by output filename; obsolete once the
allowlist flow landed (each URL is fetched exactly once, and the base-page
fetch doubles as content *and* link source).

### 5. Hyperlinks/images unwanted in KB content
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

## Known leftovers (accepted for now)

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

## Verdict so far

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

# Part 2 — productionization (2026-07-07)

The spike was promoted to **the** crawler on this branch; the LLM pipeline was
removed. The spike files referenced above no longer exist — the code moved to
the repo root:

| spike | production |
|---|---|
| `experiments/crawl4ai_strom.py` (one script) | `crawl.py` + `clean.py` + `main.py` |
| `experiments/crawl_targets.yaml` | `sites/waiblingen.yaml` (+ Pydantic `config.py`) |
| `experiments/crawl4ai_out/{raw,clean}/` | `outputs/{raw,clean}/` |
| `print()` progress | `logging` + `monitor.run_report` (Pushover) |

## Decisions finalized during productionization

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
6. **Retry + report.** Each fetch retried once; the run report lists every
   page ✓/✗/⚠ with reason, start time, duration, size — failures first so
   Pushover's 1024-char cap can't hide them. Full-site run: 62 ok / 0 failed
   in 123 s.
7. **`faq/` removed on this branch** (user decision); the old crawler and faq
   bot remain available on `main`.

Remaining docs: architecture + conventions in `CLAUDE.md`, history in
`PLAN.md` Phase 10 and `DEVLOG.md` §14.
