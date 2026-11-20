"""State for LangGraph RAG Agent.
Compatible with shared.contracts.InvoiceState and contains all intermediate
artifacts: query, extracted filters, retrieved chunks, tool results, generated
answer, citations, and RAGAS reflection scores.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentRAGState(TypedDict, total=False):
    """Shared state passed between all nodes in the LangGraph RAG workflow."""

    # 1. Inputs
    user_query: str
    invoice_id: Optional[str]
    thread_id: Optional[str]

    # 2. Query Analysis
    query_intent: str
    extracted_invoice_id: Optional[str]
    metadata_filters: Dict[str, Any]
    requires_tools: bool

    # 3. Retrieval
    retrieved_evidence: List[Dict[str, Any]]
    retrieved_contexts: List[str]

    # 4. Tool Execution
    tool_calls: List[Dict[str, Any]]
    tool_results: List[Dict[str, Any]]

    # 5. Generation
    draft_answer: str
    citations: List[str]

    # 6. RAGAS Evaluation
    ragas_scores: Dict[str, Any]

    # 7. Final Response and Observability Trace
    final_answer: str
    trace: List[Dict[str, str]]
    status: str
    errors: List[str]