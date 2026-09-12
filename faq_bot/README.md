# FAQ bot — retrieval-augmented answering

This folder is the **RAG layer** built on top of the crawler: it reads the
crawler's clean markdown, embeds it into a local Chroma vector store, and
answers questions about Stadtwerke Waiblingen through a Gradio UI.

It is a **personal learning project** and is *not* part of the company crawler
pipeline. The crawler stays LLM-free and unchanged; this layer only *reads* its
output (`outputs/clean/`) and never writes to it. Nothing here touches the
company knowledge base at `aigateway.eu` — that is `crawler/uploader.py`'s job, and only
on the `crawler-crawl4ai` branch.

Written 2026-09-04, extended 2026-09-11 with the **`pro_implementation/`**
layer (§7): four switchable chunking methods and switchable retrieval steps, so
the design decisions can be measured instead of argued about. Companion to
`evaluation/README.md`, which documents the test set and the metrics used to
judge this layer.

---

## 1. Architecture

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
full in §7. It is the one the dashboard evaluates; `implementation/` above is
the simpler course version, kept as a baseline.

| File | Role |
|---|---|
| `implementation/ingest.py` | Build the vector store. Run it whenever `outputs/clean/` changes. Deletes the old collection first, so it is a full rebuild, not an update. |
| `implementation/answer.py` | The RAG core: `fetch_context`, `combined_question`, `answer_question`. Takes and returns **plain strings** — no UI types (§4.4). |
| `app.py` | The Gradio UI, and the only place that knows about Gradio's message format. |
| `vector_db/` | The persisted Chroma store. Committed to git — see §4.8 for the trade-off. |
| `explore_chunks.ipynb` | The notebook the ingest/answer code grew out of; kept for the chunk-map and token-count experiments. |

**The dependency direction is one-way**: `app.py → answer.py`, and
`evaluation/ → answer.py`. `answer.py` imports nothing from either. That is what
lets the evaluation call the RAG core without starting a UI.

---

## 2. How to run it

### 2.0 First-time setup

```bash
uv sync --group bot          # from the repo root; installs langchain, chroma, gradio, …
```

`.env` in the **repo root** needs one key for this layer:

```
OPENAI_API_KEY=sk-…          # embeddings + answering + the LLM judge
```

`.env` is gitignored. The crawler's own keys (`PUSHOVER_*`, `AIGATEWAY_KEY`) are
unrelated and not needed here.

### 2.1 Where the knowledge files come from

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
(§4.6). Anything that globs that folder inherits every leftover — delete
`outputs/` and re-crawl before building an index you intend to trust.

### 2.2 Running each program

Two implementations exist. Everything below uses `pro_implementation/`, the
current one; `implementation/` is the simpler course version (§1).

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
notebook kernel keeps the packages it loaded at start-up (§4.3).

### 2.3 Choosing the chunking method

The method is a CLI flag; each writes its own collection, so all four can exist
side by side (§7.1, §7.4):

