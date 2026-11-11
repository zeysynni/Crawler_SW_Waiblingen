import gradio as gr
import pandas as pd
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
import plotly.graph_objects as go
from sklearn.manifold import TSNE

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from faq_bot.pro_implementation import answer as rag
from faq_bot.pro_implementation.answer import DB_NAME, RagConfig, get_collection, available_collections, default_collection
from faq_bot.pro_implementation.ingest import describe_collection
from eval import evaluate_all_retrieval, evaluate_all_answers, MODEL as JUDGE_MODEL
import results

load_dotenv(override=True)

# Color coding thresholds - Retrieval
MRR_GREEN = 0.9
MRR_AMBER = 0.75
NDCG_GREEN = 0.9
NDCG_AMBER = 0.75
COVERAGE_GREEN = 90.0
COVERAGE_AMBER = 75.0

# Color coding thresholds - Answer (1-5 scale)
ANSWER_GREEN = 4.5
ANSWER_AMBER = 4.0

# Chunk-map colours: one colour per *category*, where a category is the first
# two underscore-segments of the source filename — "Privatkunden_Strom_
# Waermestrom.md" -> "Privatkunden Strom". Colouring by section alone put all
# 26 Privatkunden pages in one blue; this separates Strom from Baeder from
# Service.
CATEGORY_DEPTH = 2


def category_of(source: str) -> str:
    """`.../Privatkunden_Strom_Waermestrom.md` -> `Privatkunden Strom`."""
    return " ".join(Path(source).stem.split("_")[:CATEGORY_DEPTH])


def build_color_map(categories: list[str]) -> dict[str, str]:
    """Give every category its own colour.

    Three qualitative colormaps are stitched together because there are 39
    categories and `tab20` holds only 20 — one map on its own would wrap round
    and hand unrelated categories the same colour.

    `sorted()` rather than `set()` order: set iteration order changes between
    interpreter runs, which would repaint the whole map on every restart and
    make two screenshots impossible to compare.

    `to_hex()` because plotly needs CSS colour strings. Matplotlib's RGBA
    tuples are accepted without error and then silently ignored by plotly.js,
    which falls back to its default and paints every marker the same colour.
    """
    from matplotlib import pyplot as plt
    from matplotlib.colors import to_hex

    palette = [
        to_hex(colour)
        for name in ("tab20", "tab20b", "tab20c")
        for colour in plt.get_cmap(name).colors
    ]
    return {
        category: palette[i % len(palette)]
        for i, category in enumerate(sorted(set(categories)))
    }


def config_label(config: RagConfig) -> str:
    """One line naming the configuration a result was produced with."""
    return (
        f"{config.collection} &middot; rewrite "
        f"{'on' if config.rewrite else 'off'} &middot; rerank "
        f"{'on' if config.rerank else 'off'}"
    )


def config_banner(config: RagConfig) -> str:
    """The configuration line shown above every result block."""
    return (
        "<div style='margin-bottom: 12px; padding: 8px 12px; background-color: #eef2f7;"
        " border-radius: 6px; font-size: 13px; color: #445;'>"
        f"Configuration: <b>{config_label(config)}</b></div>"
    )


def get_color(value: float, metric_type: str) -> str:
    """Get color based on metric value and type."""
    if metric_type == "mrr":
        if value >= MRR_GREEN:
            return "green"
        elif value >= MRR_AMBER:
            return "orange"
        else:
            return "red"
    elif metric_type == "ndcg":
        if value >= NDCG_GREEN:
            return "green"
        elif value >= NDCG_AMBER:
            return "orange"
        else:
            return "red"
    elif metric_type == "coverage":
        if value >= COVERAGE_GREEN:
            return "green"
        elif value >= COVERAGE_AMBER:
            return "orange"
        else:
            return "red"
    elif metric_type in ["accuracy", "completeness", "relevance"]:
        if value >= ANSWER_GREEN:
            return "green"
        elif value >= ANSWER_AMBER:
            return "orange"
        else:
            return "red"
    return "black"


def format_metric_html(
    label: str,
    value: float,
    metric_type: str,
    is_percentage: bool = False,
    score_format: bool = False,
) -> str:
    """Format a metric with color coding."""
    color = get_color(value, metric_type)
    if is_percentage:
        value_str = f"{value:.1f}%"
    elif score_format:
        value_str = f"{value:.2f}/5"
    else:
        value_str = f"{value:.4f}"
    return f"""
    <div style="margin: 10px 0; padding: 15px; background-color: #f5f5f5; border-radius: 8px; border-left: 5px solid {color};">
        <div style="font-size: 14px; color: #666; margin-bottom: 5px;">{label}</div>
        <div style="font-size: 28px; font-weight: bold; color: {color};">{value_str}</div>
    </div>
    """


