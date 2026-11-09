# Handover — how to use every part

The manual for this repository. Part A needs only a web browser and no git; parts
B–D are for whoever runs the code on a laptop.

| Part | For | What |
|---|---|---|
| **A** | anyone maintaining the knowledge base | browser-only tasks in GitLab |
| **B** | developer | crawler and converters on a laptop |
| **C** | developer | the FAQ bot: build vector stores, chat |
| **D** | developer | the test set and the evaluation dashboard |

The other docs: `README.md` (what this is, setup), `DEVLOG.md` (why things are the
way they are), `CLAUDE.md` (guidance for Claude Code).

---

# Part A — Keeping the knowledge base up to date (browser only)

The production pipeline refills the company knowledge base **automatically,
once a week**, from the `main` branch of the **GitLab** project. Nobody has to run
anything on a laptop, and **you do not need to know git.**

## What runs by itself every week

The scheduled pipeline in GitLab does three things, in this order:

1. **Converts the source documents** in `PDFs/` and `Excels/` into knowledge-base
   markdown (`static/*.md`).
2. **Crawls** the ~62 allowed pages of stadtwerke-waiblingen.de and cleans them.
3. **Uploads everything** to the knowledge base — replacing each file, and
   deleting anything that is no longer produced.

So the knowledge base always matches what is in the repository plus what is on
the website. You never edit the knowledge base by hand; if you do, the next
weekly run will overwrite it.

You get a **Pushover message** after every run saying what was uploaded and
whether anything failed.

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
   (*not* Export — Export produces an unusable file, `DEVLOG.md` §22.3.1).
2. In GitLab, click into the **`Excels`** folder → **`+`** → **Upload file**.
3. Drag the downloaded `.xlsx` in. Keep the name **`Knowledge Base.xlsx`**;
   GitLab will replace the old one.
4. Click **Upload file**.

The sheet's two columns must stay named **`Thema / Kategorie`** and
**`Inhalt / Wissen`** — the converter looks for those labels and stops with a
clear error if they are gone, rather than uploading empty files. Adding, editing
or removing rows is always fine. Check each cell's text before uploading: the
converter cannot tell when a cell holds the wrong answer (2026-11: the
"Mahnung – Mahngebühren" cell held the text of another row).

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
line in the run report rather than being guessed at. Format: Part B, "Adding or
changing crawl targets".

## Don't wait for the weekly run

Uploading a file does **not** start the pipeline — the schedule does. To apply a
change immediately:

**Build → Pipelines → Run pipeline** → leave the branch as-is → **Run pipeline**.

Optionally set the variable `SECTIONS` to e.g. `kontakt,Privatkunden_Strom` to run
only those sections (a partial run never deletes anything). Leave it empty for a
full run. **Never** put a key or password into these variables — the upload key
lives masked in *Settings → CI/CD → Variables*.

It takes a few minutes. Watch the job output, or wait for the Pushover message.

## When something goes wrong

The run report (in the job log, and shortened in Pushover) has one line per page:
`✓` fine, `✗` failed, `⚠` suspiciously smaller than last week.

| What you see | What it means | What to do |
|---|---|---|
| The job fails on `pdf2md.py` with *"converted to markdown with no table"* | A reissued tariff PDF lost the ruled lines around its prices, so they would land in the bot as loose prose. **The pipeline stopped on purpose — nothing was uploaded.** | This needs a developer. The knowledge base still has last week's correct version, so there is no rush. |
| The job fails on `xlsx2md.py` with *"has the sheet layout changed?"* | The two column headers in the Excel were renamed or moved. | Restore the column names `Thema / Kategorie` and `Inhalt / Wissen` and re-upload. |
| `upload HOLD: …` in Pushover | The knowledge-base API was unreachable twice in a row. | Nothing. The next run reconciles everything by itself. If it repeats for days, get a developer. |
| A few `✗` lines, run otherwise fine | Those pages failed to fetch. Deleting is skipped whenever any page fails, so nothing was lost from the knowledge base. | Nothing, if the next run is clean. If the same page keeps failing, its link text in `sites/waiblingen.yaml` probably changed on the website. |
| `label '…' is ambiguous` | Two links on the base page carry that text. | Make the sub-page its own section with a `path` (Part B). |

