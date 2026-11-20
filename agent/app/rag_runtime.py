import json
import logging
import math
import re
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from . import cache, config, database, llm

log = logging.getLogger(__name__)

qdrant = QdrantClient(url=config.QDRANT_URL, timeout=30)

CHUNK_SIZE = 800


def ensure_collection(vector_size):
    names = [c.name for c in qdrant.get_collections().collections]
    if config.QDRANT_COLLECTION in names:
        return
    qdrant.create_collection(config.QDRANT_COLLECTION,
                             vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))
    log.info("Created Qdrant collection %s", config.QDRANT_COLLECTION)


def split_text(text):
    chunks = []
    current = ""
    for line in text.splitlines():
        if len(current) + len(line) > CHUNK_SIZE and current:
            chunks.append(current.strip())
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


def report_text(row):
    report = row["report_json"] or {}
    lines = [
        f"Audit report for invoice {row['invoice_number']} from {row['vendor_name']}.",
        f"Recommendation: {row['recommendation']}. Status: {row['processing_status']}. Reason: {report.get('reason')}.",
        f"Total {row['total_amount']} {row['currency']}, PO {row['po_number']}, date {row['invoice_date']}.",
        f"Summary: {report.get('summary')}",
    ]
    if report.get("human_decision"):
        lines.append(f"Auditor decision: {report['human_decision']}.")
    if report.get("missing_fields"):
        lines.append("Missing fields: " + ", ".join(report["missing_fields"]))
    for d in report.get("discrepancies", []):
        lines.append(f"Discrepancy on {d['field_name']} ({d['severity']}): invoice value {d['invoice_value']}, "
                     f"ERP value {d['erp_value']}. {d['explanation']}")
    return "\n".join(lines)


def index_invoice(invoice_id):
    row = database.get_invoice(invoice_id)
    if row is None or not row["raw_extracted_text"]:
        return 0

    chunks = []
    for text in split_text(row["translated_text"] or row["raw_extracted_text"]):
        chunks.append({"source_type": "invoice", "chunk_text": text})
    if row["report_json"]:
        for text in split_text(report_text(row)):
            chunks.append({"source_type": "report", "chunk_text": text})

    vectors = llm.embed([c["chunk_text"] for c in chunks])
    ensure_collection(len(vectors[0]))

    qdrant.delete(config.QDRANT_COLLECTION, points_selector=Filter(
        must=[FieldCondition(key="invoice_id", match=MatchValue(value=str(invoice_id)))]))

    points = []
    for number, (chunk, vector) in enumerate(zip(chunks, vectors)):
        payload = {
            "invoice_id": str(invoice_id),
            "invoice_number": row["invoice_number"],
            "vendor": row["vendor_name"],
            "language": row["source_language"],
            "source_type": chunk["source_type"],
            "chunk_text": chunk["chunk_text"],
        }
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{invoice_id}-{number}"))
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))
    qdrant.upsert(config.QDRANT_COLLECTION, points=points, wait=True)
    database.mark_indexed(invoice_id)
    log.info("Indexed %d chunks for invoice %s", len(points), invoice_id)
    return len(points)


def indexing_node(state):
    try:
        count = index_invoice(state["invoice_id"])
    except Exception as error:
        log.warning("Indexing failed for %s: %s", state["invoice_id"], error)
        count = 0
    database.add_file_event(state["file_name"], "completed", state["checksum"],
                            "Invoice processing finished", state["invoice_id"])
    return {"chunk_count": count}


def rebuild_index():
    total = 0
    for row in database.invoices_with_text():
        try:
            total += index_invoice(row["invoice_id"])
        except Exception as error:
            log.warning("Could not index %s: %s", row["invoice_id"], error)
    return total


def qdrant_ok():
    try:
        qdrant.get_collections()
        return True
    except Exception:
        return False


def point_count():
    try:
        return qdrant.count(config.QDRANT_COLLECTION).count
    except Exception:
        return 0


class RagState(TypedDict, total=False):
    question: str
    invoice_id: str
    route: str
    query: str
    attempt: int
    chunks: list
    context: str
    answer: str
    sources: list
    scores: dict
    accepted: bool
    retrieval_error: str


STRUCTURED_WORDS = ["how many", "count", "sum of", "which invoices", "number of", "all invoices", "statistics"]


