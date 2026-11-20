
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent.app.graph.state import AgentRAGState
from agent.app.graph.invoice_state import InvoiceProcessingState


class TestState(TypedDict, total=False):
    value: int
    status: str
    error: str


def increment_node(state: TestState) -> dict:
    return {
        "value": state.get("value", 0) + 1,
        "status": "completed",
    }


def route_node(state: TestState) -> str:
    if state.get("error"):
        return "error"
    return "success"


def success_node(state: TestState) -> dict:
    return {"status": "success"}


def error_node(state: TestState) -> dict:
    return {"status": "error"}


def build_test_graph():
    graph = StateGraph(TestState)

    graph.add_node("increment", increment_node)
    graph.add_node("route", lambda state: {})
    graph.add_node("success", success_node)
    graph.add_node("error", error_node)

    graph.add_edge(START, "increment")
    graph.add_edge("increment", "route")

    graph.add_conditional_edges(
        "route",
        route_node,
        {
            "success": "success",
            "error": "error",
        },
    )

    graph.add_edge("success", END)
    graph.add_edge("error", END)

    return graph.compile()


def test_rag_state_imports():
    assert AgentRAGState is not None


def test_invoice_state_imports():
    assert InvoiceProcessingState is not None


def test_graph_compiles():
    assert build_test_graph() is not None


def test_graph_updates_state():
    result = build_test_graph().invoke({"value": 1})
    assert result["value"] == 2


def test_graph_routes_successfully():
    result = build_test_graph().invoke({"value": 0})
    assert result["status"] == "success"


def test_graph_routes_errors():
    result = build_test_graph().invoke({
        "value": 0,
        "error": "simulated failure",
    })
    assert result["status"] == "error"
