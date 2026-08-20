# Handover: keeping the FAQ knowledge base up to date

This project refills the FAQ bot's knowledge base **automatically, once a week**.
Nobody has to run anything on a laptop, and **you do not need to know git.**

Everything below is done in a web browser.

---

## What runs by itself every week

The scheduled pipeline in GitLab does three things, in this order:

1. **Converts the source documents** in `PDFs/` and `Excels/` into knowledge-base
   markdown (`static/*.md`).
2. **Crawls** the ~62 allowed pages of stadtwerke-waiblingen.de and cleans them.
3. **Uploads everything** to the knowledge base — replacing each file, and
   deleting anything that is no longer produced.

So the knowledge base always matches what is in this repository plus what is on
the website. You never edit the knowledge base by hand; if you do, the next
weekly run will overwrite it.

You get a **Pushover message** after every run saying what was uploaded and
whether anything failed.

---

## Task 1: a tariff sheet or other Bäder PDF was reissued

Example: `Tarifübersicht Freibäder 2026.pdf` becomes `… 2027.pdf`.

1. Open the project in GitLab and click into the **`PDFs`** folder.
2. If the old PDF is being *replaced*, delete it first: click it, then
   **Delete** (top right) → **Commit changes**.
3. Back in the `PDFs` folder, click the **`+`** button → **Upload file**.
4. Drag the new PDF in, then click **Upload file**.

That's it. The next weekly run converts it and updates the knowledge base.

> **Keep the umlauts and spaces in the filename** exactly as the document is
> called — the file name becomes the document's title in the knowledge base. The
> pipeline handles the conversion to a safe file name itself.

## Task 2: the colleagues' Excel knowledge base changed

The `Knowledge Base.xlsx` in `Excels/` is a copy of the SharePoint file. To
refresh it:

1. In SharePoint, download the file: the **`⋯`** menu on the file → **Download**
   (*not* Export — see `Excels/README.md` §3.1 for why Export produces an
   unusable file).
2. In GitLab, click into the **`Excels`** folder → **`+`** → **Upload file**.
3. Drag the downloaded `.xlsx` in. Keep the name **`Knowledge Base.xlsx`**;
   GitLab will replace the old one.
4. Click **Upload file**.

The sheet's two columns must stay named **`Thema / Kategorie`** and
**`Inhalt / Wissen`** — the converter looks for those labels and stops with a
clear error if they are gone, rather than uploading empty files. Adding, editing
or removing rows is always fine.

## Task 3: a hand-written page needs a correction

Pages that cannot be crawled are written by hand in **`static/`** (currently
`Kundenportal.md`).

1. Click into `static/` and click the file.
2. Click **Edit** → **Edit single file**.
3. Change the text, then **Commit changes** at the bottom.

Do not edit the other files in `static/` — everything named
`Privatkunden_Baeder_*` or `Wissensdatenbank_*` is generated from `PDFs/` and
`Excels/`, and your changes would be overwritten on the next run. Fix the source
document instead (Task 1 or 2).

## Task 4: a page should be added, removed or renamed on the website side

The list of crawled pages lives in **`sites/waiblingen.yaml`**. Each entry is a
page path plus the **visible link text** of its sub-pages. Editing it is the same
flow as Task 3 (click the file → **Edit**), but read the comments in the file
first — a label that doesn't match the website's link text shows up as a failure
line in the run report rather than being guessed at.

---

## Don't wait for the weekly run

Uploading a file does **not** start the pipeline — the schedule does. To apply a
change immediately:

**Build → Pipelines → Run pipeline** → leave the branch as-is → **Run pipeline**.

It takes a few minutes. Watch the job output, or wait for the Pushover message.

---

## When something goes wrong

The run report (in the job log, and shortened in Pushover) has one line per page:
`✓` fine, `✗` failed, `⚠` suspiciously smaller than last week.

| What you see | What it means | What to do |
|---|---|---|
| The job fails on `pdf2md.py` with *"converted to markdown with no table"* | A reissued tariff PDF lost the ruled lines around its prices, so they would land in the bot as loose prose. **The pipeline stopped on purpose — nothing was uploaded.** | This needs a developer. The knowledge base still has last week's correct version, so there is no rush. |
| The job fails on `xlsx2md.py` with *"has the sheet layout changed?"* | The two column headers in the Excel were renamed or moved. | Restore the column names `Thema / Kategorie` and `Inhalt / Wissen` and re-upload. |
| `upload HOLD: …` in Pushover | The knowledge-base API was unreachable twice in a row. | Nothing. The next run reconciles everything by itself. If it repeats for days, get a developer. |
| A few `✗` lines, run otherwise fine | Those pages failed to fetch. Deleting is skipped whenever any page fails, so nothing was lost from the knowledge base. | Nothing, if the next run is clean. If the same page keeps failing, its link text in `sites/waiblingen.yaml` probably changed on the website. |

**The knowledge base is never left half-updated.** Files are replaced one by one,
and deleting old files only happens on a complete run in which every single page
succeeded.

---

## For a developer picking this up

- `CLAUDE.md` — architecture and conventions, start here.
- `DEVLOG.md` — why things are the way they are, chronologically.
- `PDFs/README.md`, `Excels/README.md` — the two converters in detail, including
  the failure modes that shaped them (§2 of each is worth reading before
  changing extraction).
- `uv sync --group convert && uv run pytest` — the converters need the `convert`
  dependency group; `uv sync` alone omits it.
