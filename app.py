"""Gradio UI: fill in one site-YAML section, crawl it, read the markdown.

    uv sync --group ui
    uv run python app.py

The form is an **editor for `sites/*.yaml`**, not a way around it. Each field
maps 1:1 onto `config.Section`/`config.Site`, the values go through the same
Pydantic validation the CLI uses — so a typo or a broken regex fails here with
the message it would give in a site file — and the result panel prints the YAML
snippet to paste into `sites/` once the output looks right. "Try it, then
commit it."

Raw and clean sit side by side on purpose. `stop_at` can only be written by
someone who can see the noise they want to cut, and a pattern that matches
nothing is otherwise a silent failure: the clean panel just quietly keeps the
footer. The line under the box says where the cut actually landed.

No LLM is involved, here or anywhere else in this project: the crawl and the
cleaning stay deterministic, and the regex helper is `re.escape`, not a model.

The crawl is the real one (`crawl.crawl_site`), so a click opens a browser and
takes a few seconds per page. Nothing is written to disk — `outputs/` stays the
CLI's business.
"""

import logging
import re
from datetime import datetime, timezone

import gradio as gr
import yaml
from pydantic import ValidationError

import monitor
from clean import content_span
from config import Section, Site
from crawl import crawl_site
from extract import EXTRACTORS
from render import render_clean

log = logging.getLogger("crawler")

# Dropdown entry meaning "no extractor" — a Gradio dropdown cannot hold None.
NO_EXTRACTOR = "— none (use clean.py) —"