**The knowledge base is never left half-updated.** Files are replaced one by one,
and deleting old files only happens on a complete run in which every single page
succeeded.

---

# Part B — Crawler and converters on a laptop

## Branches — where the code lives

| Branch | What it is |
|---|---|
| `crawler-sw-waiblingen` | the **production** crawler. Pushed to GitLab `main`, where the weekly job runs. Crawler fixes go here. |
| `main` (GitHub) | a copy of the crawler (2026-09-12) **plus** the FAQ bot and the evaluation (parts C, D) |
| `crawl4ai-crawler` | the general, multi-site crawler |

GitHub `main` and GitLab `main` are different histories — never push one onto
the other, and never force-push. Route to production: GitHub
`crawler-sw-waiblingen` → work laptop → GitLab `main`. Details: `DEVLOG.md` §20.

## Setup (once)

```bash
uv sync --group convert --group bot     # crawler + converters + FAQ bot
uv run playwright install chromium      # browser for crawl4ai
cp .env.example .env                    # keys, see below
```

| `.env` key | Needed for |
|---|---|
| `OPENAI_API_KEY` | parts C and D (embeddings, LLM calls) |
| `AIGATEWAY_KEY` | `--upload` only |
| `PUSHOVER_TOKEN`, `PUSHOVER_USER` | optional phone alerts |

Plain `uv sync` gives a working crawler without the converters and the bot.

## Every command

| Command | Network | Writes | Safe to repeat |
|---|---|---|---|
| `uv run python Excels/xlsx2md.py` | no | `static/Wissensdatenbank_*.md` | yes |
| `uv run python PDFs/pdf2md.py` | no | `static/Privatkunden_Baeder_*.md` | yes |
| `uv run python crawler/main.py` | the website | `outputs/raw/`, `outputs/clean/` | yes |
| `uv run python crawler/main.py --sections A,B` | the website | the same, for those sections only | yes |
| `uv run python crawler/main.py --config sites/x.yaml` | the website | the same, for another site | yes |
| `uv run python crawler/main.py --upload` | the website **+ aigateway.eu** | the above **+ deletes and rewrites live knowledge-base files** | ⚠️ see below |
| `uv run pytest` | no | nothing | yes |

**Order matters.** The converters are not part of `crawler/main.py` (CI runs them
first). `main.py` copies `static/` into `outputs/clean/`, so after changing a PDF
or the Excel locally:

```bash
uv run python PDFs/pdf2md.py        # or Excels/xlsx2md.py — regenerate static/
uv run python crawler/main.py       # crawl; collects static/ as well
```

Both converters work offline in seconds and exit non-zero on a degraded
conversion, so a bad conversion fails loudly instead of reaching the corpus.

After a crawl, `outputs/clean/` is the complete corpus (crawled pages + `static/`
pages, one file per page). `outputs/` is never cleaned, only overwritten: delete
it before a crawl whose output feeds the FAQ bot, or leftovers from other
branches end up in the vector store.

## ⚠️ What `--upload` does

The **only** command that reaches outside the repository. Normally CI runs it,
not you. It:

1. lists the live knowledge base (needs `AIGATEWAY_KEY`);
2. **prunes** — deletes every remote file this run did not produce;
3. **replaces** every page — deletes all remote copies of that filename, then
   uploads the fresh `.md`.

No dry-run, no undo. Pruning only happens on a **full** run with **zero** failed
pages. The risk it cannot catch is a **stale checkout**: fewer pages locally means
the difference is deleted remotely. Upload only from an up-to-date
`crawler-sw-waiblingen`, never from `main` or an experiment. The FAQ bot never
needs `--upload`.

## Adding or changing crawl targets

Edit `sites/waiblingen.yaml` — no code changes:

```yaml
sections:
  - path: Privatkunden/Strom      # base page (crawled itself) + output name
    subpages:                     # sub-pages by their visible link text
      - Ökostromtarif
      - Grundversorgung
  - path: Störung                 # display name ...
    url: notfallnummern           # ... fetched from a different URL
```

If a label matches no link, or several, the run report says so — fix the label
or give the sub-page its own `path` section; don't add code.

---

# Part C — The FAQ bot

Reads `outputs/clean/*.md`, embeds it into a local Chroma store, answers through
a Gradio chat. A personal learning project: it never touches the company
knowledge base. Code: `faq_bot/pro_implementation/` (`implementation/` is the
simpler course version, kept as a baseline).

