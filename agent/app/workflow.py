import uuid
from typing import TypedDict

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from . import database
from .agents.business_validation import business_validation_node
from .agents.data_validation import data_validation_node
from .agents.extractor import extractor_node
from .agents.human_review import decision_node, human_review_node
from .agents.monitor import monitor_node
from .agents.parser import parse_node
from .agents.reporting import report_files_node, reporting_node
from .agents.translator import translation_node
from .rag_runtime import indexing_node


class InvoiceState(TypedDict, total=False):
    file_path: str
    file_name: str
    thread_id: str
    invoice_id: str
    checksum: str
    file_type: str
    stop: str
    raw_text: str
    extraction_method: str
    language: str
    translated_text: str
    translation_confidence: float
    revalidation: bool
    invoice: dict
    applied_corrections: list
    data_findings: list
    missing_fields: list
    data_summary: str
    business_findings: list
    business_summary: str
    risk_level: str
    erp: dict
    validation_status: str
    recommendation: str
    report: dict
    human_checked: bool
    human_action: str
    review_comment: str
    chunk_count: int


def where_to_start(state):
    return "parse" if state.get("revalidation") else "monitor"


def go_on_or_stop(state):
    return "stop" if state.get("stop") else "go_on"


def after_reporting(state):
    return "human_review" if state["recommendation"] == "review" else "report_files"


def after_human_review(state):
    return "parse" if state["human_action"] == "recheck" else "decision"


def build_graph():
    graph = StateGraph(InvoiceState)
    graph.add_node("monitor", monitor_node)
    graph.add_node("extract", extractor_node)
    graph.add_node("translate", translation_node)
    graph.add_node("parse", parse_node)
    graph.add_node("data_validation", data_validation_node)
    graph.add_node("business_validation", business_validation_node)
    graph.add_node("reporting", reporting_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("decision", decision_node)
    graph.add_node("report_files", report_files_node)
    graph.add_node("index", indexing_node)

    graph.add_conditional_edges(START, where_to_start, {"monitor": "monitor", "parse": "parse"})
    graph.add_conditional_edges("monitor", go_on_or_stop, {"go_on": "extract", "stop": END})
    graph.add_conditional_edges("extract", go_on_or_stop, {"go_on": "translate", "stop": END})
    graph.add_edge("translate", "parse")
    graph.add_edge("parse", "data_validation")
    graph.add_edge("data_validation", "business_validation")
    graph.add_conditional_edges("business_validation", go_on_or_stop, {"go_on": "reporting", "stop": END})
    graph.add_conditional_edges("reporting", after_reporting, {"human_review": "human_review",
                                                               "report_files": "report_files"})
    # review
    graph.add_conditional_edges("human_review", after_human_review, {"parse": "parse", "decision": "decision"})
    graph.add_edge("decision", "report_files")
    graph.add_edge("report_files", "index")
    graph.add_edge("index", END)

    return graph.compile(checkpointer=PostgresSaver(database.checkpoint_pool))


invoice_graph = None


def setup():
    global invoice_graph
    saver = PostgresSaver(database.checkpoint_pool)
    try:
        saver.setup()
    except psycopg.errors.DuplicateColumn as error:
        # LangGraph's final migration adds task_path but older installations can
        # have the column without the corresponding migration ledger entry.
        if 'column "task_path"' not in str(error):
            raise
        column = database.fetch_one(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'langgraph' AND table_name = 'checkpoint_writes' AND column_name = 'task_path'"
        )
        latest = database.fetch_one("SELECT max(v) AS version FROM langgraph.checkpoint_migrations")
        if not column or latest["version"] != 8:
            raise
        with database.checkpoint_pool.connection() as conn:
            conn.execute("INSERT INTO checkpoint_migrations (v) VALUES (9) ON CONFLICT DO NOTHING")
        saver.setup()
    invoice_graph = build_graph()


def thread_config(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def is_waiting_for_human(thread_id):
    if not thread_id:
        return False
    return "human_review" in invoice_graph.get_state(thread_config(thread_id)).next


def process_file(path):
    thread_id = f"invoice-{uuid.uuid4()}"
    state = {"file_path": str(path), "file_name": path.name, "thread_id": thread_id}
    return invoice_graph.invoke(state, thread_config(thread_id))


def resume(thread_id, action, comment):
    answer = {"action": action, "comment": comment}
    return invoice_graph.invoke(Command(resume=answer), thread_config(thread_id))


def recheck_finished(row):
    thread_id = f"recheck-{row['invoice_id']}-{uuid.uuid4()}"
    database.set_thread_id(row["invoice_id"], thread_id)
    state = {
        "file_path": row["file_path"],
        "file_name": row["file_name"],
        "thread_id": thread_id,
        "invoice_id": str(row["invoice_id"]),
        "checksum": row["file_checksum"],
        "raw_text": row["raw_extracted_text"],
        "extraction_method": row["extraction_method"],
        "translated_text": row["translated_text"],
        "language": row["source_language"],
        "translation_confidence": float(row["translation_confidence"] if row["translation_confidence"] is not None else 1),
        "revalidation": True,
        "human_checked": True,
    }
    return invoice_graph.invoke(state, thread_config(thread_id))
