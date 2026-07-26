# AHK Abfall-ABC Crawler (crawl4ai)

## Overview

An **LLM-free web crawler** that turns the
[Abfall-ABC of AHK Heidekreis](https://www.ahk-heidekreis.de/service/abfall-abc.html)
— the A–Ö list of waste types with disposal locations — into one clean,
complete markdown file: **26 letters, 798 entries**, as tables
(`Abfallart | Wohin? | Hinweise`).

The interesting part: on the website, the A–Ö list is a JavaScript component
that only ever *displays* one letter at a time, so a normal crawl captures
letter A and nothing else. But the component ships **all** entries embedded
in the page's HTML (a JSON `data-items` attribute). This crawler therefore
fetches the page once and builds the output directly from that embedded data
— deterministic, complete, reproducible, no clicking through 26 tabs.

> Non-technical explanation of how this works: **`docs/HOW_IT_WORKS.md`**.
> This branch (`crawler-ahk`) is a one-time/on-demand crawl tool — no
> knowledge-base upload, no schedule. It was adapted from the general
> config-driven crawler on `crawler-crawl4ai` (see `DEVLOG.md` §14–16).

## Key features

* 🚫🧠 **No LLM anywhere** — deterministic fetch + rule-based extraction;
  byte-reproducible output, no API keys, no model costs
* 🗂️ **Allowlist navigation**: the YAML claims every page; unlisted pages are
  never crawled
* 🧩 **Extractor mechanism**: pages whose content is a JS component with
  server-embedded data (like the Abfall-ABC) opt into a pure HTML→markdown
  extractor via `extract:` in the YAML
* 📄 Two outputs per page: `outputs/raw/` (the page as crawled) and
  `outputs/clean/` (**the deliverable**)
* 📟 **Run report** (log + optional Pushover): per page ✓/✗/⚠ with failure
  reason, duration, size; a site relaunch that breaks the extractor is a loud
  failure, never a silent empty file
* ✅ Pydantic-validated config, unit-tested pure functions

## Architecture

```
.
├── main.py                 # Entry point + CLI (argparse)
├── config.py               # Section/Site Pydantic models + load_site()
├── sites/
│   └── ahk-heidekreis.yaml # DATA: the crawl allowlist
├── crawl.py                # crawl4ai fetches, label→URL resolution, retries
├── extract.py              # pure HTML→markdown extractors (the A–Ö table)
├── clean.py                # pure markdown cleaning for normal pages
├── monitor.py              # run report + regression check + Pushover
├── docs/
│   └── HOW_IT_WORKS.md     # plain-language explanation for non-IT readers
├── tests/                  # unit tests for the pure functions
└── outputs/                # generated raw/ + clean/ markdown (gitignored)
```

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                              # create .venv from uv.lock
uv run playwright install chromium   # browser for crawl4ai (once)
```

## Usage

```bash
uv run python main.py                # crawl; result in outputs/clean/
uv run pytest                        # unit tests
```

The deliverable lands at **`outputs/clean/Service_Abfall-ABC.md`**
(~64 KB, overwritten on each run). A full run takes a few seconds.

## Adding / changing crawl targets

Edit `sites/ahk-heidekreis.yaml` — no code changes needed for normal pages:

```yaml
sections:
  - path: Service/Abfall-ABC        # display/file name ...
    url: service/abfall-abc.html    # ... fetched from this URL
    extract: abfall_abc             # JS-component page -> extractor
  - path: Service/Gelbe-Tonne       # a normal page: crawl + rule-based clean
    url: service/gelbe-tonne.html
```

Only a genuinely new *mechanism* (e.g. another JS component type) needs
Python: add a pure function to `extract.py`, register it in `EXTRACTORS`,
opt in via `extract:`.
