from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from chromadb import PersistentClient
from tqdm import tqdm
from litellm import completion
from multiprocessing import Pool
from tenacity import retry, wait_exponential
import glob
import re
from functools import partial
from datetime import datetime
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownTextSplitter
import argparse
import tiktoken
from tenacity import retry, wait_exponential, stop_after_attempt
import warnings
warnings.filterwarnings("ignore")


load_dotenv(override=True)

# The two LLM methods have their own model, so either can be changed alone.
CHUNK_MODEL = "openai/gpt-4.1-mini"     # llm: chooses split points + headline/summary
SUMMARY_MODEL = "openai/gpt-5-mini"   # whole_summary / whole_questions: header per page

DB_NAME = str(Path(__file__).parent / "preprocessed_db")
embedding_model = "text-embedding-3-large"
#embedding_model = "text-embedding-ada-002"
KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent.parent / "outputs/clean"
AVERAGE_CHUNK_SIZE = 1000
# llm method: each chunk runs on this many characters into the next one
OVERLAP_CHARS = 200
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100 
# text-embedding-3-large rejects any single input above this; see MAX in the
# API error "maximum input length is 8192 tokens".
MAX_EMBED_TOKENS = 8192
# Fallback chunk size for the one page that busts the cap. 20k chars is ~7.5k
# tokens on this German corpus (measured: 2.68 chars/token), so it stays under
# the cap while keeping the pieces as close to "whole file" as possible.
WHOLE_FALLBACK_CHARS = 20000
wait = wait_exponential(multiplier=1, min=10, max=240)


WORKERS = 10

openai = OpenAI()


class Result(BaseModel):
    page_content: str
    metadata: dict


class Chunk(BaseModel):
    """What the LLM returns for one chunk. It never retypes the text itself:
    copying whole pages back cost tokens and corrupted 15 of 243 chunks
    (broken umlauts, inserted soft hyphens, changed words). The LLM only says
    where a chunk starts; the text is cut from the real page (`cut_chunks`)."""
    headline: str = Field(description="A brief heading for this chunk, typically a few words, that is most likely to be surfaced in a query")
    summary: str = Field(description="A few sentences summarizing the content of this chunk to answer common questions, including the topic and category of this chunk")
    start: str = Field(description="The first 8 to 15 words of this chunk, copied character for character from the document, so the chunk can be located in it")


class Chunks(BaseModel):
    chunks: list[Chunk]


WORD = re.compile(r"\w+")


def find_start(text, start, after):
    """Position of `start` in `text` at or after `after`; None if not found.

    Matched on words only, ignoring whitespace, case and markdown/punctuation
    (`**`, `|`, `#`, quotes), which the LLM tends to drop when copying a start.
    A broken umlaut or soft hyphen splits a word differently, so such a start
    still does not match. The position is moved back to the line start when
    only markup precedes the word, so a heading's `## ` stays with its chunk."""
    words = [w.lower() for w in WORD.findall(start)]
    if not words or "\x00" in start:
        return None
    page = [(m.start(), m.group().lower()) for m in WORD.finditer(text)]
    tokens = [w for _, w in page]
    for i in range(len(page) - len(words) + 1):
        if page[i][0] >= after and tokens[i:i + len(words)] == words:
            pos = page[i][0]
            line_start = text.rfind("\n", 0, pos) + 1
            if not WORD.search(text, line_start, pos):
                pos = line_start
            return max(pos, after)
    return None


def cut_chunks(document, chunks):
    """Cut the page at the LLM's start positions; each chunk runs on
    OVERLAP_CHARS into the next one. The first chunk always starts at 0 and the
    last ends at the page end, so no text can be lost. Raises ValueError if a
    start is not in the page, which makes @retry ask the LLM again."""
    text = document["text"]
    positions, after = [], 0
    for chunk in chunks:
        pos = find_start(text, chunk.start, after)
        if pos is None:
            raise ValueError(f"{document['source']}: start not found: {chunk.start!r}")
        positions.append(pos)
        after = pos + 1
    positions[0] = 0
    ends = positions[1:] + [len(text)]

    metadata = {"source": document["source"], "type": document["type"]}
    results = []
    for chunk, begin, end in zip(chunks, positions, ends):
        if end < len(text):
            space = text.find(" ", end + OVERLAP_CHARS)
            end = len(text) if space == -1 else space
        original = text[begin:end].strip()
        results.append(Result(page_content=chunk.headline + "\n\n" + chunk.summary + "\n\n" + original, metadata=metadata))
    return results


