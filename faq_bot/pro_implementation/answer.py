from openai import OpenAI
from dotenv import load_dotenv
from chromadb import PersistentClient
from litellm import completion
from pydantic import BaseModel, Field
from pathlib import Path
from tenacity import retry, wait_exponential, stop_after_attempt
from functools import lru_cache

# The one prompt meant to be edited lives in prompts.py — see that file.
# Two import styles have to work: `import answer` with this folder on sys.path
# (app.py, a notebook, ingest-side scripts), and
# `import faq_bot.pro_implementation.answer` as a package (evaluation/eval.py).
# A plain import fails in the second case, a relative import in the first.
try:
    from prompts import SYSTEM_PROMPT
except ModuleNotFoundError:            # imported as part of the faq_bot package
    from .prompts import SYSTEM_PROMPT
import warnings
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")


load_dotenv(override=True)

MODEL = "openai/gpt-4.1-nano"
DB_NAME = str(Path(__file__).parent / "preprocessed_db")
#KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent.parent / "outputs/clean"
#SUMMARIES_PATH = Path(__file__).parent.parent / "summarieas"

embedding_model = "text-embedding-3-large"
wait = wait_exponential(multiplier=1, min=10, max=240)

openai = OpenAI()

@lru_cache
def get_collection(name):
    return PersistentClient(path=DB_NAME).get_collection(name)

RETRIEVAL_K = 10
FINAL_K = 5

class Result(BaseModel):
    page_content: str
    metadata: dict


class RankOrder(BaseModel):
    order: list[int] = Field(
        description="The order of relevance of chunks, from most relevant to least relevant, by chunk id number"
    )

class RagConfig(BaseModel):
    collection: str = "whole_file"
    rewrite: bool = False
    rerank: bool = False

@retry(wait=wait, stop=stop_after_attempt(3))
def rerank(question, chunks):
    system_prompt = """
You are a document re-ranker.
You are provided with a question and a list of relevant chunks of text from a query of a knowledge base.
The chunks are provided in the order they were retrieved; this should be approximately ordered by relevance, but you may be able to improve on that.
You must rank order the provided chunks by relevance to the question, with the most relevant chunk first.
Reply only with the list of ranked chunk ids, nothing else. Include all the chunk ids you are provided with, reranked.
"""
    user_prompt = f"The user has asked the following question:\n\n{question}\n\nOrder all the chunks of text by relevance to the question, from most relevant to least relevant. Include all the chunk ids you are provided with, reranked.\n\n"
    user_prompt += "Here are the chunks:\n\n"
    for index, chunk in enumerate(chunks):
        user_prompt += f"# CHUNK ID: {index + 1}:\n\n{chunk.page_content}\n\n"
    user_prompt += "Reply only with the list of ranked chunk ids, nothing else."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    response = completion(model=MODEL, messages=messages, response_format=RankOrder)
    reply = response.choices[0].message.content
    order = RankOrder.model_validate_json(reply).order
    return [chunks[i - 1] for i in order]


def make_rag_messages(question, history, chunks):
    context = "\n\n".join(
        f"Extract from {chunk.metadata['source']}:\n{chunk.page_content}" for chunk in chunks
    )
    system_prompt = SYSTEM_PROMPT.format(context=context)
    return (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": question}]
    )


@retry(wait=wait, stop=stop_after_attempt(3))
def rewrite_query(question, history=None):
    """Rewrite the user's question to be a more specific question that is more likely to surface relevant content in the Knowledge Base."""
    if history is None:
        history = []

    message = f"""
You are in a conversation with a user.
You are about to look up information in a Knowledge Base to answer the user's question.

This is the history of your conversation so far with the user:
{history}

And this is the user's current question:
{question}

Since the conversation is contextual, understand the meaning of the user question and add details based on the history.
Condense everything in a single contextually-rich VERY short and specific question, most likely to surface content.

EXAMPLE 1:
user: Who is the founder? -> Query: who is the founder?
assistant: The founder is FooBar
user: What role covers? -> Query: What role FooBar covers? (Revelant context found, add details into the user query.)

EXAMPLE 2:
user: Who is the founder? -> Query: who is the founder?
assistant: The founder is FooBar
user: What kind of business do you do? -> Query: What kind of business do you do? (No revelant context, query stays absolut the same.)
...

IMPORTANT: Respond ONLY with the precise knowledgebase query, in the SAME language as the user's question, nothing else. 
"""
    response = completion(model=MODEL, messages=[{"role": "system", "content": message}])
    return response.choices[0].message.content


def merge_chunks(chunks, reranked):
    merged = chunks[:]
    existing = [chunk.page_content for chunk in chunks]
    for chunk in reranked:
        if chunk.page_content not in existing:
            merged.append(chunk)
    return merged


def fetch_context_unranked(question, collection_name):
    query = openai.embeddings.create(model=embedding_model, input=[question]).data[0].embedding
    results = get_collection(collection_name).query(query_embeddings=[query], n_results=RETRIEVAL_K)
    chunks = []
    for result in zip(results["documents"][0], results["metadatas"][0]):
        chunks.append(Result(page_content=result[0], metadata=result[1]))
    return chunks


def fetch_context(original_question, history=None, config=None):
    config = config or RagConfig()
    chunks1 = fetch_context_unranked(original_question, config.collection)
    chunks = chunks1
    if config.rewrite:
        rewritten_question = rewrite_query(original_question, history)
        print(rewritten_question)
        chunks2 = fetch_context_unranked(rewritten_question, config.collection)
        chunks = merge_chunks(chunks1, chunks2)
    if config.rerank:
        reranked = rerank(original_question, chunks)
        chunks = reranked
    return chunks[:FINAL_K]


@retry(wait=wait, stop=stop_after_attempt(3))
def answer_question(question: str, history: list[dict] | None = None, config=None) -> tuple[str, list]:
    """
    Answer a question using RAG and return the answer and the retrieved context
    """
    config = config or RagConfig()
    history = history or []
    chunks = fetch_context(question, history, config)
    messages = make_rag_messages(question, history, chunks)
    response = completion(model=MODEL, messages=messages)
    return response.choices[0].message.content, chunks