## 1. Build the vector stores

Run from `faq_bot/pro_implementation/`. **The stores are not in git**
(`preprocessed_db/` is gitignored) — build them after every fresh clone and
after every change to the corpus.

```bash
uv run python ingest.py --method whole_summary   # one method
uv run python ingest.py --method all             # all six
uv run python ingest.py --list                   # collections + chunk counts
uv run python ingest.py --delete NAME            # remove one collection
```

| `--method` | Collection | What a chunk is | Cost |
|---|---|---|---|
| `whole` | `whole_file` | one page | embeddings only, seconds |
| `markdown` | `markdown` | LangChain markdown splitter | embeddings only |
| `recursive` | `recursive_Chunksize_500_Overlap_100` | 500 chars, 100 overlap | embeddings only |
| `llm` | `llm_chunks` | the LLM picks split points + writes headline/summary; text is cut from the page | 1 LLM call per page |
| `whole_summary` | `whole_file_summary` | `whole` + LLM headline and summary on top — **best so far** | 1 LLM call per page |
| `whole_questions` | `whole_file_questions` | `whole` + LLM headline and 3–5 customer questions on top | 1 LLM call per page |

Each method has its own collection, so all six coexist and re-running one only
rebuilds that one. The LLM methods print how many pages fell back (`llm` →
markdown splitter, header methods → page without header) and why.

## 2. Chat with it

```bash
cd faq_bot
uv run python app.py
```

The chat uses `RagConfig`'s defaults in `pro_implementation/answer.py` (which
collection, rewrite on/off, rerank on/off) — change them there. The app launches
with `share=True`: while it runs, anyone with the printed `gradio.live` link can
use it on your OpenAI key. Stop it with `Ctrl+C`.

## 3. The settings you will change

| What | Where |
|---|---|
| Model for `llm` chunking / for the page headers | `ingest.py: CHUNK_MODEL`, `SUMMARY_MODEL` |
| Model for answering, query rewrite | `answer.py: MODEL` |
| Model for reranking | `answer.py: RERANK_MODEL` |
| Chunks retrieved / passed to the model | `answer.py: RETRIEVAL_K` (10), `FINAL_K` (5) |
| Chunk size and overlap (`recursive`), overlap (`llm`) | `ingest.py: CHUNK_SIZE`, `CHUNK_OVERLAP`, `OVERLAP_CHARS` |
| The answering prompt (tone, company, "say when you don't know") | `pro_implementation/prompts.py` — the **only** prompt meant to be edited |

Model names need the `openai/` prefix (they go through LiteLLM). The embedding
model (`text-embedding-3-large`) is named in both `ingest.py` and `answer.py`;
they must match. The other prompts (rewrite, rerank, chunking, judge) sit next to
the code that parses their output — rewording them can break parsing.

---

# Part D — The test set and the evaluation

## The test set: `evaluation/tests.jsonl`

151 questions, one JSON object per line: 82 from the colleagues' manual test
log, 22 from the Oct/Nov 2026 customer tests, 47 generated (`"tester":
"generated"`). The bot is evaluated as a **text chatbot**; voice-only remarks in
the `note` field are history, not requirements.

```json
{"id": 1, "question": "wann ist die Hallenbadsaison?", "keywords": ["Hallenbadsaison", "16. September", "30. April"], "reference_answer": "…", "category": "temporal", "source": ["Privatkunden_Baeder"], "answerable": true, "behaviour_test": false, "tester": "Zeyuan", "tester_result": "success", "note": ""}
```

Rules when adding or editing a row:

- **`question`** verbatim, typos included — real users type that way.
- **`keywords`** (2–5) must appear **word for word** in the `source` pages: the
  retrieval metric is a case-insensitive substring match, so a paraphrased
  keyword can never be found and silently lowers the score.
- **`reference_answer`** grounded in `outputs/clean/`; never invent a fact. If
  the KB has no answer: `answerable: false` and describe the right behaviour.
- **`source`** = file names in `outputs/clean/` without `.md`.
- **`category`** one of `direct_fact`, `temporal`, `spanning`, `comparative`,
  `numerical`, `relationship`, `holistic`, `procedural` (a typo makes a new bar
  in the charts).
