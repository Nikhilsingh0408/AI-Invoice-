import logging
import shutil
import sys
import threading
import uuid
from pathlib import Path
from uuid import UUID
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import cache, config, database, erp_client, rag_runtime as rag, ragas_eval, watcher, workflow
from .agents.reporting import render_html
from .agents.parser import HEADER_CORRECTION_FIELDS, LINE_CORRECTION_FIELDS, to_date, to_decimal

logging.basicConfig(level=config.LOG_LEVEL, stream=sys.stdout,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
log = logging.getLogger("agent")


@asynccontextmanager
async def lifespan(app):
    database.open_pools()
    workflow.setup()
    rag.qdrant.get_collections()
    if not erp_client.is_healthy():
        raise RuntimeError(f"mock-erp is not reachable at {config.MOCK_ERP_URL}")
    config.load_rules()
    if not cache.ping():
        log.warning("Redis is not reachable - running without a cache")

    for folder in [config.INCOMING_DIR, config.PROCESSED_DIR, config.PENDING_DIR,
                   config.REJECTED_DIR, config.FAILED_DIR, config.REPORTS_DIR]:
        folder.mkdir(parents=True, exist_ok=True)

    if rag.point_count() == 0 and database.invoices_with_text():
        log.info("Qdrant is empty but PostgreSQL has invoices - rebuilding the index")
        threading.Thread(target=rag.rebuild_index, daemon=True).start()

    watcher.start()
    log.info("Agent is ready")
    yield
    watcher.stop()
    database.close_pools()


app = FastAPI(title="AI Invoice Auditor – Group 14 - Agent", lifespan=lifespan)


class Correction(BaseModel):
    invoice_id: str
    field_name: str
    corrected_value: str
    original_value: str | None = None
    scope: str = "header"
    line_number: int | None = None
    comment: str | None = None


class Review(BaseModel):
    invoice_id: str
    action: str
    comment: str | None = None


class Question(BaseModel):
    question: str
    invoice_id: str | None = None


class TestQuestion(BaseModel):
    question: str
    answer: str


def get_invoice_or_404(invoice_id):
    try:
        UUID(invoice_id)
    except ValueError:
        raise HTTPException(422, "Invalid invoice ID")
    row = database.get_invoice(invoice_id)
    if row is None:
        raise HTTPException(404, "Invoice not found")
    return row


@app.get("/health")
def health():
    checks = {
        "postgres": database.ping(),
        "mock_erp": erp_client.is_healthy(),
        "qdrant": rag.qdrant_ok(),
        "redis": bool(cache.ping()),
    }
    required_dependencies = ("postgres", "mock_erp", "qdrant")
    healthy = all(checks[name] for name in required_dependencies)
    status = "healthy" if healthy else "unhealthy"
    body = {"status": status, "checks": checks,
            "indexed_chunks": rag.point_count()}
    # Redis is an optional cache; its loss reduces performance but does not
    # make invoice processing or retrieval unavailable.
    body["cache_degraded"] = not checks["redis"]
    return JSONResponse(body, status_code=200 if healthy else 503)


@app.post("/uploads")
async def upload_invoice(file: UploadFile = File(...)):
    name = Path(file.filename or "invoice").name
    if Path(name).suffix.lower() not in {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        raise HTTPException(400, "Upload a PDF, DOCX, or supported image")
    destination = config.INCOMING_DIR / f"{uuid.uuid4().hex[:8]}_{name}"
    content = await file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty")
    destination.write_bytes(content)
    return {"file_name": destination.name, "status": "queued"}


@app.get("/uploads/{file_name}/status")
def upload_status(file_name: str):
    events = database.fetch_all(
        "SELECT event_type, detail, occurred_at FROM audit.file_events WHERE file_name = %s "
        "ORDER BY occurred_at, id",
        (Path(file_name).name,),
    )
    latest = events[-1] if events else {"event_type": "queued", "detail": "Waiting for the invoice monitor"}
    return {**latest, "events": events}


@app.post("/reset")
def reset_invoice_data():
    # Clear invoice-derived state while preserving the editable RAGAS question set.
    with watcher.lock:
        ragas_eval.setup()
        database.run("""TRUNCATE audit.invoice_line_items, audit.discrepancies, audit.human_feedback,
                       audit.file_events, audit.invoice_audit, audit.rag_queries, audit.ragas_results
                       RESTART IDENTITY CASCADE""")
        database.run("""DO $$ DECLARE t RECORD; BEGIN
                       FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'langgraph'
                       LOOP EXECUTE format('TRUNCATE TABLE langgraph.%I RESTART IDENTITY CASCADE', t.tablename); END LOOP;
                       END $$""")
        cache.client.flushdb()
        if config.QDRANT_COLLECTION in [c.name for c in rag.qdrant.get_collections().collections]:
            rag.qdrant.delete_collection(config.QDRANT_COLLECTION)
        for folder in [config.INCOMING_DIR, config.PROCESSED_DIR, config.PENDING_DIR,
                       config.REJECTED_DIR, config.FAILED_DIR, config.REPORTS_DIR]:
            folder.mkdir(parents=True, exist_ok=True)
            for path in folder.iterdir():
                shutil.rmtree(path) if path.is_dir() else path.unlink()
    return {"reset": True}


@app.post("/process")
def process_now():
    return {"results": watcher.scan_once()}


@app.post("/feedback")
def add_correction(c: Correction):
    fields = {"header": HEADER_CORRECTION_FIELDS, "line_item": LINE_CORRECTION_FIELDS}
    if c.scope not in fields or c.field_name not in fields[c.scope]:
        raise HTTPException(422, "Unsupported correction scope or field")
    if c.scope == "line_item" and (c.line_number is None or c.line_number < 1):
        raise HTTPException(422, "Line corrections require a positive line number")
    if c.scope == "header" and c.line_number is not None:
        raise HTTPException(422, "Header corrections must not specify a line number")
    if c.field_name in {"subtotal_amount", "tax_amount", "total_amount", "quantity", "unit_price", "line_total"}:
        if to_decimal(c.corrected_value) is None:
            raise HTTPException(422, "Invalid numeric correction")
    if c.field_name == "invoice_date" and to_date(c.corrected_value) is None:
        raise HTTPException(422, "Invalid date correction")
    get_invoice_or_404(c.invoice_id)
    database.add_feedback(c.invoice_id, c.field_name, c.original_value, c.corrected_value,
                          c.scope, c.line_number, c.comment)
    return {"saved": True}


@app.post("/review")
def review(r: Review):
    if r.action not in ("recheck", "approve", "reject"):
        raise HTTPException(400, "action must be 'recheck', 'approve' or 'reject'")
    try:
        return watcher.respond(r.invoice_id, r.action, r.comment)
    except LookupError as error:
        raise HTTPException(404, str(error))
    except ValueError as error:
        raise HTTPException(409, str(error))
    except RuntimeError as error:
        raise HTTPException(503, str(error))


@app.get("/invoices/{invoice_id}/report.html", response_class=HTMLResponse)
def report_html(invoice_id: str):
    row = get_invoice_or_404(invoice_id)
    if not row["report_json"]:
        raise HTTPException(404, "No report yet")
    return render_html(row["report_json"])


@app.post("/query")
def query(q: Question):
    result = rag.answer_question(q.question, q.invoice_id)
    contexts = result.pop("contexts", [])
    # SQL-backed answers should be returned immediately. Live RAGAS evaluation
    # calls several external model operations and adds no value to exact counts.
    if result.get("route") == "structured":
        result["ragas_scores"] = {"faithfulness": None, "answer_relevancy": None,
                                  "context_precision": None}
        result["ragas_note"] = "RAGAS is not run for SQL-backed answers."
    elif not contexts:
        result["ragas_scores"] = {"faithfulness": None, "answer_relevancy": None,
                                  "context_precision": None}
        result["ragas_note"] = "RAGAS was skipped because retrieval found no invoice context."
    else:
        result["ragas_scores"] = ragas_eval.score_live_answer(q.question, result["answer"], contexts)
    return result


@app.get("/ragas/questions")
def ragas_questions():
    return ragas_eval.list_questions()


@app.post("/ragas/questions")
def ragas_add_question(t: TestQuestion):
    if not t.question.strip() or not t.answer.strip():
        raise HTTPException(400, "Both the question and the correct answer are needed")
    ragas_eval.setup()
    ragas_eval.add_question(t.question.strip(), t.answer.strip())
    return {"saved": True}


@app.delete("/ragas/questions/{question_id}")
def ragas_delete_question(question_id: int):
    ragas_eval.delete_question(question_id)
    return {"deleted": True}


@app.post("/ragas/evaluate")
def ragas_evaluate():
    if not ragas_eval.list_questions():
        raise HTTPException(400, "Add a test question first")
    try:
        return {"run_id": ragas_eval.evaluate_all()}
    except Exception as error:
        raise HTTPException(503, str(error))


@app.get("/ragas/runs")
def ragas_runs():
    return ragas_eval.list_runs()


@app.get("/ragas/runs/{run_id}")
def ragas_run(run_id: int):
    return ragas_eval.run_results(run_id)


@app.post("/reindex")
def reindex():
    return {"chunks_written": rag.rebuild_index()}
