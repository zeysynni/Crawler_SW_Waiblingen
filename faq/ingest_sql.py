import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import os
import csv
from mcp_params import sql_db_name

_DEFAULT_OUTPUTS_DIR = str(Path(__file__).parent.parent / "outputs")
_MEMORY_DIR = Path(__file__).parent / "memory"


def init_db(db_path: str = str(_MEMORY_DIR / f"{sql_db_name}.db")):
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,
            content TEXT
        )
    """)
    con.commit()
    con.close()


def ingest_md_files(
    folder: str = _DEFAULT_OUTPUTS_DIR,
    db_path: str = str(_MEMORY_DIR / f"{sql_db_name}.db"),
):
    con = sqlite3.connect(db_path)
    # Clear existing rows to prevent duplicates on re-run
    con.execute("DELETE FROM knowledge")
    files = [f for f in os.listdir(folder) if f.endswith(".md")]
    for fname in files:
        path = os.path.join(folder, fname)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        topic = fname.replace(".md", "")
        con.execute("INSERT INTO knowledge (topic, content) VALUES (?, ?)", (topic, content))
        print(f"Ingested {fname}")
    con.commit()
    export_to_csv(con)
    con.close()
    print("Done.")


def export_to_csv(con):
    cursor = con.cursor()
    cursor.execute("SELECT * FROM knowledge")
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]

    csv_path = _MEMORY_DIR / "knowledge_data.csv"
    with open(csv_path, "w", newline="") as csv_file:
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(columns)
        csv_writer.writerows(rows)
    print(f"Data has been written to '{csv_path}'.")


if __name__ == "__main__":
    init_db()
    ingest_md_files()
