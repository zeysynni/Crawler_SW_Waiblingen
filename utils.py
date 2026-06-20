from pathlib import Path
from datetime import datetime
import json
import os
from rich.console import Console
from rich.markdown import Markdown
import pypandoc

def save_markdown(markdown_text: str, output_dir: str = "outputs", filename: str | None = None, encoding: str = "utf-8") -> Path:
    """Save Markdown-Text locally as .md-Datei. A timestamp is always appended to the filename."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename}_{timestamp}"

    file_path = output_path / f"{filename}.md"

    with file_path.open("w", encoding=encoding) as f:
        f.write(markdown_text.strip() + "\n")

    return file_path

def save_json(result, output_dir="outputs", filename=None):
    """Save markdown as json."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"crawl_{timestamp}"
    file_path = output_path / f"{filename}.json"

    with file_path.open("w", encoding="utf-8") as f:
        json.dump(result.final_output.model_dump(), f, indent=2, ensure_ascii=False)

    return file_path

def print_markdown(result):
    console = Console()
    console.print(Markdown(result.final_output))

def json_to_markdown(data: dict) -> str:
    """Convert json output as markdown. This should be adapted if the webpage structure is changed."""
    lines = []

    for page in data.get("pages", []):
        # Page metadata
        lines.append(f"*URL: {page.get('url', '')}*")
        lines.append("")

        # Blocks
        for block in page.get("blocks", []):
            lines.append(f"## {block.get('heading', '')}")
            lines.append("")

            # Segments
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
    
def save_markdown_from_json(json_path: str, md_path: str) -> Path | None:
    if not Path(json_path).exists():
        print(f"Warning: JSON file not found, skipping Markdown export: {json_path}")
        return None

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    markdown = json_to_markdown(data)

    p = Path(md_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamped_path = p.parent / f"{p.stem}_{timestamp}{p.suffix}"

    with open(timestamped_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    return timestamped_path

def md_to_pdf(input_md_path: str, output_pdf_path: str):
    """Convert a Markdown (.md) file to a PDF file."""
    if not os.path.exists(input_md_path):
        raise FileNotFoundError(f"Input file not found: {input_md_path}")

    try:
        pypandoc.convert_file(
            input_md_path,
            to="pdf",
            outputfile=output_pdf_path,
            extra_args=["--pdf-engine=xelatex"]  # or pdflatex
        )
        print(f"PDF successfully created at: {output_pdf_path}")
    except Exception as e:
        raise RuntimeError(f"Conversion failed: {e}")