def run_retrieval_evaluation(collection_name, rewrite, rerank, progress=gr.Progress()):
    """Run the retrieval evaluation with the configuration chosen in the UI."""
    config = RagConfig(collection=collection_name, rewrite=rewrite, rerank=rerank)
    total_mrr = 0.0
    total_ndcg = 0.0
    total_coverage = 0.0
    category_mrr = defaultdict(list)
    questions = []
    count = 0

    for test, result, prog_value in evaluate_all_retrieval(config):
        count += 1
        total_mrr += result.mrr
        total_ndcg += result.ndcg
        total_coverage += result.keyword_coverage

        category_mrr[test.category].append(result.mrr)
        questions.append({"id": test.id, "question": test.question, "category": test.category,
                          "expected_sources": test.source, **result.model_dump()})

        # Update progress bar only
        progress(prog_value, desc=f"Evaluating test {count}...")

    # Calculate final averages
    avg_mrr = total_mrr / count
    avg_ndcg = total_ndcg / count
    avg_coverage = total_coverage / count

    # Create final summary metrics HTML
    final_html = f"""
    <div style="padding: 0;">
        {config_banner(config)}
        {format_metric_html("Mean Reciprocal Rank (MRR)", avg_mrr, "mrr")}
        {format_metric_html("Normalized DCG (nDCG)", avg_ndcg, "ndcg")}
        {format_metric_html("Keyword Coverage", avg_coverage, "coverage", is_percentage=True)}
        <div style="margin-top: 20px; padding: 10px; background-color: #d4edda; border-radius: 5px; text-align: center; border: 1px solid #c3e6cb;">
            <span style="font-size: 14px; color: #155724; font-weight: bold;">✓ Evaluation Complete: {count} tests</span>
        </div>
    </div>
    """

    # Create final bar chart data
    category_data = []
    for category, mrr_scores in category_mrr.items():
        avg_cat_mrr = sum(mrr_scores) / len(mrr_scores)
        category_data.append({"Category": category, "Average MRR": avg_cat_mrr})

    df = pd.DataFrame(category_data)

    saved_part = {
        "config": config.model_dump(), "tests": count,
        "averages": {"mrr": avg_mrr, "ndcg": avg_ndcg, "keyword_coverage": avg_coverage},
        "by_category": by_category(questions, ["mrr", "ndcg", "keyword_coverage"]),
        "questions": questions,
    }
    return final_html, df, saved_part


def run_answer_evaluation(collection_name, rewrite, rerank, progress=gr.Progress()):
    """Run the answer evaluation with the configuration chosen in the UI."""
    config = RagConfig(collection=collection_name, rewrite=rewrite, rerank=rerank)
    total_accuracy = 0.0
    total_completeness = 0.0
    total_relevance = 0.0
    category_accuracy = defaultdict(list)
    questions = []
    count = 0

    for test, result, prog_value, generated, sources in evaluate_all_answers(config):
        count += 1
        total_accuracy += result.accuracy
        total_completeness += result.completeness
        total_relevance += result.relevance

        category_accuracy[test.category].append(result.accuracy)
        questions.append({"id": test.id, "question": test.question, "category": test.category,
                          "expected_sources": test.source, "reference_answer": test.reference_answer,
                          "generated_answer": generated, "answer_sources": sources,
                          **result.model_dump()})

        # Update progress bar only
        progress(prog_value, desc=f"Evaluating test {count}...")

    # Calculate final averages
    avg_accuracy = total_accuracy / count
    avg_completeness = total_completeness / count
    avg_relevance = total_relevance / count

    # Create final summary metrics HTML
    final_html = f"""
    <div style="padding: 0;">
        {config_banner(config)}
        {format_metric_html("Accuracy", avg_accuracy, "accuracy", score_format=True)}
        {format_metric_html("Completeness", avg_completeness, "completeness", score_format=True)}
        {format_metric_html("Relevance", avg_relevance, "relevance", score_format=True)}
        <div style="margin-top: 20px; padding: 10px; background-color: #d4edda; border-radius: 5px; text-align: center; border: 1px solid #c3e6cb;">
            <span style="font-size: 14px; color: #155724; font-weight: bold;">✓ Evaluation Complete: {count} tests</span>
        </div>
    </div>
    """

    # Create final bar chart data
    category_data = []
    for category, accuracy_scores in category_accuracy.items():
        avg_cat_accuracy = sum(accuracy_scores) / len(accuracy_scores)
        category_data.append({"Category": category, "Average Accuracy": avg_cat_accuracy})

    df = pd.DataFrame(category_data)

    saved_part = {
        "config": config.model_dump(), "tests": count,
        "averages": {"accuracy": avg_accuracy, "completeness": avg_completeness, "relevance": avg_relevance},
        "by_category": by_category(questions, ["accuracy", "completeness", "relevance"]),
        "questions": questions,
    }
    return final_html, df, saved_part


