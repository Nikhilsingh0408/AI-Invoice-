"""Self-reflective RAG workflow.

Extends the deterministic DAG with a reflection loop. If the average RAGAS
score falls below the configured threshold, retrieval and generation are
retried within a bounded retry budget.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.app.core.config import get_settings
from agent.app.core.logging_config import get_logger
from agent.app.graph.nodes.analyze_query import analyze_query_node
from agent.app.graph.nodes.evaluate_ragas import evaluate_ragas_node
from agent.app.graph.nodes.execute_tools import execute_tools_node
from agent.app.graph.nodes.generate_answer import generate_answer_node
from agent.app.graph.nodes.retrieve_context import retrieve_context_node
from agent.app.graph.state import AgentRAGState

logger = get_logger(__name__)


class ReflectiveRAGState(AgentRAGState, total=False):
    """RAG state plus the reflection loop's retry counter."""

    retry_count: int
    retry_history: list[dict[str, Any]]


def average_ragas_score(scores: Any) -> Optional[float]:
    """Average numeric RAGAS metrics while tolerating common result shapes."""

    if scores is None:
        return None

    if isinstance(scores, bool):
        return None

    if isinstance(scores, (int, float)):
        return float(scores)

    if isinstance(scores, dict):
        if isinstance(scores.get("scores"), dict):
            return average_ragas_score(scores["scores"])

        numeric = [
            float(value)
            for key, value in scores.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and key not in {"retry_count", "count"}
        ]

        if numeric:
            return sum(numeric) / len(numeric)

    return None


def should_retry(state: ReflectiveRAGState) -> str:
    """Retry retrieval when the answer fails evaluation and budget remains."""

    settings = get_settings()
    retry_count = int(state.get("retry_count", 0) or 0)

    max_retries = int(settings.rag_max_retries)
    threshold = float(settings.rag_score_threshold)

    if retry_count >= max_retries:
        logger.info(
            "Reflection budget exhausted after %s retries",
            retry_count,
        )
        return "finalize"

    average = average_ragas_score(state.get("ragas_scores"))

    if average is None:
        logger.error(
            "RAG evaluation produced no usable numeric scores; "
            "the answer cannot pass the quality gate."
        )
        return "finalize"

    if average < threshold:
        logger.info(
            "RAGAS average %.3f below threshold %.2f; "
            "retrying (attempt %s/%s)",
            average,
            threshold,
            retry_count + 1,
            max_retries,
        )
        return "retry"

    return "finalize"


def prepare_retry_node(
    state: ReflectiveRAGState,
) -> dict[str, Any]:
    """Increment the retry counter and widen retrieval before the next attempt."""

    settings = get_settings()

    retry_count = int(state.get("retry_count", 0) or 0) + 1
    average = average_ragas_score(state.get("ragas_scores"))

    history = [
        *state.get("retry_history", []),
        {
            "attempt": retry_count,
            "average_score": average,
            "rejected_answer": (
                state.get("draft_answer")
                or state.get("final_answer")
                or ""
            ),
        },
    ]

    return {
        "retry_count": retry_count,
        "retry_history": history,
        "metadata_filters": {
            **state.get("metadata_filters", {}),
            "top_k": int(settings.rag_top_k) * (retry_count + 1),
        },
        "trace": [
            *state.get("trace", []),
            {
                "node": "prepare_retry",
                "message": f"Retry {retry_count}",
            },
        ],
    }


def finalize_node(
    state: ReflectiveRAGState,
) -> dict[str, Any]:
    """Normalize the final answer key without guessing."""

    average = average_ragas_score(state.get("ragas_scores"))

    settings = get_settings()
    threshold = float(settings.rag_score_threshold)
    retry_count = int(state.get("retry_count", 0) or 0)

    if average is None:
        status = "evaluation_failed"
        answer = ""
        errors = [
            *state.get("errors", []),
            "RAG quality evaluation is missing or invalid.",
        ]

    elif average < threshold:
        status = "quality_threshold_not_met"
        answer = ""
        errors = [
            *state.get("errors", []),
            (
                f"RAG quality score {average:.3f} is below "
                f"the required threshold {threshold:.3f}."
            ),
        ]

    else:
        status = "completed"
        answer = state.get("draft_answer") or state.get("final_answer") or ""
        errors = state.get("errors", [])

    return {
        "final_answer": answer,
        "draft_answer": state.get("draft_answer", ""),
        "status": status,
        "errors": errors,
        "trace": [
            *state.get("trace", []),
            {
                "node": "finalize",
                "message": status,
            },
        ],
    }


def build_reflective_rag_graph(
    checkpointer: Any | None = None,
) -> Any:
    """Assemble and compile the self-reflective RAG graph."""

    workflow = StateGraph(ReflectiveRAGState)

    workflow.add_node("analyze_query", analyze_query_node)
    workflow.add_node("retrieve_context", retrieve_context_node)
    workflow.add_node("execute_tools", execute_tools_node)
    workflow.add_node("generate_answer", generate_answer_node)
    workflow.add_node("evaluate_ragas", evaluate_ragas_node)
    workflow.add_node("prepare_retry", prepare_retry_node)
    workflow.add_node("finalize", finalize_node)

    workflow.add_edge(START, "analyze_query")
    workflow.add_edge("analyze_query", "retrieve_context")
    workflow.add_edge("retrieve_context", "execute_tools")
    workflow.add_edge("execute_tools", "generate_answer")
    workflow.add_edge("generate_answer", "evaluate_ragas")

    workflow.add_conditional_edges(
        "evaluate_ragas",
        should_retry,
        {
            "retry": "prepare_retry",
            "finalize": "finalize",
        },
    )

    workflow.add_edge("prepare_retry", "retrieve_context")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=checkpointer)


_reflective_graph: Any | None = None


def get_reflective_rag_graph() -> Any:
    """Return the singleton compiled self-reflective RAG graph."""

    global _reflective_graph

    if _reflective_graph is None:
        from agent.app.graph.checkpointer import get_checkpointer

        _reflective_graph = build_reflective_rag_graph(
            checkpointer=get_checkpointer()
        )

    return _reflective_graph