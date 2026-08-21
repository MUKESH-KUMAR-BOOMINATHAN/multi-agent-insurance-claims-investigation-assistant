"""
build_index.py

Converts historical claims (insurance_claims.csv) into LlamaIndex Documents
and builds a persisted ChromaDB vector index for retrieval.

Run once (or whenever the historical claims dataset changes):
    uv run python retrieval/build_index.py
"""

import chromadb
import pandas as pd
from llama_index.core import Document, StorageContext, VectorStoreIndex, Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

CSV_PATH = "data/insurance_claims.csv"          # adjust if your path differs
CHROMA_PERSIST_DIR = "retrieval/chroma_db"
COLLECTION_NAME = "historical_claims"

# The label column that tells us the confirmed outcome of a past claim.
# Kept OUT of the embedded text (so retrieval matches on claim facts, not
# on the answer) but stored as metadata so the reasoning step can cite it
# as evidence ("3 of 5 similar past claims were confirmed fraud").
LABEL_COLUMN = "fraud_reported"

# Columns that are IDs / free-text noise and shouldn't go into the summary.
DROP_FROM_TEXT = {LABEL_COLUMN, "policy_number", "insured_zip", "incident_location", "_c39"}


def row_to_text(row: pd.Series) -> str:
    """Turn a claim row into a natural-language summary for embedding."""
    parts = []
    for col, val in row.items():
        if col in DROP_FROM_TEXT:
            continue
        if pd.isna(val) or str(val).strip() in ("", "?", "-1"):
            continue
        label = col.replace("_", " ").replace("-", " ")
        parts.append(f"{label}: {val}")
    return "Claim record — " + "; ".join(parts)


def build_documents(df: pd.DataFrame) -> list[Document]:
    docs = []
    for idx, row in df.iterrows():
        text = row_to_text(row)
        metadata = {"row_index": int(idx)}

        if LABEL_COLUMN in df.columns and not pd.isna(row[LABEL_COLUMN]):
            metadata["fraud_reported"] = str(row[LABEL_COLUMN])

        # keep a few high-signal fields as metadata too, for optional filtering
        for col in ("incident_type", "incident_severity", "auto_make", "auto_model"):
            if col in df.columns and not pd.isna(row.get(col)):
                metadata[col] = str(row[col])

        docs.append(Document(text=text, metadata=metadata))
    return docs


def main():
    print(f"Loading {CSV_PATH} ...")
    df = pd.read_csv(CSV_PATH)
    print(f"Loaded {len(df)} historical claims, {len(df.columns)} columns.")

    documents = build_documents(df)
    print(f"Built {len(documents)} documents. Example:\n{documents[0].text[:300]}...\n")

    # Local embedding model — no API key needed, matches your Ollama-local stack
    Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

    chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    chroma_collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    print("Embedding + indexing (this may take a minute for large datasets)...")
    index = VectorStoreIndex.from_documents(
        documents, storage_context=storage_context, show_progress=True
    )

    print(f"\nDone. Index persisted to {CHROMA_PERSIST_DIR}/ (collection: {COLLECTION_NAME})")
    return index


if __name__ == "__main__":
    main()