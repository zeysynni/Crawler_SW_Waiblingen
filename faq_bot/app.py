import gradio as gr
from dotenv import load_dotenv

from implementation.answer import answer_question

load_dotenv(override=True)

def format_context(context):
    result = "<h2 style='color: #ff7800;'>Relevant Context</h2>\n\n"
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


def chat(history):
    messages = plain_history(history)
    last_message = messages[-1]["content"]
    prior = messages[:-1]
    answer, context = answer_question(last_message, prior)
    history.append({"role": "assistant", "content": answer})
    return history, format_context(context)

def main():
    def put_message_in_chatbot(message, history):
        return "", history + [{"role": "user", "content": message}]

    theme = gr.themes.Soft(font=["Inter", "system-ui", "sans-serif"])

    with gr.Blocks(title="SW Waiblingen Expert Assistant", theme=theme) as ui:
        gr.Markdown("# 🏢 SW Waiblingen Expert Assistant\nAsk me anything about SW Waiblingen!")

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
        ).then(chat, inputs=chatbot, outputs=[chatbot, context_markdown])

    ui.launch(inbrowser=True)#

if __name__ == "__main__":
    main()