```bash
../../.venv/bin/python ingest.py --method whole       # 1 file = 1 chunk       →  81
../../.venv/bin/python ingest.py --method markdown    # LangChain defaults     → 128
../../.venv/bin/python ingest.py --method recursive   # 500 chars / 100 overlap→ 738
../../.venv/bin/python ingest.py --method llm         # gpt-4.1-nano splits    → ~298
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

### 2.4 Turning rewrite and rerank on or off

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
is in §7.5.

### 2.5 Editing the test set

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
  the corpus; never invent a fact (`evaluation/README.md` §2.2).
- **`category` is the x-axis of both bar charts**, so a typo creates a new bar.
  The eight values are listed in `evaluation/README.md` §2.6.
- **Questions are kept verbatim, typos included** — real users type that way.
- The file must be **UTF-8** and every line valid JSON. `load_tests()` fails on
  the first bad line.

Full field reference and the composition of the current 82: `evaluation/README.md`
§1-§2.

### 2.6 Where the prompts are — and which one you may edit

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
(`evaluation/README.md` §3.3).

**The other four are machinery, not phrasing.** Their replies are parsed into
`RankOrder`, `Chunks` and `AnswerEval` objects, so a reworded instruction can
break the parsing rather than merely change the output. Two specific traps: the
rewrite prompt must keep its *"in the SAME language as the user's question"*
line (§7.7), and the chunking prompt must keep demanding the original text back
unchanged — without it, `--method llm` would store a summary instead of the
document. Editing the chunking prompt also means re-running
`ingest.py --method llm`, because it changes what is *stored*, not what is said.

The answering prompt was moved into `pro_implementation/prompts.py` on
2026-09-12 for exactly this reason; §8.1 explains the decision and why the other
four stay where they are. `answer.py` imports it with a `try/except` that covers
both import styles (plain when run from inside the folder, relative when
imported as `faq_bot.pro_implementation.answer` by the evaluation) — a bare
`from prompts import …` breaks the second case with `ModuleNotFoundError`.

---

## 3. Current settings

**`pro_implementation/` (current).** Every value below is a module constant —
this table is the complete list of knobs:

| Setting | Value | Where |
|---|---|---|
| Embedding model | `text-embedding-3-large` (3072 dims) | `ingest.py`, `answer.py` — **must match** |
| Chunking / rewrite / rerank / answer model | `openai/gpt-4.1-nano` | `ingest.py:MODEL`, `answer.py:MODEL` |
| LLM judge model | `openai/gpt-4.1` (deliberately stronger) | `evaluation/eval.py:MODEL` |
| Chunk size / overlap | 500 / 100 — **`recursive` only** | `ingest.py:CHUNK_SIZE`, `CHUNK_OVERLAP` |
| LLM chunk target | 1000 chars | `ingest.py:AVERAGE_CHUNK_SIZE` |
| Parallel chunking workers | 10 | `ingest.py:WORKERS` |
| Retrieved per search | `RETRIEVAL_K = 10` | `answer.py` |
| Passed to the model | `FINAL_K = 5` | `answer.py` |
| Default store / switches | `whole_file`, rewrite off, rerank off | `answer.py:RagConfig` |
| Store | Chroma, `pro_implementation/preprocessed_db`, 4 collections | both |
| Retry policy | 3 attempts, exponential 10 s → 240 s | `answer.py:@retry` |

The `openai/` prefix is **litellm** syntax. `pro_implementation` calls litellm,
so it needs the prefix; `implementation/` calls LangChain's `ChatOpenAI`, which
needs the bare name `gpt-4.1-nano`. Copying a model string between the two files
without adjusting the prefix produces a 404.

**`implementation/` (the simpler course version).**

| Setting | Value | Where |
|---|---|---|
| Embedding model | `text-embedding-3-large` (3072 dims) | `ingest.py`, `answer.py` — **must match** |
| Answer model | `gpt-4.1-nano`, `temperature=0` | `answer.py` |
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

## 4. Problems met, and how they were solved

### 4.1 Two Python environments, silently

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

### 4.2 The pillow conflict: `uv sync` had no solution

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

### 4.3 "nbformat is not installed" when it was

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

### 4.4 The Gradio message format leaked into the RAG core

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

### 4.5 Relative paths differ between a notebook and a script

`glob.glob("../outputs/clean/*.md")` works in a notebook (whose working
directory is its own folder) and breaks in a script run from the repo root.
`ingest.py` therefore derives its paths from `__file__` instead. Related:
`glob.glob` has **no guaranteed order**, so anything that depends on file order
must sort — otherwise a rebuild is not reproducible.

### 4.6 Stale files in `outputs/clean/` poison the vector store

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

### 4.7 The embedding model must match the store

The notebook first built the DB with `text-embedding-3-small` (1536 dims); the
code then moved to `text-embedding-3-large` (3072). Querying a store with a
different model than it was built with is a dimension mismatch — a loud error if
the sizes differ, and silently wrong results if they happen to match.

**Check before debugging retrieval quality:**

```bash
sqlite3 faq_bot/vector_db/chroma.sqlite3 "select name, dimension from collections;"
```

3072 = `-large`. Both `ingest.py` and `answer.py` name the model, and they must
agree; a shared constant would be better than two literals.

### 4.8 Committing the vector store

`vector_db/` is committed on purpose, to avoid re-paying for embeddings. Two
things make that a weaker deal than it looks:

- The full corpus is ~317,000 characters ≈ 108,000 tokens, so a **complete
  rebuild costs about $0.017** with `text-embedding-3-large` — less than two
  cents.
- `chroma.sqlite3` is **binary**: git cannot store a delta, so every rebuild adds
  a whole new copy to history for ever. The first commit already contained
  **three** collection directories from three earlier rebuilds, and the file is
  now ~18 MB.

If the goal is only "do not lose it", a copy outside the repo achieves the same
thing without the permanent history cost.

---

## 5. Known limitations

- **Chunk sizes are very uneven** (64 → 3999 chars, median 1735). The splitter
  runs on its default 4000-character budget, so most pages survive as one or two
  large chunks. Large chunks average many topics into one vector, which is
  exactly what `evaluation/README.md` §2.1 argued against for the Excel pages.
- **`k = 10` is fixed** and never tuned against the test set.
- **The store still holds the duplicated WLAN chunks** — the source file was
  deleted (§4.6) but the DB predates that, so `ingest.py` needs a re-run.
  (This applies to `implementation/vector_db` only; the `pro_implementation`
  collections were all built after the deletion.)
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

## 6. Next steps

Chunking, query rewriting and reranking were the three items on this list, and
all three now exist as **switches** in `pro_implementation/` (§7) rather than as
opinions. What is still open:

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
  down. Everything in §7 is built and verified to run; none of it is *measured*
  yet.

Baseline to beat: the colleagues' manual results — 54 success, 14 suggestion,
14 failure — and in particular the 5 failures whose answer is already in the
corpus (`evaluation/README.md` §3.3).


---

## 7. The advanced implementation (`pro_implementation/`, 2026-09-11)

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

### 7.1 The four chunking methods

| `--method` | how it splits | chunks | min | median | max |
|---|---|---:|---:|---:|---:|
| `whole` | no splitting: one file = one chunk (own code) | 81 | 307 | 1,961 | 17,989 |
| `markdown` | `MarkdownTextSplitter()` — LangChain defaults (4000/200) | 128 | 64 | 1,733 | 3,999 |
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

### 7.2 Heading-only chunks, and why overlap does not prevent them

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

### 7.3 The CLI

```bash
cd faq_bot/pro_implementation

