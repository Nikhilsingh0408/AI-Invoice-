import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config, database, workflow
from .agents.monitor import file_checksum, list_incoming_files

log = logging.getLogger(__name__)

lock = threading.Lock()
stop_event = threading.Event()

FOLDER_FOR_STATUS = {
    "approved": config.PROCESSED_DIR,
    "pending_review": config.PENDING_DIR,
    "rejected": config.REJECTED_DIR,
    "failed": config.FAILED_DIR,
}


def move_file(path, folder, invoice_id=None):
    if path.parent == folder:
        return
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    if target.exists():
        target = folder / f"{path.stem}_{uuid.uuid4().hex}{path.suffix}"
    shutil.move(str(path), str(target))
    if invoice_id:
        database.update_file_path(invoice_id, str(target))


def file_to_status_folder(invoice_id):
    row = database.get_invoice(invoice_id)
    path = Path(row["file_path"])
    folder = FOLDER_FOR_STATUS.get(row["processing_status"])
    if folder and path.exists():
        move_file(path, folder, invoice_id)
    return row["processing_status"]


def process_one(path):
    with lock:
        if not path.exists():
            return {"file": path.name, "status": "gone"}
        log.info("Processing %s", path.name)
        try:
            result = workflow.process_file(path)
        except Exception as error:
            log.exception("Workflow failed for %s", path.name)
            row = database.find_by_checksum(file_checksum(path))
            invoice_id = str(row["invoice_id"]) if row else None
            if invoice_id:
                database.mark_failed(invoice_id, f"Workflow error: {error}")
            database.add_file_event(path.name, "failed", detail=str(error)[:500], invoice_id=invoice_id)
            move_file(path, config.FAILED_DIR, invoice_id)
            return {"file": path.name, "status": "failed", "error": str(error)}

        if result.get("stop") == "duplicate":
            move_file(path, config.PROCESSED_DIR)
            return {"file": path.name, "status": "duplicate", "invoice_id": result.get("invoice_id")}
        if not result.get("invoice_id"):
            move_file(path, config.FAILED_DIR)
            return {"file": path.name, "status": "failed", "reason": result.get("stop")}

        status = file_to_status_folder(result["invoice_id"])
        return {"file": path.name, "invoice_id": result["invoice_id"], "status": status}


def respond(invoice_id, action, comment):
    with lock:
        row = database.get_invoice(invoice_id)
        if row is None:
            raise LookupError("Invoice not found")
        if not row["raw_extracted_text"]:
            raise ValueError("This invoice has no extracted text, it cannot be checked")

        if workflow.is_waiting_for_human(row["thread_id"]):
            result = workflow.resume(row["thread_id"], action, comment)
        elif action == "recheck":
            result = workflow.recheck_finished(row)
        else:
            raise ValueError("This invoice is not waiting for a review")

        if result.get("stop"):
            raise RuntimeError(f"The workflow stopped: {result['stop']}")
        if action == "recheck":
            database.mark_feedback_revalidated(invoice_id)
            database.add_file_event(row["file_name"], "rechecked", row["file_checksum"],
                                    comment, invoice_id)

        status = file_to_status_folder(invoice_id)
        report = result.get("report") or {}
        return {"status": status, "recommendation": result.get("recommendation"),
                "reason": report.get("reason"), "discrepancies": len(report.get("discrepancies", []))}


def scan_once():
    results = []
    for path in list_incoming_files():
        try:
            if time.time() - path.stat().st_mtime < 2:
                continue
            results.append(process_one(path))
        except FileNotFoundError:
            # Another scan may have moved a file from this snapshot already.
            continue
    return results


def loop():
    while not stop_event.is_set():
        try:
            scan_once()
        except Exception:
            log.exception("Scan of the incoming folder failed")
        stop_event.wait(config.WATCH_INTERVAL_SECONDS)


def start():
    log.info("Watching %s every %s seconds", config.INCOMING_DIR, config.WATCH_INTERVAL_SECONDS)
    threading.Thread(target=loop, name="watcher", daemon=True).start()


def stop():
    stop_event.set()
