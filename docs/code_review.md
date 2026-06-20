# Code Review Report

## Summary

This is a small LLM-powered web crawler that uses the OpenAI Agents SDK with Playwright MCP for browser automation. It extracts structured content from a German utility company website, stores results as JSON/Markdown, and provides an FAQ bot backed by either a knowledge-graph DB or SQLite. The codebase is straightforward and prototype-grade: it works for happy-path runs but has no error handling, several silent data-loss bugs, duplicated modules, unsafe file I/O, and hardcoded assumptions that will break as soon as the use case expands.

---

## Critical Issues

**1. `utils.py:70` — `segment["FAQs"]` key never exists in JSON output**

`json_to_markdown` checks `segment.get("FAQs")`, but the Pydantic model `ContentSegment` in `webpage_structure.py` has no field named `FAQs`. The segment fields are `subheading`, `text`, `files`, and `contacts`. FAQ data lives inside `Block.segments` as a separate `FAQ` object only in `QA`/`FAQ`/`Block` nesting — but `ContentSegment` never holds a `FAQs` key. As a result, **all FAQ content is silently dropped from every Markdown output**.

Fix: Either add a `faqs: Optional[FAQ]` field to `ContentSegment` and wire it in, or restructure `json_to_markdown` to match the actual serialised shape produced by `model_dump()`. Verify with a real JSON output file first.

**2. `utils.py:25-38` — `save_json` always overwrites the same file per topic**

When `main.py` calls `launch_crawler` for multiple subparts of a topic in a loop, each call overwrites `outputs/{topic}.json` with only the latest subpart's result. Only the last subpart is preserved; earlier subparts are silently lost.

```python
# main.py lines 18-20
for subpart in subparts:
    prompt = get_user_prompt_structured_output(url, subpart)
    await launch_crawler(agent, topic, prompt)  # each call overwrites outputs/{topic}.json
```

Fix: Either accumulate results in memory across subpart calls and write once, or append/merge the `Webpages.pages` lists before writing to disk.

**3. `faq/faq_bot.py:14-30` — New MCP server spawned on every single chat message**

`create_faq_agent` (and thus `create_db_servers`) is called inside `ask()`, which is called for every Gradio message. This means a new `npx`/`uvx` child process is spawned, connected to, and then torn down for every user query. This will fail under load, leak file handles, and produces severe latency.

```python
async def ask(question: str, history: list) -> str:
    async with AsyncExitStack() as stack:   # new MCP server process per message
        agent = await create_faq_agent(stack, ...)
```

Fix: Lift the `AsyncExitStack` and agent creation out of `ask()` to module-level (initialised once at startup), or use Gradio's `State` / a persistent lifespan context.

**4. `config.py:349` — `object` shadows Python built-in and active config is not obvious**

```python
object = kontakt   # shadows the built-in `object`
structure = { object.get("title"): ... }
```

The crawl target is changed by editing a single assignment deep in a 350-line config file with no comment marking it as the "active selection". Shadowing `object` will cause confusing `AttributeError` if any downstream code (or the SDK) relies on the built-in `object` type.

Fix: Rename to `active_topic = kontakt` and add a clear `# ← change this line to switch crawl target` comment.

---

## Security Issues

**5. `faq/ingest_sql.py:47` — CSV exported to current working directory with fixed name**

```python
with open('knowledge_data.csv', 'w', newline='') as csv_file:
```

The file is written relative to whatever `cwd` the script is launched from, not to the `memory/` folder where the database lives. This is already manifested in the git status (`?? faq/knowledge_data.csv`). The CSV contains the full text of all ingested documents. If the project is later deployed in a web-accessible directory, this file is exposed.

Fix: Write to `memory/knowledge_data.csv` using an absolute path derived from `__file__`, or make the path a parameter; also add `knowledge_data.csv` to `.gitignore`.

**6. `mcp_params.py:1` (root) and `faq/mcp_params.py:1` — `@playwright/mcp@latest` and `mcp-memory-libsql` pinned to `latest`**

```python
playwright_params = {"command": "npx","args": ["@playwright/mcp@latest"], ...}
```

Using `@latest` for an MCP server that runs as a child process with full browser access means any breaking change or supply-chain compromise in those npm packages silently affects the next run. There is no lockfile for these npm packages.

Fix: Pin to a specific version (e.g., `@playwright/mcp@0.0.28`). Consider adding an `npm` lockfile or a `package.json` to the project.

---

## Code Quality Issues

**7. `faq/db_agent.py:1` — Unused import `from ast import Str`**

`Str` is imported from the `ast` module but never used. `ast.Str` is itself deprecated since Python 3.8. This is dead code.

Fix: Remove the line.

**8. `utils.py:9` — `save_markdown` always appends a timestamp even when `filename` is provided**

