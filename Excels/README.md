# Excel → knowledge-base markdown (one-off conversion)

This folder holds the colleagues' knowledge-base spreadsheet and the script that
converted it, `xlsx2md.py`. Like `PDFs/`, it is self-contained: the crawler
itself is untouched, so nothing here affects a normal crawl.

Written 2026-08-18. Companion to `DEVLOG.md` (the crawler's own history) and to
`PDFs/README.md`, which documents the same pattern for PDF sources.

---

## 1. What was done

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

### How to run it

```bash
uv run --with openpyxl python Excels/xlsx2md.py
```

`openpyxl` is **not** a project dependency: one-time job, so it is pulled in per
invocation and `pyproject.toml` stays as minimal as the crawler's conventions
demand.

### Output shape

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

## 2. Decisions, and why

### 2.1 One file per group — not one file, and not one per row

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

### 2.2 `Wissensdatenbank` as the h1 root

The crawled pages use roots from the site's own navigation (`Privatkunden`,
`Netze`). These rows are the colleagues' supplementary knowledge rather than
website content, and some of it is not strictly Privatkunden material, so they
carry their own root. This also keeps the bot's sources distinguishable when
reviewing what it answered from.

### 2.3 Output goes to `static/`, never `outputs/clean/`

Same reasoning as `PDFs/README.md` §2.2: `main.py` builds the upload list from
*crawled pages + `static/*.md` only*, `outputs/` is gitignored and rebuilt each
run, and `prune_stale` deletes remote files not on that list. `static/` is the
supported route (it is how `Kundenportal.md` ships).

### 2.4 ASCII filenames

`ascii_name()` transliterates the German way (`ä`→`ae`, `ß`→`ss`), so
`Elektromobilität` becomes `Wissensdatenbank_Elektromobilitaet.md`. The
knowledge base is keyed **by filename** and the uploader deletes/replaces by it,
so a name whose Unicode normalisation form differs between macOS and the CI
runner would orphan the old remote file and upload a duplicate. Full explanation
of the NFC/NFD trap — which cost real debugging time on the PDFs — is in
`PDFs/README.md` §2.4.

The function is **deliberately duplicated** in both folders rather than shared,
to keep each one-off conversion self-contained. Only `clean.slug` and
`clean.strip_links` are imported from the pipeline.

---

## 3. Notes on the source data

### 3.1 Export as PDF or CSV is the wrong way to get this file

The first attempt used SharePoint's **Export** menu, and both offered formats
came out unusable:

- **PDF export** is laid out for *printing* — page breaks mid-table, headers
  repeated per page, wide columns cut off. It also throws away all cell
  structure, which then has to be reconstructed by heuristics (see
  `PDFs/README.md` for how much work that is).
- **CSV export** takes one sheet only, drops merged cells and any meaning
  carried by formatting, and German Excel writes it with **semicolon**
  delimiters and **comma** decimal separators — which looks like one garbled
  column in a viewer expecting commas.

The fix is to take the raw `.xlsx` via **Download** (the `⋯` menu on the file, or
*File → Create a Copy → Download a Copy*), which is a different menu from
`Export`. The `.xlsx` keeps sheets, cells, types and cached formula values, and
`openpyxl` reads it directly with no export step to mangle anything.

**Source-quality ranking: `.xlsx` → `.csv` → `.pdf`.**

### 3.2 The sheet layout is found, not assumed

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

### 3.3 Hand-typing artifacts

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

### 3.4 Personal-data check

Because this file came from the customer and the knowledge base feeds a
customer-facing bot, the content was scanned before conversion. **No personal
data:** no IBANs, no phone numbers, no customer names, no identifier digit runs.
The only email addresses are company ones (`einspeiser@`,
`vertrieb@stadtwerke-waiblingen.de`) which are part of the answers themselves and
belong in the KB. Re-check this if a revised file ever arrives.

---

## 4. What was verified

- **All 18 source rows present** in their output file, compared
  whitespace-insensitively; all 18 sub-topic headings present.
- **8 groups, 18 topics** — the totals the script reports match the sheet.
- **Filenames pure ASCII** for all 8 files.
- **Pipeline integration**: `main.py:copy_static()` returns 14 pages
  (`Kundenportal` + 5 from `PDFs/` + these 8), and `uploader.chunk_params_for`
  confirms every one is a single chunk.

---

## 5. If a revised file arrives

The customer called this final, so no automation was built — a Graph API fetch
and a CI secret would be pure maintenance burden for a file that does not change.
If a revision does come:

1. Replace `Knowledge Base.xlsx` in this folder.
2. Re-run `uv run --with openpyxl python Excels/xlsx2md.py`.
3. **Read the output**, and check whether any group name changed — a renamed
   group produces a new filename and leaves the old one behind in `static/`.
   Delete stale `static/Wissensdatenbank_*.md` before committing.
4. Commit, then run a **full** `--upload` (not `--sections`): `prune_stale` only
   runs on a full run with zero failures, and it is what removes the superseded
   remote file.

### Known limitations

- **Group names come from the text before the dash.** A topic typed without the
  ` – ` separator becomes its own single-topic group; an inconsistent prefix
  (`Einspeiseanlage` vs `Einspeiseanlagen`) would silently create two groups. The
  per-group topic counts in the log are the quickest way to spot that.
- **No unit tests.** `read_rows`, `group_rows`, `render` and `ascii_name` are all
  pure and testable, and would fit `tests/` if this stops being a one-off.
- **Column D exists but is empty** in the current file and is ignored. A third
  column added later would need a decision about how to render it.
