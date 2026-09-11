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

The document is from the public webpages of a company called SW Waiblingen.
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
    """Return the documents as is"""
    def _as_result(document) -> Result:
        """Build one storage record from a document dict and a piece of its text."""
        return Result(
            page_content=document["text"],
            metadata={"source": document["source"], "type": document["type"]},)
    return [_as_result(doc) for doc in documents]

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


if __name__ == "__main__":
    args = parse_args()
    documents = fetch_documents()
    print("len of DOCUMENTS:", len(documents))
    chunks = create_chunks(documents, args)
    collection_name = collection_for(args)
    print(f"{args.method}: {len(documents)} docs -> {len(chunks)} chunks -> collection {collection_name!r}")
    create_embeddings(chunks, collection_name)
    print("Ingestion complete")
