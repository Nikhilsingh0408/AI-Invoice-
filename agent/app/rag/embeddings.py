"""Embedding provider using BedrockEmbeddings from langchain_aws."""

from __future__ import annotations

import os
from typing import List, Optional

from langchain_aws import BedrockEmbeddings


def get_bedrock_embeddings(
    model_id: Optional[str] = None,
    region_name: Optional[str] = None,
) -> BedrockEmbeddings:
    """Instantiate BedrockEmbeddings for generating dense vectors stored in Qdrant."""

    model = (
        model_id
        or os.getenv(
            "BEDROCK_EMBEDDING_MODEL_ID",
            "amazon.titan-embed-text-v2:0",
        )
    )

    region = (
        region_name
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    )

    return BedrockEmbeddings(
        model_id=model,
        region_name=region,
    )


def embed_text(
    text: str,
    embeddings: Optional[BedrockEmbeddings] = None,
) -> List[float]:
    """Helper to embed a single query text string into a vector."""

    provider = embeddings or get_bedrock_embeddings()

    return provider.embed_query(text)


def embed_documents(
    texts: List[str],
    embeddings: Optional[BedrockEmbeddings] = None,
) -> List[List[float]]:
    """Helper to batch embed a list of document chunk strings into vectors."""

    provider = embeddings or get_bedrock_embeddings()

    return provider.embed_documents(texts)