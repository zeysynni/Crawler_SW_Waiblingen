"""Save an evaluation run to evaluation/results/ — one JSON + one Markdown file.

The JSON is the record: exact numbers, the configuration and models, and every
question with its retrieved sources, generated answer and judge verdict, so
runs can be compared later by a script. The Markdown is the same run for
reading, worst questions first. The folder is gitignored.
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
REPO = Path(__file__).parent.parent


def git_commit() -> str:
    """Short commit hash, with '+dirty' when the working tree has changes."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                               capture_output=True, text=True).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def config_key(config: dict) -> tuple:
    return (config["collection"], config["rewrite"], config["rerank"])


def file_stem(config: dict, when: datetime) -> str:
    onoff = lambda b: "on" if b else "off"
    return (f"{when:%Y-%m-%d_%H%M%S}_{config['collection']}"
            f"_rw-{onoff(config['rewrite'])}_rr-{onoff(config['rerank'])}")


def merge_questions(retrieval: dict | None, answer: dict | None) -> list[dict]:
    """One row per test question, retrieval and answer fields side by side."""
    rows = {}
    for part in (retrieval, answer):
        for q in (part or {}).get("questions", []):
            rows.setdefault(q["id"], {}).update(q)
    return [rows[i] for i in sorted(rows, key=lambda i: (i is None, i))]


def build_record(retrieval: dict | None, answer: dict | None, settings: dict) -> dict:
    """Both parts must come from the same configuration (checked by the caller)."""
    first = retrieval or answer
    return {
        "saved": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": first["config"],
        "settings": settings,
        "retrieval": {k: v for k, v in (retrieval or {}).items() if k not in ("questions", "config")} or None,
        "answer": {k: v for k, v in (answer or {}).items() if k not in ("questions", "config")} or None,
        "questions": merge_questions(retrieval, answer),
    }


def to_markdown(record: dict) -> str:
    c, s = record["config"], record["settings"]
    lines = [
        f"# Evaluation: {c['collection']} · rewrite {'on' if c['rewrite'] else 'off'} · rerank {'on' if c['rerank'] else 'off'}",
        "",
        f"Saved {record['saved']} · commit `{record['git_commit']}`",
        "",
        "| Setting | Value |", "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in s.items()]

    for part, metrics in (("Retrieval", ["mrr", "ndcg", "keyword_coverage"]),
                          ("Answer", ["accuracy", "completeness", "relevance"])):
        data = record[part.lower()]
        if not data:
            continue
        lines += ["", f"## {part} ({data['tests']} tests)", "",
                  "| " + " | ".join(metrics) + " |", "|" + "---|" * len(metrics),
                  "| " + " | ".join(f"{data['averages'][m]:.3f}" for m in metrics) + " |",
                  "", "| Category | n | " + " | ".join(metrics) + " |", "|---|---|" + "---|" * len(metrics)]
        for cat, v in sorted(data["by_category"].items()):
            lines.append(f"| {cat} | {v['n']} | " + " | ".join(f"{v[m]:.3f}" for m in metrics) + " |")

    # Worst first: low answer accuracy, then low MRR — those are the ones to read.
    def badness(q):
        return (q.get("accuracy", 6), q.get("mrr", 2))
    lines += ["", "## Questions (worst first)"]
    for q in sorted(record["questions"], key=badness):
        head = f"### #{q['id']} [{q['category']}] {q['question']}"
        scores = []
        if "mrr" in q:
            scores.append(f"MRR {q['mrr']:.2f} · keywords {q['keywords_found']}/{q['total_keywords']}")
        if "accuracy" in q:
            scores.append(f"accuracy {q['accuracy']:g} · completeness {q['completeness']:g} · relevance {q['relevance']:g}")
        lines += ["", head, "", " — ".join(scores), ""]
        if q.get("keywords_missing"):
            lines.append(f"- **Missing keywords:** {', '.join(q['keywords_missing'])}")
        if q.get("expected_sources"):
            lines.append(f"- **Expected sources:** {', '.join(q['expected_sources'])}")
        sources = q.get("retrieved_sources") or q.get("answer_sources") or []
        if sources:
            lines.append(f"- **Retrieved:** {', '.join(f'{i}. {src}' for i, src in enumerate(sources, 1))}")
        if "generated_answer" in q:
            lines += ["", f"**Reference:** {q['reference_answer']}", "",
                      f"**Answer:** {q['generated_answer']}", "",
                      f"**Judge:** {q['feedback']}"]
    return "\n".join(lines) + "\n"


def save(retrieval: dict | None, answer: dict | None, settings: dict) -> list[Path]:
    """Write one JSON + one MD per configuration. Results of the two tabs are
    combined only when they were produced with the same configuration."""
    parts = [p for p in (retrieval, answer) if p]
    if not parts:
        return []
    if len(parts) == 2 and config_key(retrieval["config"]) == config_key(answer["config"]):
        groups = [(retrieval, answer)]
    else:
        groups = [(retrieval, None), (None, answer)]
    RESULTS_DIR.mkdir(exist_ok=True)
    written = []
    for r, a in groups:
        if not (r or a):
            continue
        record = build_record(r, a, settings)
        stem = file_stem(record["config"], datetime.now())
        json_path = RESULTS_DIR / f"{stem}.json"
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        md_path = RESULTS_DIR / f"{stem}.md"
        md_path.write_text(to_markdown(record), encoding="utf-8")
        written += [json_path, md_path]
    return written