../../.venv/bin/python ingest.py --help
../../.venv/bin/python ingest.py --method whole        # seconds, ~2¢ of embeddings
../../.venv/bin/python ingest.py --method markdown
../../.venv/bin/python ingest.py --method recursive
../../.venv/bin/python ingest.py --method llm          # ~2 min, 81 nano calls, 10 workers

../../.venv/bin/python ingest.py --list                # what is in the store
../../.venv/bin/python ingest.py --delete markdown     # remove one collection
```

`--list` prints the name and chunk count of every collection; `--delete` prints
the count before removing, and refuses an unknown name by listing what exists.
`argparse`'s `choices=` rejects a mistyped method instead of silently doing
nothing.

### 7.4 One collection per method, and the chunk count as a fingerprint

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

### 7.5 The retrieval switches: `RagConfig`

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

### 7.6 How to run the whole thing

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

### 7.7 Problems met, and how they were solved

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
functions now carry `stop=stop_after_attempt(3)`. A retry policy is for
transient failures; without a stop it also "retries" your bugs.

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

### 7.8 Known limitations

- **Nothing here is measured yet.** All four collections are built and the
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

## 8. Toward a reusable RAG test harness (sketch, not built)

The layer has quietly become a small experiment framework: a corpus goes in,
four chunking methods and two retrieval switches can be combined, and 82 graded
questions come out as numbers. Everything that is *specific to Stadtwerke
Waiblingen* is now a small minority of the code. This section records what it
would take to make that reusable, and in what order — **none of it is built**.

### 8.1 Move the answering prompt out of the code — **done 2026-09-12**

The first step, and the cheapest. It is now
`faq_bot/pro_implementation/prompts.py`; the rest of this section is the
reasoning, kept because it applies to the next prompt that becomes a knob.

Of the five prompts (§2.6), only **one is meant to be edited**: the answering
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
   The English-rewrite bug (§7.7) was a *prompt* defect that silently halved
   retrieval quality. Once the answering prompt is a named constant in its own
   file, a `prompt_variant` field on `RagConfig` could put version A against
   version B over the same 82 questions.
3. **Prompts change far more often than code.** In its own file, a prompt change
   stops looking like a code change in `git diff`.
4. **Non-programmers can edit a text file.** The same argument `HANDOVER.md`
   makes for the crawler's source documents: a colleague from the Kunden-Center
   could improve the answering tone without being shown a Python file.

When the three-way split (§8.4) happens, this file belongs to part 2. The judge
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

### 8.2 The other steps, in order

| Step | What it means | Why it is next |
|---|---|---|
| **Extract the config** | corpus path, DB path, company name, model names, `K`s into one `settings.py` or a YAML file | the same "data separate from code" rule the crawler follows (`CLAUDE.md`); it is what makes a second corpus possible at all |
| **Record every run** | append `{timestamp, config, metrics}` to a CSV/JSONL after each evaluation | today results live only in the browser; comparing four collections means four screenshots, and nothing survives a page reload |
| **Sweep instead of clicking** | a `compare.py` that loops over `[collections] × [rewrite] × [rerank]` and prints one table | the manual dashboard is fine for 4 runs, not for 16 |
| **Score retrieval by source, not keywords** | every test row already carries `source`; use it | keyword matching cannot distinguish "found the right page" from "found a page containing the word" (`evaluation/README.md` §6.5) |
| **Generalise the test set** | `tests.jsonl` + a loader is already corpus-agnostic; the *categories* are not | `procedural` was added for a utility's FAQ; another domain needs its own mix (`evaluation/README.md` §2.6) |
| **Separate the layers properly** | `core/` (corpus-agnostic) vs `sites/waiblingen/` (prompts, corpus, tests) | the point of the exercise: swapping the site should touch no Python |

### 8.3 What is already reusable, and what is not

**Already generic:** the chunking methods, `RagConfig` and the switch plumbing,
the Chroma collection-per-experiment layout, the metrics (MRR / nDCG / coverage
/ LLM-judge), the dashboard, `tests.jsonl` and its loader.

**Still Waiblingen-specific:** all five prompts (company name, German),
`KNOWLEDGE_BASE_PATH` pointing at `outputs/clean`, the chunk-map category
convention (`Section_Subsection_Page.md`), the eight question categories, and
the keyword lists.

So the ratio is already favourable — which is exactly why §8.1 is worth doing
before the code grows further.

### 8.4 The three-part split, and where each thing belongs

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
