import os
import glob
from pathlib import Path
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownTextSplitter
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings


from dotenv import load_dotenv


MODEL = "gpt-4.1-nano"

DB_NAME = str(Path(__file__).parent.parent/ "vector_db")
KNOWLEDGE_BASE = str(Path(__file__).parent.parent.parent / "outputs/clean")

load_dotenv(override=True)

embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 200

def fetch_documents():
    """Fetch all the .md files in kb. Files from other format will also be converted into .md."""
    documents = []
    loader = DirectoryLoader(KNOWLEDGE_BASE, glob="**/*.md", loader_cls=TextLoader, loader_kwargs={'encoding': 'utf-8'})
    folder_docs = loader.load()
    for doc in folder_docs:
        doc.metadata["doc_type"] = Path(doc.metadata['source']).stem.split('_')[0]
        #print(Path(doc.metadata['source']).stem.split('_')[0])
        documents.append(doc)
    return documents


def create_chunks(documents):
    #text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=200)
    #chunks = text_splitter.split_documents(documents)

    #text_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    text_splitter = MarkdownTextSplitter()
    chunks = text_splitter.split_documents(documents)
    return chunks


def create_embeddings(chunks):
    """If DB already exist, delete to recreate to keep it up to date"""
    if os.path.exists(DB_NAME):
        Chroma(persist_directory=DB_NAME, embedding_function=embeddings).delete_collection()

    vectorstore = Chroma.from_documents(
        documents=chunks, embedding=embeddings, persist_directory=DB_NAME
    )

    collection = vectorstore._collection
    count = collection.count()

    sample_embedding = collection.get(limit=1, include=["embeddings"])["embeddings"][0]
    dimensions = len(sample_embedding)
    print(f"There are {count:,} vectors with {dimensions:,} dimensions in the vector store")
    return vectorstore


if __name__ == "__main__":
    documents = fetch_documents()
    chunks = create_chunks(documents)
    create_embeddings(chunks)
    print("Ingestion complete")