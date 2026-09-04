# FAQ bot — retrieval-augmented answering

This folder is the **RAG layer** built on top of the crawler: it reads the
crawler's clean markdown, embeds it into a local Chroma vector store, and
answers questions about Stadtwerke Waiblingen through a Gradio UI.

It is a **personal learning project** and is *not* part of the company crawler
pipeline. The crawler stays LLM-free and unchanged; this layer only *reads* its
output (`outputs/clean/`) and never writes to it. Nothing here touches the
company knowledge base at `aigateway.eu` — that is `uploader.py`'s job, and only
on the `crawler-crawl4ai` branch.

Written 2026-09-04. Companion to `evaluation/README.md`, which documents the
test set and the metrics used to judge this layer.

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

```bash
uv sync --group bot                          # from the repo root

cd faq_bot
../.venv/bin/python implementation/ingest.py # build the DB (~$0.02 of embeddings)
../.venv/bin/python app.py                   # launch the UI
```

**`app.py` must be started from inside `faq_bot/`.** It does
`from implementation.answer import answer_question`, which only resolves when
`faq_bot/` is on `sys.path` — that happens automatically when `app.py` *is* the
script being run. From the repo root the import fails.

`ingest.py` has no such constraint: its paths are derived from `__file__`, so it
finds `outputs/clean/` and `vector_db/` wherever it is invoked from.

**After changing a dependency, restart the process.** A running Gradio app or
notebook kernel keeps the packages it loaded at start-up (§4.3).

---

## 3. Current settings

| Setting | Value | Where |
|---|---|---|
| Embedding model | `text-embedding-3-large` (3072 dims) | `ingest.py`, `answer.py` — **must match** |
| Answer model | `gpt-4.1-nano`, `temperature=0` | `answer.py` |
| Splitter | `MarkdownTextSplitter()` — defaults: 4000 chars, 200 overlap | `ingest.py` |
| Retrieval | `k = 10` | `answer.py:RETRIEVAL_K` |
| Store | Chroma, `faq_bot/vector_db`, collection `langchain` | both |

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

`outputs/` is gitignored and never cleaned; `main.py` only *overwrites* the files
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

**The lesson for both cases:** the upload path is safe from this — `main.py`
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
- **Retrieval is scored by keyword presence, not by source file.** The test set
  carries a `source` field precisely so retrieval can be judged on whether the
  *right document* came back; `eval.py` does not use it yet.
- **Undeclared dependencies:** `pandas` and `litellm` are imported directly but
  are not in `pyproject.toml` — they work only because gradio and other packages
  pull them in.
- **No tests.** The crawler's pure functions are unit-tested; nothing here is.
- **Small cleanups pending:** unused imports and the dead
  `CHUNK_SIZE`/`CHUNK_OVERLAP` in `ingest.py`; `model_name=` in `answer.py` is
  the legacy alias for `model=`; `evaluation/eval.py` shadows the builtin `eval`.

---

## 6. Next steps

A code review of this layer, then advanced retrieval. The point of having
`evaluation/tests.jsonl` first is that each of these can be **measured** rather
than assumed:

- **Chunking**: contextual chunk headers (prefix every chunk with its page
  hierarchy), or parent-document retrieval (embed small, return the whole page).
  The `spanning` vs `direct_fact` split in the test set is the evidence for which
  way to go.
- **Query handling**: rewriting a follow-up question into a standalone one.
  `combined_question` currently just concatenates the user's turns, which works
  but grows noisily.
- **Hybrid retrieval**: BM25 alongside the dense vectors. The two
  `relationship` questions (Schorndorf → outside the supply area, eAnwälte →
  E-Rechtsanwälte) are exactly where lexical matching helps.
- **Reranking** the top-k before it reaches the prompt.
- **Metadata filtering** on `doc_type`, which is already stored on every chunk.

Baseline to beat: the colleagues' manual results — 54 success, 14 suggestion,
14 failure — and in particular the 5 failures whose answer is already in the
corpus (`evaluation/README.md` §3.3).