def _lines(text: str) -> list[str]:
    """Textarea -> list, blank lines dropped. Used for subpages and stop_at."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _form_to_site(root_url: str, path: str, url: str, subpages: str,
                  extract: str, stop_at: str) -> Site:
    """Form fields -> a validated one-section `Site`.

    Raises `ValidationError` exactly like a bad `sites/*.yaml` would: missing
    path, unknown extractor, unparseable stop_at regex. The UI shows that
    message unchanged rather than inventing its own wording.
    """
    section = Section(
        path=path.strip(),
        url=url.strip() or None,
        subpages=_lines(subpages),
        extract=None if extract == NO_EXTRACTOR else extract,
    )
    return Site(root_url=root_url.strip(), stop_at=_lines(stop_at), sections=[section])


def _yaml_snippet(site: Site) -> str:
    r"""The site file this form describes — ready to paste into `sites/`.

    Written as text rather than `yaml.dump`: unset optionals stay out instead of
    appearing as `url: null`, the sequence indentation matches the existing site
    files, and `stop_at` values get the single quotes they need (YAML rejects
    `\s` inside double quotes) without PyYAML also quoting the keys.
    """
    def sq(value: str) -> str:                 # '' is YAML's escaped single quote
        return "'" + value.replace("'", "''") + "'"

    out = [f"root_url: {site.root_url}"]

    if site.stop_at:
        out += ["", "# Cut the clean form at the first line matching any of these",
                "# (regex, re.search per line). Single quotes are required.",
                "stop_at:"]
        out += [f"  - {sq(pattern)}" for pattern in site.stop_at]

    section = site.sections[0]
    out += ["", "sections:", f"  - path: {section.path}"]
    if section.url:
        out.append(f"    url: {section.url}")
    if section.extract:
        out.append(f"    extract: {section.extract}")
    if section.subpages:
        out.append("    subpages:")
        out += [f"      - {label}" for label in section.subpages]
    return "\n".join(out) + "\n"


def _cut_note(raw_markdown: str, stop_at: list[str], used_extractor: bool) -> str:
    """One line saying where `stop_at` cut — the whole point of the raw panel.

    Uses `clean.content_span`, the same function `clean_markdown` uses, so this
    can never claim a different cut than the one that actually happened.
    """
    if used_extractor:
        return "ℹ️ built by an extractor from the HTML — `stop_at` does not apply."
    lines = raw_markdown.splitlines()
    start, end = content_span(lines, stop_at)
    if not stop_at:
        return f"ℹ️ no `stop_at` patterns — nothing cut, {len(lines) - start} lines kept."
    if end >= len(lines):
        return ("⚠️ **no line matches** — nothing was cut. The footer and cookie "
                "banner are still in the clean panel.")
    return (f"✅ cut at raw line {end + 1}: `{lines[end].strip()[:60]}` — "
            f"{end - start} lines kept, {len(lines) - end} dropped.")


def regex_from_line(line: str) -> str:
    """A literal line -> a regex that matches exactly it, anchored at line start.

    Deterministic on purpose: for "cut here, at this line I can see in the raw
    panel" — which is most of the real cases — `re.escape` is exact, instant and
    free, and needs no API key.
    """
    line = line.strip()
    return "^" + re.escape(line) if line else ""


async def crawl_form(root_url, path, url, subpages, extract, stop_at):
    """Validate the form, crawl the one section, render every page it produced.

    `async def` on purpose: Gradio already runs an event loop, so `asyncio.run`
    would raise "this event loop is already running" here.
    """
    empty = (gr.update(choices=[], value=None), "", "", "", "")
    try:
        site = _form_to_site(root_url, path, url, subpages, extract, stop_at)
    except ValidationError as e:
        return (f"**Config invalid**\n```\n{e}\n```", {}, *empty)

    started = datetime.now(timezone.utc)
    pages = await crawl_site(site)
    finished = datetime.now(timezone.utc)

    # One entry per page that both fetched *and* rendered. A broken extractor
    # marks the page failed (same rule as main.save_outputs) so the run report
    # names it, instead of the UI silently showing one page less.
    rendered: dict[str, dict[str, str]] = {}
    for page in pages:
        if not page.ok:
            continue
        try:
            clean = render_clean(page, site)
        except Exception as e:        # noqa: BLE001 — see render.render_clean
            page.error = f"extractor {page.extract}: {e}"
            continue
        page.clean_chars = len(clean)
        rendered[page.name] = {
            "clean": clean,
            "raw": page.raw_markdown,
            "note": _cut_note(page.raw_markdown, site.stop_at, bool(page.extract)),
        }

    report = monitor.run_report(pages, started, finished)
    names = list(rendered)
    first = names[0] if names else None
    shown = rendered.get(first, {})
    return (
        f"```\n{report}\n```",
        rendered,
        gr.update(choices=names, value=first),
        shown.get("raw", ""),
        shown.get("clean", ""),
        shown.get("note", ""),
        _yaml_snippet(site),
    )


def show_page(rendered: dict, name: str):
    """Switch the panels to another page of the same crawl (no refetch)."""
    page = (rendered or {}).get(name)
    if not page:
        return "", "", ""
    return page["raw"], page["clean"], page["note"]


with gr.Blocks(title="crawl4ai crawler") as demo:
    gr.Markdown(
        "# Try a section\n"
        "Fill in one section of a `sites/*.yaml` allowlist, crawl it, and compare "
        "**raw** against **clean** before you commit the config."
    )

    # This crawl's pages, so the picker can switch between them without
    # crawling again. Per browser session, never shared between viewers.
    rendered = gr.State({})

    with gr.Row():
        with gr.Column(scale=2):
            root_url = gr.Textbox(
                label="root_url", value="https://www.ahk-heidekreis.de",
                info="the site's base URL",
            )
            path = gr.Textbox(
                label="path", placeholder="Service/Abfall-ABC",
                info="names the output file; also the URL unless you set 'url'",
            )
            url = gr.Textbox(
                label="url (optional)", placeholder="service/abfall-abc.html",
                info="fetch this instead — relative to root_url, or absolute",
            )
            subpages = gr.Textbox(
                label="subpages (optional)", lines=4,
                placeholder="Gelbe Tonne\nSperrmüll",
                info="one visible link text per line, matched on the base page",
            )
            extract = gr.Dropdown(
                label="extract", choices=[NO_EXTRACTOR, *sorted(EXTRACTORS)],
                value=NO_EXTRACTOR,
                info="build the clean form from the HTML instead of clean.py",
            )
            stop_at = gr.Textbox(
                label="stop_at — one REGEX per line", lines=4,
                placeholder=r"^##\s+Weitere Links\b" "\n" "Wir nutzen Cookies",
                info="Python regex, matched with re.search against each raw line. "
                     "The clean form is cut at the first line that matches. "
                     "Empty = cut nothing.",
            )
            with gr.Row():
                literal = gr.Textbox(
                    label="regex from a literal line", scale=3,
                    placeholder="paste a line from the raw panel",
                )
                make = gr.Button("→ regex", scale=1)
            go = gr.Button("Crawl", variant="primary")

        with gr.Column(scale=3):
            report = gr.Markdown()
            picker = gr.Dropdown(label="page", choices=[], interactive=True)
            note = gr.Markdown()
            with gr.Row():
                raw_out = gr.Code(label="raw", language="markdown", lines=28)
                clean_out = gr.Code(label="clean", language="markdown", lines=28)
            with gr.Accordion("YAML to paste into sites/", open=False):
                yaml_out = gr.Code(language="yaml", lines=14)

    go.click(
        crawl_form,
        inputs=[root_url, path, url, subpages, extract, stop_at],
        outputs=[report, rendered, picker, raw_out, clean_out, note, yaml_out],
    )
    picker.change(show_page, inputs=[rendered, picker],
                  outputs=[raw_out, clean_out, note])
    make.click(lambda line: regex_from_line(line), inputs=literal, outputs=literal)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    demo.launch()
