"""Hybrid retriever combining Qdrant dense vector search with metadata filtering."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.app.rag.embeddings import embed_text
from agent.app.db.vector_store import QdrantVectorStore


class HybridInvoiceRetriever:
    """Retrieve invoice evidence using dense vector search in Qdrant."""

    def __init__(
        self,
        vector_store: Optional[QdrantVectorStore] = None,
    ) -> None:
        self.vector_store = vector_store or QdrantVectorStore()

    def retrieve(
        self,
        query: str,
        invoice_id: Optional[str] = None,
        top_k: int = 4,
    ) -> List[Dict[str, Any]]:
        """Execute hybrid search over Qdrant for relevant invoice evidence."""

        query_vector = embed_text(query)

        results = self.vector_store.search_similarity(
            query_vector=query_vector,
            limit=top_k,
            invoice_id=invoice_id,
        )

        formatted_evidence = []

        for result in results:
            formatted_evidence.append(
                {
                    "source": result.get("source", "Qdrant Vector DB"),
                    "snippet": result.get("text", ""),
                    "confidence": round(
                        float(result.get("score", 0.0)),
                        4,
                    ),
                    "payload": result.get("payload", {}),
                }
            )

        return formatted_evidence