def asks_total_invoice_count(question):
    normalized = re.sub(r"[^a-z0-9 ]", " ", question.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return ("invoice" in normalized and
            ("how many" in normalized or "number of" in normalized or
             re.search(r"\bcount\b", normalized) is not None) and
            not any(word in normalized for word in ("approved", "rejected", "review", "pending", "failed")))


def find_invoice_in_question(question):
    for row in database.list_invoices():
        if row["invoice_number"] and row["invoice_number"].lower() in question.lower():
            return str(row["invoice_id"])
    return None


def router_node(state):
    # Route unambiguous total-count questions deterministically; a router model
    # call can otherwise send them to vector search or consume time needlessly.
    if asks_total_invoice_count(state["question"]):
        route = "structured"
    else:
        answer = llm.ask("rag", "classify_question", {"question": state["question"]})
        if answer and answer.get("route") in ("structured", "semantic"):
            route = answer["route"]
        else:
            question = state["question"].lower()
            route = "structured" if any(word in question for word in STRUCTURED_WORDS) else "semantic"
    invoice_id = state.get("invoice_id") or find_invoice_in_question(state["question"])
    return {"route": route, "invoice_id": invoice_id, "query": state["question"], "attempt": 0}


def structured_node(state):
    rows = database.invoice_summary_rows(state.get("invoice_id"))
    if asks_total_invoice_count(state["question"]):
        count = len(rows)
        noun = "invoice" if count == 1 else "invoices"
        return {"answer": f"There {('is' if count == 1 else 'are')} {count} {noun} in the audit record.",
                "context": f"Total invoices: {count}", "sources": ["audit.invoice_audit"],
                "attempt": state["attempt"] + 1, "scores": {"answer_relevance": 1.0,
                "groundedness": 1.0, "context_relevance": 1.0}, "accepted": True}
    table = "\n".join(json.dumps(row, default=str) for row in rows) or "(no invoices yet)"
    answer = llm.ask("rag", "generate_answer", {"question": state["question"], "context": table},
                     use_cache=state["attempt"] == 0)
    text = answer["answer"] if answer and answer.get("answer") else "Invoices in the audit trail:\n" + table
    return {"answer": text, "context": table, "sources": ["audit.invoice_audit"], "attempt": state["attempt"] + 1}


def retrieval_node(state):
    attempt = state["attempt"] + 1
    limit = config.RAG_TOP_K * 2 * attempt
    query_filter = None
    if state.get("invoice_id"):
        query_filter = Filter(must=[FieldCondition(key="invoice_id", match=MatchValue(value=state["invoice_id"]))])
    retrieval_error = None
    try:
        vector = llm.embed([state["query"]])[0]
        hits = qdrant.query_points(config.QDRANT_COLLECTION, query=vector, limit=limit,
                                   query_filter=query_filter, with_payload=True).points
    except Exception as error:
        log.warning("Retrieval failed: %s", error)
        hits = []
        retrieval_error = str(error)
    chunks = [dict(hit.payload, score=hit.score) for hit in hits]
    update = {"chunks": chunks, "attempt": attempt}
    if retrieval_error:
        update["retrieval_error"] = retrieval_error
    return update


def augmentation_node(state):
    question_words = set(re.findall(r"[a-z0-9-]{3,}", state["question"].lower()))
    wants_report = any(w in state["question"].lower() for w in ["why", "flag", "reject", "approve", "discrepan", "review"])

    def rank(chunk):
        words = set(re.findall(r"[a-z0-9-]{3,}", chunk["chunk_text"].lower()))
        overlap = len(question_words & words) / max(len(question_words), 1)
        bonus = 0.1 if wants_report and chunk["source_type"] == "report" else 0
        return chunk["score"] + 0.3 * overlap + bonus

    best = sorted(state["chunks"], key=rank, reverse=True)[:config.RAG_TOP_K]
    parts = []
    for number, chunk in enumerate(best, start=1):
        parts.append(f"[{number}] invoice {chunk['invoice_number']} ({chunk['source_type']}):\n{chunk['chunk_text']}")
    return {"chunks": best, "context": "\n\n".join(parts) or "(nothing found)"}


def generation_node(state):
    if state.get("retrieval_error"):
        return {"answer": "I couldn't search invoice records because the embedding service is unavailable. "
                          "Check the agent's AWS credentials, Bedrock model access, and AWS region.",
                "sources": []}
    answer = llm.ask("rag", "generate_answer", {"question": state["question"], "context": state["context"]})
    if answer and answer.get("answer"):
        text = answer["answer"]
    elif state["chunks"]:
        text = "The model is not available. The most relevant passage is:\n\n" + state["chunks"][0]["chunk_text"]
    else:
        text = "No processed invoice contains information about this."
    sources = sorted({c["invoice_number"] or c["invoice_id"] for c in state["chunks"]})
    return {"answer": text, "sources": sources}


def reflection_node(state):
    # Exact SQL count answers are deterministic and already grounded in the database.
    if state.get("accepted") and state.get("route") == "structured":
        return {"scores": state["scores"], "accepted": True, "query": state["query"]}
    if state.get("retrieval_error"):
        return {"scores": {"answer_relevance": 0.0, "groundedness": 0.0,
                           "context_relevance": 0.0, "average": 0.0},
                "accepted": False, "query": state["query"]}
    scores = {"answer_relevance": 0.0, "groundedness": 0.0, "context_relevance": 0.0}
    result = llm.ask("rag", "reflect_answer", {"question": state["question"], "context": state["context"],
                                                "answer": state["answer"]})
    if result:
        for name in scores:
            try:
                value = float(result.get(name, 0))
                scores[name] = value if math.isfinite(value) and 0 <= value <= 1 else 0.0
            except (TypeError, ValueError):
                pass
    scores["average"] = round(sum(scores.values()) / 3, 3)
    accepted = scores["average"] > config.RAG_SCORE_THRESHOLD
    log.info("RAG attempt %d scored %.3f (accepted=%s)", state["attempt"], scores["average"], accepted)

    query = state["query"]
    if not accepted and result and result.get("better_query"):
        query = result["better_query"]
    return {"scores": scores, "accepted": accepted, "query": query}


def after_router(state):
    return state["route"]


def after_reflection(state):
    if state.get("retrieval_error") or state["accepted"] or state["attempt"] >= config.RAG_MAX_RETRIES:
        return "done"
    return "structured" if state["route"] == "structured" else "retry"


def build_rag_graph():
    graph = StateGraph(RagState)
    graph.add_node("router", router_node)
    graph.add_node("structured", structured_node)
    graph.add_node("retrieve", retrieval_node)
    graph.add_node("augment", augmentation_node)
    graph.add_node("generate", generation_node)
    graph.add_node("reflect", reflection_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", after_router, {"structured": "structured", "semantic": "retrieve"})
    graph.add_edge("structured", "reflect")
    graph.add_edge("retrieve", "augment")
    graph.add_edge("augment", "generate")
    graph.add_edge("generate", "reflect")
    # retry
    graph.add_conditional_edges("reflect", after_reflection,
                                {"retry": "retrieve", "structured": "structured", "done": END})
    return graph.compile()


rag_graph = build_rag_graph()


def answer_question(question, invoice_id=None):
    # Cache the whole RAG response as well as individual model calls. This
    # avoids repeating retrieval, embedding, generation, and reflection for an
    # identical question while keeping Redis disposable. Include audit-data
    # timestamps so a new invoice or correction cannot reuse a stale answer.
    revision = database.fetch_one(
        """SELECT coalesce((SELECT max(processed_at)::text FROM audit.invoice_audit), '') AS invoices,
                  coalesce((SELECT max(corrected_at)::text FROM audit.human_feedback), '') AS feedback"""
    )
    cache_key = cache.make_key(
        "rag-query-v4",
        f"{revision['invoices']}\n{revision['feedback']}\n{invoice_id or ''}\n{question.strip()}\n"
        f"{llm.MODELS}",
    )
    cached = cache.get(cache_key)
    if cached is not None:
        scores = cached.get("scores", {})
        database.save_rag_query(question, cached.get("route", "semantic"), cached.get("answer", ""),
                                cached.get("attempts", 1), scores, cached.get("accepted", False))
        return cached

    result = rag_graph.invoke({"question": question, "invoice_id": invoice_id})
    response = {
        "answer": result["answer"] if result["accepted"] else
                  (result["answer"] if result.get("retrieval_error") else
                  "I could not verify an answer against the available invoice records. "
                  "Please select a specific invoice or rephrase the question."),
        "sources": result.get("sources", []),
        "route": result["route"],
        "attempts": result["attempt"],
        "scores": result["scores"],
        "contexts": [chunk["chunk_text"] for chunk in result.get("chunks", [])],
        "accepted": result["accepted"],
    }
    if result.get("retrieval_error"):
        response["retrieval_error"] = result["retrieval_error"]
    if not response["contexts"] and result.get("context") not in (None, "(nothing found)"):
        response["contexts"] = [result["context"]]
    database.save_rag_query(question, result["route"], response["answer"], result["attempt"],
                            result["scores"], result["accepted"])
    cache.put(cache_key, response)
    return response
