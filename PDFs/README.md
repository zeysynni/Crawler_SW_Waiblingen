# PDF → knowledge-base markdown

This folder holds four Stadtwerke Waiblingen **Bäder** PDFs and the script that
converts them to knowledge-base markdown, `pdf2md.py`. The crawler itself is
untouched by this work — `pdf2md.py` only writes into `static/`, which the
pipeline already uploads.

Written 2026-08-13 as a one-off conversion; **since 2026-08-20 it runs in CI
before every weekly crawl**, so replacing a PDF in this folder is the whole
update procedure (§4, and `HANDOVER.md` for the browser-only version). Companion
to `DEVLOG.md` (the crawler's own history) — this file documents only the PDF
side.

---

## 1. What was done

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

### How to run it

```bash
uv run python PDFs/pdf2md.py
```

Runs automatically in CI before every weekly crawl (`.gitlab-ci.yml`) — see §4.
Run it locally only to check a conversion by hand.

`pdfplumber` is a **locked dependency in the `convert` group**, so `uv sync
--group convert` is needed once (plain `uv sync` omits it, keeping the crawler's
own runtime deps as minimal as its conventions demand). It was originally pulled
in per-invocation with `uv run --with`, which stopped being right when this
became a weekly automated job: an unpinned extraction library that changes
behaviour between runs would silently rewrite prices in a customer-facing bot.

### Output shape

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

## 2. Problems met, and how they were solved

Every one of these was found by inspecting real output against the source PDFs,
not by reasoning ahead of time.

### 2.1 `pymupdf4llm` silently corrupted the prices

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
a real text layer* (see the table in §1). OCR was never needed; the extension
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

### 2.2 Writing to `outputs/clean/` would have broken the upload

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
exactly this (it is how `Kundenportal.md` ships). `main.py` still performs the
`outputs/clean/` step itself, so the end result is identical — via the path that
survives a CI run.

### 2.3 The page border was detected as a table containing the whole page

`find_tables()` returned **two** tables for each tariff sheet: the real one, and
a bogus outer "table" formed by the page's border box, holding the entire page
as one giant cell. Both were emitted, so the tariff sheet appeared twice — once
as an unreadable single-row blob, once correctly.

**Solution:** `_encloses()` — any detected table whose bounding box contains
another table's box is dropped in favour of the inner one. A general rule, not a
per-file patch.

### 2.4 Umlauts in filenames: the NFC / NFD trap

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

### 2.5 Hyphen spacing in the justified WLAN document

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

### 2.6 German line-break hyphenation

Words split across lines needed two different treatments, distinguished by the
case of the continuation:

| Source | Rule | Result |
|---|---|---|
| `ver-` + `antwortlichen` | lowercase → drop the hyphen | `verantwortlichen` |
| `Gäste-WLAN-` + `Zugangs` | uppercase → **keep** the hyphen | `Gäste-WLAN-Zugangs` |

A capital continuation means a genuine German compound hyphen, not syllable
hyphenation.

### 2.7 An empty trailing heading

The tariff sheets set their validity period (`Saison 2026/2027`) in heading type
at the foot of the page, so it became a `##` section with no content under it.
`_demote_empty_headings()` turns any heading with nothing beneath it back into
plain text — the information is kept, the empty section is not.

### 2.8 The WLAN document exceeded the API's chunk cap

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

### 2.9 Stale output accumulating in `static/`

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

## 3. What was verified

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

## 4. Maintaining this

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
  point of view. This retires §2.9 and the old "delete the superseded file first"
  chore.
- **Conversion runs before the crawl and fails the job**, so a bad conversion
  never reaches the upload step. `static/` is only touched after every PDF has
  converted successfully, so a mid-way failure cannot leave a short set behind
  for `prune_stale` to act on.

### The degradation guard

The limitation below used to be answered by "read the generated markdown". Since
nobody does that on a scheduled run, it is now enforced: a document whose title
matches `TABLE_REQUIRED` (`Tarif`, i.e. the tariff sheets) and whose output
contains no markdown table **exits 1 with a loud error**. Prices arriving as
loose prose is the one failure that looks plausible and is wrong, so it stops the
pipeline; last week's correct version stays in the knowledge base until someone
looks. Covered by `tests/test_convert.py`.

### Known limitations

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
