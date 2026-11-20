"""LangGraph workflow assembly and compilation.

Builds a deterministic Directed Acyclic Graph (DAG):

START -> analyze_query -> retrieve_context -> execute_tools
      -> generate_answer -> evaluate_ragas -> END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.app.graph.state import AgentRAGState
from agent.app.graph.nodes.analyze_query import analyze_query_node
from agent.app.graph.nodes.retrieve_context import retrieve_context_node
from agent.app.graph.nodes.execute_tools import execute_tools_node
from agent.app.graph.nodes.generate_answer import generate_answer_node
from agent.app.graph.nodes.evaluate_ragas import evaluate_ragas_node


def build_rag_graph() -> Any:
    """Build and compile the LangGraph workflow for the RAG agent."""

    workflow = StateGraph(AgentRAGState)

    # Add nodes
    workflow.add_node("analyze_query", analyze_query_node)
    workflow.add_node("retrieve_context", retrieve_context_node)
    workflow.add_node("execute_tools", execute_tools_node)
    workflow.add_node("generate_answer", generate_answer_node)
    workflow.add_node("evaluate_ragas", evaluate_ragas_node)

    # Deterministic edges
    workflow.add_edge(START, "analyze_query")
    workflow.add_edge("analyze_query", "retrieve_context")
    workflow.add_edge("retrieve_context", "execute_tools")
    workflow.add_edge("execute_tools", "generate_answer")
    workflow.add_edge("generate_answer", "evaluate_ragas")
    workflow.add_edge("evaluate_ragas", END)

    return workflow.compile()


_rag_graph: Any | None = None


def get_rag_graph() -> Any:
    """Retrieve or compile the singleton RAG LangGraph workflow."""

    global _rag_graph

    if _rag_graph is None:
        _rag_graph = build_rag_graph()

    return _rag_graph