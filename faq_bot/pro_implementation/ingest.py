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
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownTextSplitter
import argparse
import tiktoken
import warnings
warnings.filterwarnings("ignore")


load_dotenv(override=True)

MODEL = "openai/gpt-4.1-nano"

DB_NAME = str(Path(__file__).parent / "preprocessed_db")
embedding_model = "text-embedding-3-large"
KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent.parent / "outputs/clean"
AVERAGE_CHUNK_SIZE = 1000
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
    headline: str = Field(description="A brief heading for this chunk, typically a few words, that is most likely to be surfaced in a query")
    summary: str = Field(description="A few sentences summarizing the content of this chunk to answer common questions, including the topic and category of this chunk")
    original_text: str = Field(description="The original text of this chunk from the provided document, exactly as is, not changed in any way")

    def as_result(self, document):
        metadata = {"source": document["source"], "type": document["type"]}
        return Result(page_content=self.headline + "\n\n" + self.summary + "\n\n" + self.original_text, metadata=metadata)


class Chunks(BaseModel):
    chunks: list[Chunk]


def fetch_documents():
    """Similar to LangChain DirectoryLoader"""

    documents = []
    filenames = glob.glob(str(KNOWLEDGE_BASE_PATH)+"/*.md")
    #import pdb; pdb.set_trace()

    for filename in filenames:
        doc_type = Path(filename).stem
        with open(filename, "r", encoding="utf-8") as f:
            documents.append({"type": doc_type, "source": filename, "text": f.read()})

    print(f"Loaded {len(documents)} documents")

    return documents

def make_prompt(document):
    how_many = (len(document["text"]) // AVERAGE_CHUNK_SIZE) + 1
    return f"""
You take a document and you split the document into overlapping chunks for a KnowledgeBase.

The document is from the shared drive of a company.
The document from the category: {document["type"]}
The document has been retrieved from: {document["source"]}

A chatbot will use these chunks to answer questions about the company.
You should divide up the document as you see fit, being sure that the entire document is returned in the chunks - don't leave anything out.
This document should probably be split into {how_many} chunks, but you can have more or less as appropriate.
There should be overlap between the chunks as appropriate; typically about 25% overlap or about 50 words, so you have the same text in multiple chunks for best retrieval results.

For each chunk, you should provide a headline, a summary, and the original text of the chunk.
Together your chunks should represent the entire document with overlap.

Here is the document:

{document["text"]}

Respond with the chunks.
"""


def make_messages(document):
    return [
        {"role": "user", "content": make_prompt(document)},
    ]


@retry(wait=wait)
def process_document(document):
    messages = make_messages(document)
    response = completion(model=MODEL, messages=messages, response_format=Chunks)
    reply = response.choices[0].message.content
    doc_as_chunks = Chunks.model_validate_json(reply).chunks
    return [chunk.as_result(document) for chunk in doc_as_chunks]


def create_chunks_llm(documents):
    """
    Create chunks using a number of workers in parallel.
    If you get a rate limit error, set the WORKERS to 1.
    """
    chunks = []
    with Pool(processes=WORKERS) as pool:
        for result in tqdm(pool.imap_unordered(process_document, documents), total=len(documents)):
            chunks.extend(result)
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

    One document is over the embedding model's hard per-input cap
    (Service_Abfall-ABC.md, the A-Z waste directory, ~23.7k tokens). It cannot
    be one vector, so it is split on its markdown headings instead. Every other
    page is still stored whole, so the collection name stays accurate for 81
    of the 82 files.
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

def create_embeddings(chunks, collection_name):
    chroma = PersistentClient(path=DB_NAME)
    if collection_name in [c.name for c in chroma.list_collections()]:
        chroma.delete_collection(collection_name)

    collection = chroma.get_or_create_collection(collection_name)
    texts = [chunk.page_content for chunk in chunks]
    emb = openai.embeddings.create(model=embedding_model, input=texts).data
    vectors = [e.embedding for e in emb]
    ids = [str(i) for i in range(len(chunks))]
    metas = [chunk.metadata for chunk in chunks]

    collection.add(ids=ids, embeddings=vectors, documents=texts, metadatas=metas)
    print(f"Vectorstore created with {collection.count()} documents")

def parse_args():
    parser = argparse.ArgumentParser(description="Build the FAQ-bot vector store.")
    parser.add_argument("--method", choices=["whole", "recursive", "markdown", "llm"], default="whole", help="chunking method, default: %(default)s")
    parser.add_argument("--list", action="store_true", help="list collections and exit")
    parser.add_argument("--delete", metavar="NAME", help="delete a collection and exit")
    return parser.parse_args()

def collection_for(args):
    """Each method gets its own collection, so runs don't overwrite each other."""
    return {"whole": "whole_file", 
    "llm": "llm_chunks", 
    "recursive": f"recursive_Chunksize_{CHUNK_SIZE}_Overlap_{CHUNK_OVERLAP}", 
    "markdown": "markdown"}[args.method]

def create_chunks(documents, args):
    if args.method == "whole":
        return create_chunks_whole_file(documents)
    if args.method == "recursive":
        return create_chunks_recursive(documents)
    if args.method == "markdown":
        return create_chunks_markdown(documents)
    return create_chunks_llm(documents)

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
    print("len of DOCUMENTS:", len(documents))
    chunks = create_chunks(documents, args)
    collection_name = collection_for(args)
    print(f"{args.method}: {len(documents)} docs -> {len(chunks)} chunks -> collection {collection_name!r}")
    create_embeddings(chunks, collection_name)
    print("Ingestion complete")