def fetch_documents():
    """Similar to LangChain DirectoryLoader"""

    documents = []
    filenames = glob.glob(str(KNOWLEDGE_BASE_PATH)+"/*.md")
    #import pdb; pdb.set_trace()

    for filename in filenames:
        doc_type = Path(filename).stem
        with open(filename, "r", encoding="utf-8") as f:
            documents.append({"type": doc_type, "source": Path(filename).stem, "text": f.read()})

    print(f"Loaded {len(documents)} documents")

    return documents

def make_prompt(document):
    how_many = max(1,(len(document["text"]) // AVERAGE_CHUNK_SIZE))
    return f"""
    You take a document and you split the document into overlapping chunks for a KnowledgeBase.

    The document is from the shared drive of a company.
    The document from the category: {document["type"]}
    The document has been retrieved from: {document["source"]}

    A chatbot will use these chunks to answer questions about the company.
    You should divide up the document as you see fit, at points where the topic changes. The chunks follow each other in document order and together cover the entire document.
    This document should probably be split into {how_many} chunks, but you can have more or less as appropriate.
    Do not copy the text of the chunks; the text is cut from the document automatically. Overlap between chunks is also added automatically.

    For each chunk, provide a headline, a summary, and its start: the first 8 to 15 words of the chunk, copied character for character from the document (same spelling, umlauts, punctuation and markdown symbols), so the chunk can be found in the document.
    The first chunk starts at the beginning of the document.
    Write the headline and the summary in German, the language of the document, so they match the words customers use.

    Here is the document:

    {document["text"]}

    Respond with the chunks.
    """


def make_messages(document):
    return [
        {"role": "user", "content": make_prompt(document)},
    ]


@retry(wait=wait, stop=stop_after_attempt(3))
def process_document(document):
    messages = make_messages(document)
    response = completion(model=CHUNK_MODEL, messages=messages, response_format=Chunks)
    reply = response.choices[0].message.content
    doc_as_chunks = Chunks.model_validate_json(reply).chunks
    return cut_chunks(document, doc_as_chunks)


def process_document_or_fallback(document):
    """LLM chunking; if it still fails after the retries, split this one page
    with the markdown splitter instead of aborting the whole run."""
    try:
        return process_document(document), None
    except Exception as e:
        # RetryError only says "gave up"; the reason is the last attempt's error
        e = e.last_attempt.exception() if hasattr(e, "last_attempt") else e
        splitter = MarkdownTextSplitter(chunk_size=AVERAGE_CHUNK_SIZE, chunk_overlap=OVERLAP_CHARS)
        metadata = {"source": document["source"], "type": document["type"]}
        pieces = splitter.split_text(document["text"])
        return [Result(page_content=p, metadata=metadata) for p in pieces], f"{document['source']}: {e}"


def create_chunks_llm(documents):
    """
    Create chunks using a number of workers in parallel.
    If you get a rate limit error, set the WORKERS to 1.
    """
    chunks, fallbacks = [], []
    with Pool(processes=WORKERS) as pool:
        for result, failure in tqdm(pool.imap_unordered(process_document_or_fallback, documents), total=len(documents)):
            chunks.extend(result)
            if failure:
                fallbacks.append(failure)
    for failure in fallbacks:
        print(f"markdown fallback: {failure}")
    print(f"{len(fallbacks)} of {len(documents)} pages fell back to the markdown splitter")
    return chunks

def create_chunks_recursive(documents):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    # Be aware: chunk is list[Document object of Langchain] 
    chunks = text_splitter.split_documents(create_chunks_whole_file(documents))
    return chunks

def create_chunks_markdown(documents):
    text_splitter = MarkdownTextSplitter()
    # Be aware: chunk is list[Document object of Langchain] 
    chunks = text_splitter.split_documents(create_chunks_whole_file(documents))
    return chunks

def create_chunks_whole_file(documents):
    """Return the documents as is, except those too large to embed.

    A page over the embedding model's hard per-input cap cannot be one vector,
    so it is split on its markdown headings instead (the leftover
    Service_Abfall-ABC.md from crawler-ahk once was). The current corpus has no
    such page; the largest, Netze_Uebersicht-Netze, is ~7.2k tokens.
    """
    enc = tiktoken.get_encoding("cl100k_base")
    splitter = MarkdownTextSplitter(
        chunk_size=WHOLE_FALLBACK_CHARS, chunk_overlap=CHUNK_OVERLAP
    )

    results = []
    for document in documents:
        metadata = {"source": document["source"], "type": document["type"]}
        text = document["text"]
        if len(enc.encode(text)) <= MAX_EMBED_TOKENS:
            results.append(Result(page_content=text, metadata=metadata))
            continue
        pieces = splitter.split_text(text)
        print(f"{Path(document['source']).name}: over the {MAX_EMBED_TOKENS}-token "
              f"embedding cap, split into {len(pieces)} chunks")
        results.extend(Result(page_content=p, metadata=metadata) for p in pieces)
    return results

class PageSummary(BaseModel):
    headline: str = Field(description="A brief heading for the whole page, a few words, in German")
    summary: str = Field(description="A few sentences in German summarizing the page: its topic, the facts it gives and the customer questions it answers")

    def header(self):
        return self.headline + "\n\n" + self.summary


class PageQuestions(BaseModel):
    headline: str = Field(description="A brief heading for the whole page, a few words, in German")
    questions: list[str] = Field(description="3 to 5 questions in German that a customer could ask and that this page answers, phrased the way a customer would ask them")

    def header(self):
        return self.headline + "\n\n" + "\n".join("- " + q for q in self.questions)


# What the LLM writes on top of each whole page: schema + task. `whole_summary`
# describes the page; `whole_questions` lists questions it answers, which are
# phrased like the user's question and copy no prices or numbers.
HEADERS = {
    "summary": (PageSummary,
                "write a headline and a short summary. Name the topics the page covers and the questions it answers, using the words a customer would use."),
    "questions": (PageQuestions,
                  "write a headline and 3 to 5 questions a customer could ask that this page answers. Phrase them like a customer would, in everyday words, covering the page's different topics. Do not put prices, numbers or other facts from the page into the questions."),
}


def make_header_messages(piece, kind):
    task = HEADERS[kind][1]
    return [{"role": "user", "content": f"""
    You read a page of a German utility company's website (Stadtwerke Waiblingen) and {task}
    A chatbot will retrieve the page by comparing customer questions with what you write and the page itself.
    Do not invent facts. Write in German, the language of the page.

    The page has been retrieved from: {piece.metadata["source"]}

    Here is the page:

    {piece.page_content}
    """}]


@retry(wait=wait, stop=stop_after_attempt(3))
def add_header(piece, kind):
    schema = HEADERS[kind][0]
    response = completion(model=SUMMARY_MODEL, messages=make_header_messages(piece, kind), response_format=schema)
    header = schema.model_validate_json(response.choices[0].message.content).header()
    return Result(page_content=header + "\n\n" + piece.page_content, metadata=piece.metadata)


def add_header_or_plain(piece, kind):
    """If the header still fails after the retries, keep the page without one."""
    try:
        return add_header(piece, kind), None
    except Exception as e:
        e = e.last_attempt.exception() if hasattr(e, "last_attempt") else e
        return piece, f"{piece.metadata['source']}: {e}"


def create_chunks_whole_with_header(documents, kind):
    """`whole`, plus an LLM-written header (see HEADERS) on top of each page.

    The page text itself is untouched (it is never sent back by the LLM), so
    this isolates one question: does the header help retrieval on top of the
    `whole` baseline? One LLM call per page, in parallel."""
    pieces = create_chunks_whole_file(documents)
    chunks, failures = [], []
    with Pool(processes=WORKERS) as pool:
        for chunk, failure in tqdm(pool.imap(partial(add_header_or_plain, kind=kind), pieces), total=len(pieces)):
            chunks.append(chunk)
            if failure:
                failures.append(failure)
    for failure in failures:
        print(f"no {kind}: {failure}")
    print(f"{len(failures)} of {len(pieces)} pages stored without a {kind}")
    return chunks


def create_chunks_whole_summary(documents):
    return create_chunks_whole_with_header(documents, "summary")


def create_chunks_whole_questions(documents):
    return create_chunks_whole_with_header(documents, "questions")


def create_embeddings(chunks, collection_name, build_info=None):
    """`build_info` is stored as the collection's metadata, so an evaluation
    result can say how the collection it measured was built."""
    chroma = PersistentClient(path=DB_NAME)
    if collection_name in [c.name for c in chroma.list_collections()]:
        chroma.delete_collection(collection_name)

    collection = chroma.get_or_create_collection(collection_name, metadata=build_info or None)
    texts = [chunk.page_content for chunk in chunks]
    emb = openai.embeddings.create(model=embedding_model, input=texts).data
    vectors = [e.embedding for e in emb]
    ids = [str(i) for i in range(len(chunks))]
    metas = [chunk.metadata for chunk in chunks]

    collection.add(ids=ids, embeddings=vectors, documents=texts, metadatas=metas)
    print(f"Vectorstore created with {collection.count()} documents")

# The chunking methods: --method name -> (chunking function, collection).
# Each method writes its own collection, so all of them can live side by side
# and be compared in the dashboard; re-running one rebuilds only that one.
METHODS = {
    "whole": (create_chunks_whole_file, "whole_file",
              "One chunk per page: every file in outputs/clean/ is embedded whole, unchanged (a page above the embedding cap would be split on its headings)."),
    "markdown": (create_chunks_markdown, "markdown",
                 "LangChain MarkdownTextSplitter with its defaults (4000 characters, 200 overlap), splitting at markdown headings."),
    "recursive": (create_chunks_recursive, f"recursive_Chunksize_{CHUNK_SIZE}_Overlap_{CHUNK_OVERLAP}",
                  f"LangChain RecursiveCharacterTextSplitter: fixed-size chunks of {CHUNK_SIZE} characters with {CHUNK_OVERLAP} overlap."),
    "llm": (create_chunks_llm, "llm_chunks",
            f"An LLM chooses where each page is split and writes a headline and summary per chunk; the chunk text is cut from the page itself ({OVERLAP_CHARS} characters overlap)."),
    "whole_summary": (create_chunks_whole_summary, "whole_file_summary",
                      "The whole page, with an LLM-written German headline and summary on top. The page text is unchanged."),
    "whole_questions": (create_chunks_whole_questions, "whole_file_questions",
                        "The whole page, with an LLM-written German headline and 3-5 example customer questions on top. The page text is unchanged."),
}


def describe_collection(collection_name):
    """(method, description) of a collection, by its name; for reports."""
    for method, (_, name, description) in METHODS.items():
        if name == collection_name:
            return method, description
    return "unknown", "Not built by any method in ingest.METHODS (renamed or removed method?)."


def parse_args():
    parser = argparse.ArgumentParser(description="Build the FAQ-bot vector store.")
    parser.add_argument("--method", choices=[*METHODS, "all"], default="whole", help="chunking method, or 'all' to build every collection; default: %(default)s")
    parser.add_argument("--list", action="store_true", help="list collections and exit")
    parser.add_argument("--delete", metavar="NAME", help="delete a collection and exit")
    return parser.parse_args()


def build(method, documents):
    create, collection_name, description = METHODS[method]
    chunks = create(documents)
    print(f"{method}: {len(documents)} docs -> {len(chunks)} chunks -> collection {collection_name!r}")
    build_info = {"method": method, "description": description,
                  "built": datetime.now().isoformat(timespec="seconds"),
                  "embedding_model": embedding_model}
    if method == "llm":
        build_info["llm_model"] = CHUNK_MODEL
    elif method.startswith("whole_") and method != "whole":
        build_info["llm_model"] = SUMMARY_MODEL
    create_embeddings(chunks, collection_name, build_info)

def delete_collection(name, missing_ok=False):
    chroma = PersistentClient(path=DB_NAME)
    existing = [c.name for c in chroma.list_collections()]
    if name not in existing:
        if missing_ok:
            return
        raise SystemExit(f"No collection {name!r}. Available: {existing}")
    count = chroma.get_collection(name).count()
    chroma.delete_collection(name)
    print(f"Deleted collection {name!r} ({count} chunks)")


if __name__ == "__main__":
    args = parse_args()
    if args.list:
        for c in PersistentClient(path=DB_NAME).list_collections():
            print(f"{c.name:45s} {c.count():5d} chunks")
        raise SystemExit(0)
    if args.delete:
        delete_collection(args.delete)
        raise SystemExit(0)
    documents = fetch_documents()
    for method in (METHODS if args.method == "all" else [args.method]):
        build(method, documents)
    print("Ingestion complete")
    #import pdb; pdb.set_trace()