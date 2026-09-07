from pathlib import Path
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.messages import SystemMessage, HumanMessage, convert_to_messages
from langchain_core.documents import Document

from dotenv import load_dotenv

load_dotenv(override=True)

MODEL = "gpt-5.6-luna"
#DB_NAME = str(Path(__file__).parent.parent/ "vector_db")
DB_NAME = str(Path(__file__).parent.parent/ "vector_db_big")

embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
RETRIEVAL_K = 5

SYSTEM_PROMPT = """
You are a knowledgeable, friendly assistant representing the german company SW Waiblingen.
You are chatting with a user about your company.
If relevant, use the given context to answer any question.
If you don't know the answer, say so.
Context:
{context}
"""

vectorstore = Chroma(persist_directory=DB_NAME, embedding_function=embeddings)
retriever = vectorstore.as_retriever()
llm = ChatOpenAI(temperature=0, model_name=MODEL)


def fetch_context(question: str) -> list[Document]:
    """
    Retrieve relevant context documents for a question.
    """
    return retriever.invoke(question, k=RETRIEVAL_K)


def combined_question(question: str, history: list[dict] | None = None) -> str:
    """
    Combine the user's earlier questions with the current one, so retrieval
    sees the whole thread. `history` is `{"role", "content"}` dicts with
    **string** content — the UI layer flattens Gradio's message parts before
    calling in here (see faq_bot/app.py).
    """
    prior = "\n".join(m["content"] for m in (history or []) if m["role"] == "user")
    return f"{prior}\n{question}" if prior else question


def answer_question(question: str, history: list[dict] | None = None) -> tuple[str, list[Document]]:
    """
    Answer the given question with RAG; return the answer and the context documents.
    """
    history = history or []
    combined = combined_question(question, history)
    docs = fetch_context(combined) # consider all the prior questions when retrieving
    context = "\n\n".join(doc.page_content for doc in docs)
    system_prompt = SYSTEM_PROMPT.format(context=context)
    messages = [SystemMessage(content=system_prompt)]
    messages.extend(convert_to_messages(history))
    messages.append(HumanMessage(content=question))
    response = llm.invoke(messages)
    return response.content, docs