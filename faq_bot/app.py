import gradio as gr
from dotenv import load_dotenv

# The current implementation. `implementation/` is the simpler course version,
# kept as a baseline — switch the two lines to compare (it needs its own
# `ingest.py` run first; the two use different stores).
from pro_implementation.answer import answer_question, RagConfig, available_collections, default_collection
#from implementation.answer import answer_question

load_dotenv(override=True)

def format_context(context, config):
    result = "<h2 style='color: #ff7800;'>Relevant Context</h2>\n\n"
    result += (f"*{config.collection} · rewrite {'on' if config.rewrite else 'off'}"
               f" · rerank {'on' if config.rerank else 'off'}*\n\n")
    for doc in context:
        result += f"<span style='color: #ff7800;'>Source: {doc.metadata['source']}</span>\n\n"
        result += doc.page_content + "\n\n"
    return result

def message_text(content) -> str:
    """Flatten one Chatbot message's content to plain text.

    Gradio 6 rewrites `content` into a list of parts
    ([{"type": "text", "text": ...}]) as soon as the value passes through the
    Chatbot component, while the RAG core takes plain strings. Keeping the
    conversion here means answer.py stays independent of the UI framework.
    """
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") for part in content if part.get("type") == "text"
    )


def plain_history(history: list[dict]) -> list[dict]:
    """Chatbot messages -> {"role", "content"} dicts with string content."""
    return [
        {"role": m["role"], "content": message_text(m["content"])} for m in history
    ]


def chat(history, collection, rewrite, rerank):
    """Answer the last message with the settings currently chosen in the UI.
    Unlike the evaluation, the chat passes the earlier turns as history — so
    query rewriting can turn a follow-up into a complete question here."""
    config = RagConfig(collection=collection, rewrite=rewrite, rerank=rerank)
    messages = plain_history(history)
    last_message = messages[-1]["content"]
    prior = messages[:-1]
    answer, context = answer_question(last_message, prior, config=config)
    history.append({"role": "assistant", "content": answer})
    return history, format_context(context, config)

def main():
    def put_message_in_chatbot(message, history):
        return "", history + [{"role": "user", "content": message}]

    theme = gr.themes.Soft(font=["Inter", "system-ui", "sans-serif"])

    with gr.Blocks(title="SW Waiblingen Expert Assistant") as ui:
        gr.Markdown("# 🏢 SW Waiblingen Expert Assistant\nAsk me anything about SW Waiblingen!")

        # Same three settings as the evaluation dashboard; each message is
        # answered with whatever is selected when it is sent.
        choices = available_collections()
        with gr.Row():
            collection_dd = gr.Dropdown(
                choices=choices,
                value=default_collection([name for _, name in choices]),
                label="🗄️ Vector store",
                info="Which chunking method to answer from",
                scale=2,
            )
            rewrite_cb = gr.Checkbox(
                value=RagConfig().rewrite,
                label="✍️ Query rewriting",
                info="Rewrite the question using the conversation so far (1 extra LLM call)",
                scale=1,
            )
            rerank_cb = gr.Checkbox(
                value=RagConfig().rerank,
                label="🔀 Reranking",
                info="Let the LLM reorder the retrieved chunks (1 extra LLM call)",
                scale=1,
            )

        with gr.Row():
            with gr.Column(scale=1):
                chatbot = gr.Chatbot(
                    label="💬 Conversation", height=600
                )
                message = gr.Textbox(
                    label="Your Question",
                    placeholder="Ask anything about SW Waiblingen...",
                    show_label=False,
                )

            with gr.Column(scale=1):
                context_markdown = gr.Markdown(
                    label="📚 Retrieved Context",
                    value="*Retrieved context will appear here*",
                    container=True,
                    height=600,
                )

        message.submit(
            put_message_in_chatbot, inputs=[message, chatbot], outputs=[message, chatbot]
        ).then(chat, inputs=[chatbot, collection_dd, rewrite_cb, rerank_cb],
               outputs=[chatbot, context_markdown])

    # Gradio 6 moved `theme` from the Blocks constructor to launch().
    ui.launch(inbrowser=True, theme=theme)

if __name__ == "__main__":
    main()