The comment `#if filename is None:` is left but commented out; the timestamp is added unconditionally:

```python
def save_markdown(markdown_text, ..., filename=None):
    # if filename is None:     ← commented out
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename}_{timestamp}"
```

Callers who pass an explicit filename get a timestamped name they didn't ask for. The function is also imported in `utils.py` but never called from `main.py` or anywhere in the active pipeline — the active code path uses `save_markdown_from_json` instead. `save_markdown` is dead code in its current state.

Fix: Either restore the `if filename is None:` guard so callers get predictable filenames, or delete the function if it is unused.

**9. `crawl_agent.py:42-50` — New `openai.AsyncOpenAI` client created on every `launch_crawler` call**

```python
async def launch_crawler(agent, topic, message):
    openai_client = openai.AsyncOpenAI(max_retries=5)
    run_config = RunConfig(model_provider=OpenAIProvider(openai_client=openai_client), ...)
```

The same pattern appears in `faq/db_agent.py:43-47`. The HTTP client (including its connection pool) is reconstructed for every agent run. This negates connection-pooling benefits and is wasteful when many topics are crawled sequentially.

Fix: Create the client once (at module level or injected via parameter) and reuse it across calls.

**10. `faq/ingest_sql.py:19-30` — No duplicate-check before inserting; re-running ingestion doubles all rows**

```python
con.execute("INSERT INTO knowledge (topic, content) VALUES (?, ?)", (topic, content))
```

There is no `DELETE FROM knowledge` before the loop and no `WHERE NOT EXISTS` guard. Every re-run of `ingest_sql.py` appends all documents again. The FAQ bot will then find duplicate rows and produce duplicated answers.

Fix: Add `con.execute("DELETE FROM knowledge")` at the start of `ingest_md_files`, or use `INSERT OR REPLACE` with a `UNIQUE` constraint on `topic`.

**11. `faq/ingest_kg.py:22-29` — No idempotency for knowledge-graph ingestion either**

`ingest_kg.py` calls `launch_DB` for each markdown file without checking whether entities already exist. Re-running the script accumulates duplicate nodes in the libsql knowledge graph.