def by_category(questions, metrics):
    """Average of each metric per question category, with the count."""
    groups = defaultdict(list)
    for q in questions:
        groups[q["category"]].append(q)
    return {cat: {"n": len(qs), **{m: sum(q[m] for q in qs) / len(qs) for m in metrics}}
            for cat, qs in groups.items()}


def run_settings(retrieval_part, answer_part):
    """Everything besides the three UI fields that decides a score."""
    collection_name = (retrieval_part or answer_part)["config"]["collection"]
    collection = get_collection(collection_name)
    built = collection.metadata or {}
    method, description = describe_collection(collection_name)
    return {
        "method": method,
        "method_description": description,
        "chunks_in_collection": collection.count(),
        "collection_built": built.get("built", "unknown (built before 2026-11-09)"),
        "collection_llm_model": built.get("llm_model", "none" if built else "unknown"),
        "embedding_model": rag.embedding_model,
        "answer_model": rag.MODEL,
        "rerank_model": rag.RERANK_MODEL,
        "judge_model": JUDGE_MODEL,
        "retrieval_k": rag.RETRIEVAL_K,
        "final_k": rag.FINAL_K,
    }


def save_results(retrieval_part, answer_part):
    """Save button: write what was run (not what the dropdown says now)."""
    if not retrieval_part and not answer_part:
        return "⚠️ Nothing to save yet — run the Retrieval and/or Answer evaluation first."
    written = results.save(retrieval_part, answer_part, run_settings(retrieval_part, answer_part))
    note = ""
    if retrieval_part and answer_part and results.config_key(retrieval_part["config"]) != results.config_key(answer_part["config"]):
        note = "\n\n⚠️ Retrieval and Answer were run with different settings, so they were saved as two runs."
    names = "\n".join(f"- `evaluation/results/{p.name}`" for p in written)
    return f"✅ Saved:\n{names}{note}"


def build_chunk_map(collection_name, progress=gr.Progress()):
    """Project every chunk in the vector store to 2D with t-SNE.

    One trace per doc_type, so the legend can be clicked to isolate a section.
    The heavy imports are deferred: a run that only looks at the metrics should
    not pay for scikit-learn or load the vector store.
    """

    progress(0.1, desc="Reading the vector store...")
    stored = get_collection(collection_name).get(
        include=["embeddings", "documents", "metadatas"]
    )
    vectors = np.array(stored["embeddings"])
    documents = stored["documents"]
    metadatas = stored["metadatas"]
    doc_types = [m.get("doc_type", "unknown") for m in metadatas]
    sources = [Path(m.get("source", "?")).name for m in metadatas]
    categories = [category_of(m.get("source", "?")) for m in metadatas]
    color_map = build_color_map(categories)

    progress(0.35, desc=f"Reducing {len(vectors)} chunks to 2D...")
    reduced = TSNE(n_components=2, random_state=42).fit_transform(vectors)

    progress(0.9, desc="Drawing...")
    fig = go.Figure()
    for category in sorted(set(categories)):
        idx = [i for i, c in enumerate(categories) if c == category]
        # legendgroup keeps a section's categories together in the legend and
        # lets one click toggle the whole section.
        section = category.split(" ")[0]
        fig.add_trace(
            go.Scatter(
                x=reduced[idx, 0],
                y=reduced[idx, 1],
                mode="markers",
                name=f"{category} ({len(idx)})",
                legendgroup=section,
                legendgrouptitle_text=section,
                marker=dict(size=6, color=color_map[category], opacity=0.85),
                text=[
                    f"{categories[i]}<br>{sources[i]}<br>{documents[i][:150]}..."
                    for i in idx
                ],
                hoverinfo="text",
            )
        )
    fig.update_layout(
        title=f"Chunk map — {collection_name}: {len(vectors)} chunks, t-SNE of the embeddings",
        xaxis_title="x",
        yaxis_title="y",
        height=600,
        margin=dict(r=20, b=10, l=10, t=40),
        legend=dict(title="Category", groupclick="toggleitem"),
    )
    return fig