- **`behaviour_test: true`** for tone/handling tests — exclude them from
  retrieval scores.
- UTF-8, one valid JSON object per line; the loader stops at the first bad line.

Only `question`, `keywords`, `reference_answer` and `category` are read by the
code. A new round of colleague tests: `DEVLOG.md` §24.5 (and the traps in it).

## Running the evaluation

From `evaluation/`:

```bash
uv run python evaluator.py      # dashboard
uv run python eval.py 3         # one question (0-based) in the terminal: 2 LLM calls
```

The dashboard's top row selects the **collection** (filled from Chroma, with the
chunk count as a fingerprint), **query rewriting** and **reranking**. Its tabs:

| Tab | Shows | LLM calls per run |
|---|---|---|
| Retrieval | MRR, nDCG, keyword coverage, MRR by category | 0 with both switches off; +1 per question for each switch |
| Answer | judge scores (accuracy, completeness, relevance) by category | 2 per question (answer + judge) |
| Chunk Map | t-SNE map of the chunks, coloured by page category | 0 |

Sensible order: compare collections with both switches off (free), then try the
switches on the winner. Like the chat app, the dashboard launches with
`share=True`.

**Save every run you want to keep:** after Retrieval and/or Answer, click
**💾 Save results**. It writes to `evaluation/results/` (gitignored):

- `<date>_<collection>_rw-on|off_rr-on|off.json` — the record: settings (chunk
  count, when and with which LLM the collection was built, answer/rerank/judge
  models, `RETRIEVAL_K`, `FINAL_K`, git commit), averages, per-category scores,
  and **every question** with its retrieved sources, missing keywords,
  generated answer and the judge's feedback and scores.
- the same `.md` — for reading; questions sorted worst first.

What gets saved is the configuration each result was *run* with, not what the
dropdown shows now. Retrieval and Answer run with different settings are saved
as two runs. Collections built before 2026-11-09 show "unknown" for build time
and LLM — rebuild them to get it recorded.

Two things to know when reading the numbers:

- **Rewrite alone cannot change retrieval scores** — its extra hits are appended
  after the original top 10 and only the top 5 are kept. It matters only
  together with rerank. (`DEVLOG.md` §27.4)
- **Scores are only comparable within the same corpus, models and code.** A new
  crawl, a model change or a rebuilt collection means measuring the baseline
  again.

The judge model is `evaluation/eval.py: MODEL` (`openai/gpt-4.1`) — keep it
stronger than the answering model.

---

# What to re-run after a change

| You changed | Re-run |
|---|---|
| a PDF or the Excel | its converter → `crawler/main.py` → `ingest.py` |
| `sites/*.yaml`, or the website changed | `crawler/main.py` → `ingest.py` |
| `static/Kundenportal.md` | `crawler/main.py` → `ingest.py` |
| a chunking method, size, overlap or `CHUNK_MODEL`/`SUMMARY_MODEL` | `ingest.py` for that method |
| `prompts.py`, `MODEL`, `RERANK_MODEL`, `RagConfig` | nothing — restart `app.py` / the dashboard |
| `evaluation/tests.jsonl` | nothing — read on every run |

---

# Troubleshooting (developer)

| Symptom | Cause | Fix |
|---|---|---|
| `NotFoundError: The model … has been deprecated` | OpenAI retired the model | set a current model in the constants of Part C §3 |
| An LLM step seems to hang | a `@retry` without `stop` retries a permanent error for ever | every `@retry` needs `stop=stop_after_attempt(3)` |
| `RetryError[<Future …>]` | the 3 retries are used up | the real error is `e.last_attempt.exception()`; the fallback messages print it |
| Run stops at `(Pdb)` | a forgotten `breakpoint()` / `pdb.set_trace()` | `c` to continue, `q` to quit; remove it before committing |
| A run won't stop | multiprocessing workers | `Ctrl+C` 2–3×, else `Ctrl+\`, else `pkill -f ingest.py` |
| Dashboard dropdown empty / `get_collection` fails | no store built on this checkout | Part C §1 |
| Answers mention another company or old content | leftover files in `outputs/clean/` | delete `outputs/`, re-crawl, re-ingest |
| `uv` can't resolve or wrong packages | `uv sync` without the groups | `uv sync --group convert --group bot` |

For the reasons behind any of this: `DEVLOG.md` (index at the top).