Fix: Add a pre-ingestion wipe step (call the KG's delete/reset tool) or skip files whose entities are already stored.

**12. `utils.py:90-97` — `save_markdown_from_json` raises `FileNotFoundError` with no message when JSON does not exist**

```python
def save_markdown_from_json(json_path: str, md_path: str) -> None:
    with open(json_path, encoding="utf-8") as f:   # bare open(), no error handling
        data = json.load(f)
```

If a crawl run fails and the JSON is not written, `main.py` will crash here with a raw `FileNotFoundError`. The same applies to `md_to_pdf` in `utils.py:100-113`.

Fix: Wrap in try/except with a descriptive message, or at minimum check `Path(json_path).exists()` and log a warning before skipping.

**13. `crawl_agent.py:50` and `faq/db_agent.py:51` — `max_turns=200` with no timeout**

Both agent runners set `max_turns=200` but there is no wall-clock timeout. A hung Playwright session or a model that loops indefinitely will block the process forever.

Fix: Add a `asyncio.wait_for(..., timeout=300)` wrapper around `Runner.run(...)` calls.

**14. `md2pdf.py:102` — Nested f-string with same quote style (Python < 3.12 syntax error)**

```python
filename=f"{object.get("title")}.md",
```

Using `"` inside an f-string delimited by `"` is only valid in Python 3.12+. On Python 3.10/3.11 this is a `SyntaxError`.

Fix: Use `object.get('title')` (single quotes) inside the outer f-string, or extract to a variable: `title = active_topic.get("title")`.

---

## Design & Architecture Issues

**15. `prompts.py` is duplicated verbatim into `faq/prompts.py`**

Both files are byte-for-byte identical (the entire `prompts.py` content is copy-pasted into `faq/prompts.py`). Any prompt change must be made in two places.

Fix: Delete `faq/prompts.py` and update `faq/faq_bot.py`, `faq/ingest_kg.py`, and `faq/ingest_sql.py` to import from the parent package: `from prompts import ...` (after ensuring the path is on `sys.path` or making the project a proper package with `__init__.py` files).

**16. `mcp_params.py` is duplicated verbatim into `faq/mcp_params.py`**

Identical to issue 15. Both files define the same `playwright_params`, `knowledge_graph_db_params`, `sql_db_name`, etc.

Fix: Delete `faq/mcp_params.py` and import from the root `mcp_params.py`.

**17. `crawl_agent.py` and `faq/db_agent.py` share nearly identical server-creation and run boilerplate**

`create_mcp_servers` / `create_db_servers` are functionally the same function (same body, different names). `launch_crawler` and `launch_DB` are almost identical (create client, build RunConfig, call Runner.run, return result). There is no shared base.

Fix: Extract a shared `create_mcp_servers(stack, params_list)` utility and a `run_agent(agent, message, topic, max_turns=200)` helper into `utils.py` or a new `agent_utils.py`; import from both callers.

**18. `config.py` mixes data definitions and active selection at module level**

`config.py` is 350 lines of raw dictionaries. The active crawl target is controlled by a single mutable assignment at the bottom (`object = kontakt`). Any other file importing `config.structure` is coupled to this global side effect. There is no way to run two topics without editing the file.

Fix: Move all topic definitions to a data structure (e.g., a `dict[str, dict]` keyed by name). Expose an `ALL_TOPICS` dict and let `main.py` iterate over the desired subset, passed via CLI argument or a small config section at the top of `config.py`.

**19. `ingest_sql.py` mixes schema initialisation, data ingestion, and CSV export in one script with no separation**

`init_db`, `ingest_md_files`, and `export_to_csv` are in the same file and the main block calls them sequentially without any option to run them independently. The CSV export happens as a side effect of every ingest.

Fix: Either separate into distinct entry points or add CLI flags (`--init`, `--ingest`, `--export`) using `argparse`.

**20. `faq_bot.py` history parsing is fragile**

```python
messages.append({"role": "user", "content": turn["content"] if isinstance(turn, dict) else turn[0]})
messages.append({"role": "assistant", "content": turn["content"] if isinstance(turn, dict) else turn[1]})
```

Both the user and assistant turns are populated from the same `turn` object in the `history` list, with the same branch condition. When Gradio passes tuples (`(user_msg, assistant_msg)`), the code takes `turn[0]` for both user and assistant for the first branch — but it should be `turn[1]` for assistant. When the Gradio `type="messages"` format passes dicts, both turns share the same `turn["content"]`. The history reconstruction is incorrect in both cases.

Fix: Iterate over history pairs properly. With `type="messages"` Gradio already passes a list of `{"role": ..., "content": ...}` dicts — pass them directly to the runner without manual reconstruction.

---

## Recommended Actions (Prioritized)

1. **Fix the FAQ key mismatch in `json_to_markdown`** (`utils.py:70`) — all FAQ content is currently dropped from every Markdown output. Verify against an actual `model_dump()` of a `ContentSegment` and fix the key name. **Effort: quick.**

2. **Fix result overwrite in multi-subpart crawl loop** (`main.py:18-20`, `utils.py:25-38`) — earlier subpart results are silently lost. Accumulate `Webpages.pages` lists and write once, or use per-subpart filenames. **Effort: quick.**

3. **Fix per-message MCP server spawn in `faq_bot.py`** — move agent creation outside `ask()` to a module-level startup. **Effort: quick.**

4. **Deduplicate `prompts.py` and `mcp_params.py`** — delete the copies under `faq/` and import from the root. **Effort: quick.**

5. **Fix `ingest_sql.py` duplicate-row problem** — add a `DELETE FROM knowledge` before the insert loop, or add a unique constraint. **Effort: quick.**

6. **Fix the nested f-string syntax error in `md2pdf.py:102`** — use single quotes inside the f-string to ensure compatibility below Python 3.12. **Effort: quick.**

7. **Rename `object` to `active_topic` in `config.py`** — remove the shadowing of the built-in and make the active selection obvious. **Effort: quick.**

8. **Remove unused import `from ast import Str` in `faq/db_agent.py`** and dead `save_markdown` function in `utils.py`. **Effort: quick.**

9. **Add `asyncio.wait_for` timeout around `Runner.run` calls** in both `crawl_agent.py` and `faq/db_agent.py`. **Effort: quick.**

10. **Refactor shared agent/server boilerplate** into a common utility (`create_mcp_servers`, `run_agent`) to eliminate the near-duplicate code in `crawl_agent.py` and `faq/db_agent.py`. **Effort: medium.**

11. **Restructure `config.py`** — replace the global side-effect active selection with an `ALL_TOPICS` dict and CLI-driven topic selection in `main.py`. **Effort: medium.**

12. **Fix `faq_bot.py` history reconstruction** — pass Gradio's `type="messages"` history dict list directly to the runner without manual per-turn reconstruction. **Effort: medium.**

13. **Add `gradio` and `tqdm` to `requirements.txt`** — both are imported (`faq_bot.py`, `ingest_kg.py`) but missing from the dependency list. Also add `openai-agents` (or whatever the correct package name for `agents` is) explicitly. **Effort: quick.**

14. **Add `.gitignore` entries** for `knowledge_data.csv`, `memory/*.db`, `outputs/`, and `__pycache__`. **Effort: quick.**
