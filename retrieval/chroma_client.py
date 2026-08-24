"""
chroma_client.py

Single shared Chroma-backed LlamaIndex VectorStoreIndex for the whole app.

IMPORTANT: chromadb.PersistentClient must only be instantiated ONCE per
process for a given path. Creating two independent clients pointed at the
same directory causes SQLite locking conflicts that can crash the whole
worker process. Every module that needs the index must import get_index()
from HERE instead of building its own client.
"""

import chromadb
from llama_index.core import Settings, VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

CHROMA_PERSIST_DIR = "retrieval/chroma_db"
COLLECTION_NAME = "historical_claims"

_index: VectorStoreIndex | None = None


def get_index() -> VectorStoreIndex:
    global _index
    if _index is not None:
        return _index

    Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

    chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    chroma_collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)

    _index = VectorStoreIndex.from_vector_store(vector_store)
    return _index
