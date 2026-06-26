"""Output pipeline: crawl result -> JSON -> Markdown.

We keep only the newest version of each topic. Files are written to stable,
un-timestamped paths (``outputs/<topic>.json`` and ``outputs/<topic>.md``) and
overwritten on every run, which suits a regular (e.g. weekly) re-crawl.

`json_to_markdown` is a pure function (dict -> str) and must be kept in sync
with the schema in `webpage_structure.py`.
"""

import json
import logging
from pathlib import Path

import pypandoc

OUTPUT_DIR = Path("outputs")
PDF_DIR = Path("customer_files")

log = logging.getLogger("crawler")


def save_json(result, topic: str, output_dir: Path | str = OUTPUT_DIR) -> Path:
    """Write the agent's structured output to ``<output_dir>/<topic>.json`` (overwrite)."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{topic}.json"
    path.write_text(
        json.dumps(result.final_output.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def json_to_markdown(data: dict) -> str:
    """Convert the crawl JSON structure into Markdown text.

    Pure function — adapt this if the webpage structure changes.
    """
    lines: list[str] = []

    for page in data.get("pages", []):
        lines.append(f"*URL: {page.get('url', '')}*")
        lines.append("")

        for block in page.get("blocks", []):
            lines.append(f"## {block.get('heading', '')}")
            lines.append("")

            for segment in block.get("segments", []):
                if segment.get("subheading"):
                    lines.append(f"### {segment['subheading']}")
                    lines.append("")
                if segment.get("text"):
                    lines.append(segment["text"])
                    lines.append("")
                if segment.get("files"):
                    lines.append("**Dateien:**")
                    lines.append(segment["files"])
                    lines.append("")
                if segment.get("faqs"):
                    faq = segment["faqs"]
                    if faq.get("title"):
                        lines.append(f"### {faq['title']}")
                        lines.append("")
                    for qa in faq.get("QAs", []):
                        lines.append(f"**{qa.get('question', '')}**")
                        lines.append("")
                        lines.append(qa.get("answer", ""))
                        lines.append("")
                if segment.get("contacts"):
                    lines.append("**Kontakt:**")
                    lines.append(segment["contacts"])
                    lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def write_markdown(topic: str, output_dir: Path | str = OUTPUT_DIR) -> Path | None:
    """Read ``<output_dir>/<topic>.json``, convert it, and write ``<topic>.md`` (overwrite).

    Returns the Markdown path, or None if the JSON does not exist yet.
    """
    out = Path(output_dir)
    json_path = out / f"{topic}.json"
    if not json_path.exists():
        log.warning("%s not found, skipping Markdown export", json_path)
        return None

    data = json.loads(json_path.read_text(encoding="utf-8"))
    md_path = out / f"{topic}.md"
    md_path.write_text(json_to_markdown(data), encoding="utf-8")
    return md_path


def to_pdf(md_path: Path | str, output_dir: Path | str = PDF_DIR) -> Path:
    """Convert a Markdown file to PDF in ``output_dir`` (overwrite), returning the PDF path.

    Opt-in only (the ``--pdf`` flag): requires a system `pandoc` install and a
    LaTeX engine (xelatex). Not used by the default crawl, so the regular run
    never needs the LaTeX toolchain.
    """
    md_path = Path(md_path)
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown file not found: {md_path}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = out / f"{md_path.stem}.pdf"
    pypandoc.convert_file(
        str(md_path),
        to="pdf",
        outputfile=str(pdf_path),
        extra_args=["--pdf-engine=xelatex"],
    )
    return pdf_path
