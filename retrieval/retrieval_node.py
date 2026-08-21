"""
retrieval_node.py

LangGraph node: given the current claim under investigation (and, ideally,
the fraud_node's output already in state), retrieves the most similar
historical claims from the persisted Chroma index and returns them as
structured evidence for the next reasoning node.

Expects graph state to contain a "claim" dict (same shape used by fraud_node)
and writes "similar_claims" back into state.
"""

import chromadb
from llama_index.core import Settings, VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

CHROMA_PERSIST_DIR = "retrieval/chroma_db"
COLLECTION_NAME = "historical_claims"
TOP_K = 5

_index = None  # lazy singleton so we don't reload the index on every call


def _get_index() -> VectorStoreIndex:
    global _index
    if _index is not None:
        return _index

    Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

    chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    chroma_collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)

    _index = VectorStoreIndex.from_vector_store(vector_store)
    return _index


def claim_to_query_text(claim: dict) -> str:
    """Mirror build_index.row_to_text so the query embeds in the same space."""
    parts = [f"{k.replace('_', ' ')}: {v}" for k, v in claim.items() if v not in (None, "", "?")]
    return "Claim record — " + "; ".join(parts)


def retrieval_node(state: dict) -> dict:
    """
    LangGraph node function.

    Input state keys used: "claim" (dict of the incoming claim's fields)
    Output state keys added: "similar_claims" (list of dicts)
    """
    claim = state["claim"]
    query_text = claim_to_query_text(claim)

    index = _get_index()
    retriever = index.as_retriever(similarity_top_k=TOP_K)
    nodes = retriever.retrieve(query_text)

    similar_claims = []
    for n in nodes:
        similar_claims.append({
            "score": round(float(n.score), 4) if n.score is not None else None,
            "summary": n.node.get_content(),
            "fraud_reported": n.node.metadata.get("fraud_reported"),
            "incident_type": n.node.metadata.get("incident_type"),
            "incident_severity": n.node.metadata.get("incident_severity"),
        })

    fraud_count = sum(1 for c in similar_claims if c["fraud_reported"] == "Y")
    state["similar_claims"] = similar_claims
    state["similar_claims_summary"] = (
        f"{fraud_count} of {len(similar_claims)} most similar past claims were confirmed fraud."
    )
    return state


if __name__ == "__main__":
    # quick manual smoke test — adjust fields to match a real claim from your dataset
    test_claim = {
        "incident_type": "Multi-vehicle Collision",
        "incident_severity": "Major Damage",
        "collision_type": "Rear Collision",
        "auto_make": "Honda",
        "auto_model": "Civic",
        "total_claim_amount": 58000,
    }
    result = retrieval_node({"claim": test_claim})
    print(result["similar_claims_summary"])
    for c in result["similar_claims"]:
        print(f"  score={c['score']}  fraud={c['fraud_reported']}  {c['summary'][:100]}...")