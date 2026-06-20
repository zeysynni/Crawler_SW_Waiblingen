#from utils import md_to_pdf
from pathlib import Path
from typing import List
import pypandoc
from config import active_topic

def get_md_files(folder_path: str) -> List[Path]:
    """
    Return all .md files (only) in a folder.
    Ignores other file types and directories.
    """
    folder = Path(folder_path)

    if not folder.is_dir():
        raise NotADirectoryError(f"Invalid folder: {folder_path}")

    return [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() == ".md"
    ]

def md_to_pdf(input_md_path: str, output_pdf_path: str):
    pypandoc.convert_file(
        input_md_path,
        to="pdf",
        outputfile=output_pdf_path,
        extra_args=["--pdf-engine=xelatex"]
    )


def convert_folder_md_to_pdf(input_folder: str, output_folder: str):
    """
    Convert all .md files in a folder to .pdf files.

    Args:
        input_folder (str): Folder containing markdown files
        output_folder (str): Folder to save PDFs
    """
    input_path = Path(input_folder)
    output_path = Path(output_folder)

    if not input_path.is_dir():
        raise NotADirectoryError(f"Invalid input folder: {input_folder}")

    output_path.mkdir(parents=True, exist_ok=True)

    md_files = [
        f for f in input_path.iterdir()
        if f.is_file() and f.suffix.lower() == ".md"
    ]

    if not md_files:
        print("No .md files found.")
        return

    for md_file in md_files:
        output_pdf = output_path / (md_file.stem + ".pdf")

        try:
            md_to_pdf(str(md_file), str(output_pdf))
            print(f"Converted: {md_file.name} -> {output_pdf.name}")
        except Exception as e:
            print(f"Failed: {md_file.name} | Error: {e}")

def convert_single_md(input_folder: str, filename: str, output_folder: str):
    """
    Convert a specific Markdown file from input_folder to PDF in output_folder.

    Args:
        input_folder (str): Folder containing markdown files
        filename (str): Name of the .md file (with or without .md)
        output_folder (str): Folder to save the PDF
    """
    input_path = Path(input_folder)
    output_path = Path(output_folder)

    # Ensure .md extension
    if not filename.lower().endswith(".md"):
        filename += ".md"

    md_file = input_path / filename

    if not md_file.is_file():
        raise FileNotFoundError(f"File not found: {md_file}")

    output_path.mkdir(parents=True, exist_ok=True)

    output_pdf = output_path / (md_file.stem + ".pdf")

    pypandoc.convert_file(
        str(md_file),
        to="pdf",
        outputfile=str(output_pdf),
        extra_args=["--pdf-engine=xelatex"]
    )

    print(f"Converted: {md_file.name} -> {output_pdf}")

if __name__ == "__main__":
    convert_single_md(
        input_folder="outputs",
        filename=f"{active_topic.get('title')}.md",
        output_folder="customer_files"
    )
