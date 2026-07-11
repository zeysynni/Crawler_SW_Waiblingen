# Code Review Report — deterministic crawl4ai pipeline

**Branch:** `crawler-crawl4ai` (vs `main`) · **Reviewed:** 2026-07-08

**Scope:** `config.py`, `crawl.py`, `clean.py`, `monitor.py`, `uploader.py`,
`main.py`, `sites/waiblingen.yaml`, `tests/`, `.gitlab-ci.yml`, `pyproject.toml`,
docs. Method: five independent review passes (CLAUDE.md compliance, bug scan,
git-history audit, design-doc adherence, code-comment compliance), each finding
then verified against the code and against real crawl output in `outputs/raw/`.
Findings that did not survive verification were dropped.

## Summary

The rewrite is in good shape: the allowlist/Pydantic boundary, the pure
cleaning functions, the one-chunk-per-file upload contract, and the run
report all match their documentation and are unit-tested. The review found
**no bugs that affect a plain crawl run** on the current site. What it did
find: two real defects in the `--upload` failure paths that can silently
remove or gap knowledge-base content, two latent resolver/cleaner bugs that
only fire on *other* sites (relevant now that the tool is being shared), and
a handful of stale documentation references left over from the branch rename.

---

## High priority — upload failure paths

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

## Latent — will matter when colleagues point the tool at a new site

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

## Documentation — stale references (blocking for sharing)

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

## Minor

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

## Test-coverage gaps (recommendations, not defects)

- `crawl._fetch` retry loop (`RETRIES = 1`, timestamps on failure vs success)
  is documented as a load-bearing reliability mechanism but has no test —
  `tests/test_crawl.py` only covers `resolve_subpages`.
- `main.py` has no tests: the `prune=…` wiring (finding 1) and the
  measure-old-before-overwrite ordering in `save_outputs` (the regression
  baseline) could both be silently broken by a refactor.

---

## What checked out (verified, no action needed)

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

## Prioritized actions

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