def main():
    """Launch the Gradio evaluation app."""
    theme = gr.themes.Soft(font=["Inter", "system-ui", "sans-serif"])

    with gr.Blocks(title="RAG Evaluation Dashboard") as app:
        gr.Markdown("# 📊 RAG Evaluation Dashboard")
        gr.Markdown(
            "Evaluate retrieval and answer quality of the SW Waiblingen FAQ bot. "
            "The settings below apply to every section on this page."
        )

        # One configuration for the whole page: every button below reads these
        # three fields, so a result can never be produced with settings other
        # than the ones on screen.
        choices = available_collections()
        names = [value for _, value in choices]
        with gr.Row():
            collection_dd = gr.Dropdown(
                choices=choices,
                value=default_collection(names),
                label="🗄️ Vector store",
                info="Which chunking method to evaluate",
                scale=2,
            )
            rewrite_cb = gr.Checkbox(
                value=RagConfig().rewrite,
                label="✍️ Query rewriting",
                info="Also retrieve with an LLM-rewritten question (1 extra LLM call per question)",
                scale=1,
            )
            rerank_cb = gr.Checkbox(
                value=RagConfig().rerank,
                label="🔀 Reranking",
                info="Let the LLM reorder the retrieved chunks (1 extra LLM call per question)",
                scale=1,
            )

        # RETRIEVAL SECTION
        gr.Markdown("## 🔍 Retrieval Evaluation")

        retrieval_button = gr.Button("Run Evaluation", variant="primary", size="lg")

        with gr.Row():
            with gr.Column(scale=1):
                retrieval_metrics = gr.HTML(
                    "<div style='padding: 20px; text-align: center; color: #999;'>Click 'Run Evaluation' to start</div>"
                )

            with gr.Column(scale=1):
                retrieval_chart = gr.BarPlot(
                    x="Category",
                    y="Average MRR",
                    title="Average MRR by Category",
                    y_lim=[0, 1],
                    height=400,
                )

        # ANSWERING SECTION
        gr.Markdown("## 💬 Answer Evaluation")

        answer_button = gr.Button("Run Evaluation", variant="primary", size="lg")

        with gr.Row():
            with gr.Column(scale=1):
                answer_metrics = gr.HTML(
                    "<div style='padding: 20px; text-align: center; color: #999;'>Click 'Run Evaluation' to start</div>"
                )

            with gr.Column(scale=1):
                answer_chart = gr.BarPlot(
                    x="Category",
                    y="Average Accuracy",
                    title="Average Accuracy by Category",
                    y_lim=[1, 5],
                    height=400,
                )

        # CHUNK MAP SECTION
        gr.Markdown("## 🗺️ Chunk Map")
        gr.Markdown(
            "Every chunk in the vector store, projected to 2D with t-SNE. "
            "Chunks that sit close together were embedded as similar text. "
            "One colour per category (section + subsection); click a legend "
            "entry to isolate it, or a section heading to toggle the group."
        )

        chunk_button = gr.Button("Draw Chunk Map", variant="primary", size="lg")
        chunk_plot = gr.Plot(label="Vector store")

        # SAVE SECTION
        gr.Markdown("## 💾 Save results")
        gr.Markdown(
            "Writes the last Retrieval and Answer results — with the settings they "
            "were run with, the models, and every question with its answer and "
            "sources — to `evaluation/results/` as JSON and Markdown."
        )
        save_button = gr.Button("Save results", variant="secondary", size="lg")
        save_status = gr.Markdown()

        # Each result is kept together with the configuration it was produced
        # with, so changing the dropdown before saving cannot mislabel it.
        retrieval_state = gr.State(None)
        answer_state = gr.State(None)

        # Wire up the evaluations. `config_inputs` is the same three components
        # for every button, so the three sections are always comparable.
        config_inputs = [collection_dd, rewrite_cb, rerank_cb]

        retrieval_button.click(
            fn=run_retrieval_evaluation,
            inputs=config_inputs,
            outputs=[retrieval_metrics, retrieval_chart, retrieval_state],
        )

        answer_button.click(
            fn=run_answer_evaluation,
            inputs=config_inputs,
            outputs=[answer_metrics, answer_chart, answer_state],
        )

        chunk_button.click(fn=build_chunk_map, inputs=[collection_dd], outputs=chunk_plot)
        save_button.click(fn=save_results, inputs=[retrieval_state, answer_state], outputs=save_status)

    # Gradio 6 moved `theme` from the Blocks constructor to launch().
    # share=True opens a public gradio.live tunnel to this local app (72h).
    app.launch(inbrowser=True, share=True, theme=theme)


if __name__ == "__main__":
    main()
