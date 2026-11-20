import json
import uuid

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

pool = ConnectionPool(config.POSTGRES_URL, min_size=1, max_size=10, open=False,
                      kwargs={"row_factory": dict_row})

checkpoint_pool = ConnectionPool(config.POSTGRES_URL, min_size=1, max_size=4, open=False,
                                 kwargs={"autocommit": True, "row_factory": dict_row,
                                         "options": "-c search_path=langgraph"})


def open_pools():
    pool.open(wait=True, timeout=30)
    checkpoint_pool.open(wait=True, timeout=30)
    tables = {t["table_name"] for t in fetch_all(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'audit'")}
    for needed in ["invoice_audit", "invoice_line_items", "discrepancies", "human_feedback"]:
        if needed not in tables:
            raise RuntimeError(f"audit.{needed} is missing. Apply the missing db/init schema SQL after backing up the database.")


def close_pools():
    pool.close()
    checkpoint_pool.close()


def run(sql, params=None):
    with pool.connection() as conn:
        conn.execute(sql, params)


def fetch_all(sql, params=None):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql, params=None):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def to_json(value):
    return Jsonb(json.loads(json.dumps(value, default=str)))


def ping():
    try:
        fetch_one("SELECT 1")
        return True
    except Exception:
        return False


def update_invoice(invoice_id, **columns):
    sets = ", ".join(f"{name} = %s" for name in columns)
    values = [to_json(v) if isinstance(v, (dict, list)) else v for v in columns.values()]
    run(f"UPDATE audit.invoice_audit SET {sets} WHERE invoice_id = %s", values + [invoice_id])


def find_by_checksum(checksum):
    return fetch_one("SELECT * FROM audit.invoice_audit WHERE file_checksum = %s", (checksum,))


def create_invoice(file_path, file_name, file_type, checksum, thread_id):
    invoice_id = str(uuid.uuid4())
    run("""INSERT INTO audit.invoice_audit (invoice_id, file_path, file_name, file_type, file_checksum, thread_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (invoice_id, file_path, file_name, file_type, checksum, thread_id))
    return invoice_id


def get_invoice(invoice_id):
    return fetch_one("SELECT * FROM audit.invoice_audit WHERE invoice_id = %s", (invoice_id,))


def list_invoices():
    return fetch_all("SELECT invoice_id, invoice_number FROM audit.invoice_audit ORDER BY processed_at")


def save_raw_text(invoice_id, method, text):
    update_invoice(invoice_id, extraction_method=method, raw_extracted_text=text, processing_status="extracted")


def save_translation(invoice_id, language, translated_text, confidence):
    update_invoice(invoice_id, source_language=language, translated_text=translated_text,
                   translation_confidence=confidence)


def save_fields(invoice_id, invoice):
    # Commit the header and replacement lines together, or retain the previous data.
    with pool.connection() as conn:
        conn.execute("""UPDATE audit.invoice_audit
                       SET invoice_number = %s, vendor_name = %s, po_number = %s,
                           invoice_date = %s, currency = %s, subtotal_amount = %s,
                           tax_amount = %s, total_amount = %s, extracted_raw = %s
                       WHERE invoice_id = %s""",
                     (invoice["invoice_number"], invoice["vendor_name"], invoice["po_number"],
                      invoice["invoice_date"], invoice["currency"], invoice["subtotal_amount"],
                      invoice["tax_amount"], invoice["total_amount"], to_json(invoice["extras"]), invoice_id))
        conn.execute("DELETE FROM audit.invoice_line_items WHERE invoice_id = %s", (invoice_id,))
        for item in invoice["line_items"]:
            conn.execute("""INSERT INTO audit.invoice_line_items
                           (invoice_id, line_number, item_code, description, quantity, unit_price, line_total)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                         (invoice_id, item["line_number"], item["item_code"], item["description"],
                          item["quantity"], item["unit_price"], item["line_total"]))


def save_vendor_id(invoice_id, vendor_id):
    update_invoice(invoice_id, vendor_id=vendor_id)


def mark_failed(invoice_id, message):
    update_invoice(invoice_id, processing_status="failed", error_message=message[:2000])


def save_outcome(invoice_id, run_number, validation_status, recommendation, report):
    status = {"approve": "approved", "review": "pending_review", "reject": "rejected"}[recommendation]
    run("""UPDATE audit.invoice_audit
           SET validation_status = %s, recommendation = %s, processing_status = %s,
               report_json = %s, run_number = %s, processed_at = now(), error_message = NULL
           WHERE invoice_id = %s""",
        (validation_status, recommendation, status, to_json(report), run_number, invoice_id))


def save_report_paths(invoice_id, json_path, html_path):
    update_invoice(invoice_id, report_json_path=json_path, report_html_path=html_path)


def set_thread_id(invoice_id, thread_id):
    update_invoice(invoice_id, thread_id=thread_id)


def update_file_path(invoice_id, path):
    update_invoice(invoice_id, file_path=path)


def mark_indexed(invoice_id):
    run("UPDATE audit.invoice_audit SET indexed_at = now() WHERE invoice_id = %s", (invoice_id,))


def invoices_with_text():
    return fetch_all("SELECT invoice_id FROM audit.invoice_audit WHERE raw_extracted_text IS NOT NULL "
                     "AND processing_status IN ('approved', 'rejected') ORDER BY processed_at")


def invoice_summary_rows(invoice_id=None):
    return fetch_all(
        """SELECT a.invoice_number, a.file_name, a.vendor_name, a.vendor_id, a.po_number, a.invoice_date,
                  a.currency, a.subtotal_amount, a.tax_amount, a.total_amount, a.processing_status,
                  a.recommendation, a.source_language, a.translation_confidence,
                  (SELECT json_agg(json_build_object('field', d.field_name, 'invoice_value', d.invoice_value,
                                                    'erp_value', d.erp_value, 'deviation_pct', d.deviation_pct,
                                                    'severity', d.severity, 'explanation', d.explanation))
                   FROM audit.discrepancies d
                   WHERE d.invoice_id = a.invoice_id AND d.run_number = a.run_number) AS discrepancies
           FROM audit.invoice_audit a
           WHERE %(id)s::uuid IS NULL OR a.invoice_id = %(id)s::uuid
           ORDER BY a.processed_at""",
        {"id": invoice_id},
    )


def save_discrepancies(invoice_id, run_number, discrepancies):
    for d in discrepancies:
        run("""INSERT INTO audit.discrepancies
               (invoice_id, run_number, source, field_name, line_number, invoice_value, erp_value,
                deviation_pct, severity, action, explanation)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (invoice_id, run_number, d["source"], d["field_name"], d.get("line_number"),
             d.get("invoice_value"), d.get("erp_value"), d.get("deviation_pct"),
             d["severity"], d["action"], d.get("explanation")))


def add_feedback(invoice_id, field_name, original_value, corrected_value,
                 scope="header", line_number=None, comment=None):
    run("""INSERT INTO audit.human_feedback
           (invoice_id, scope, line_number, field_name, original_value, corrected_value, comment)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (invoice_id, scope, line_number, field_name, original_value, corrected_value, comment))


def latest_corrections(invoice_id):
    return fetch_all("""SELECT DISTINCT ON (scope, line_number, field_name)
                               scope, line_number, field_name, corrected_value
                        FROM audit.human_feedback
                        WHERE invoice_id = %s AND scope IN ('header', 'line_item')
                        ORDER BY scope, line_number, field_name, corrected_at DESC""", (invoice_id,))


def mark_feedback_revalidated(invoice_id):
    run("UPDATE audit.human_feedback SET revalidated = TRUE WHERE invoice_id = %s AND revalidated = FALSE",
        (invoice_id,))


def save_decision(invoice_id, decision, comment):
    row = get_invoice(invoice_id)
    add_feedback(invoice_id, "recommendation", row["recommendation"], decision, scope="decision", comment=comment)
    run("""UPDATE audit.invoice_audit
           SET processing_status = %s, report_json = COALESCE(report_json, '{}'::jsonb) || %s
           WHERE invoice_id = %s""",
        ("approved" if decision == "approve" else "rejected", to_json({"human_decision": decision}), invoice_id))


def add_file_event(file_name, event_type, checksum=None, detail=None, invoice_id=None):
    run("""INSERT INTO audit.file_events (file_name, file_checksum, event_type, detail, invoice_id)
           VALUES (%s, %s, %s, %s, %s)""", (file_name, checksum, event_type, detail, invoice_id))


def save_rag_query(question, route, answer, attempts, scores, accepted):
    run("""INSERT INTO audit.rag_queries
           (question, route, answer, attempts, answer_relevance, groundedness, context_relevance,
            avg_score, accepted)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (question, route, answer, attempts, scores.get("answer_relevance"), scores.get("groundedness"),
         scores.get("context_relevance"), scores.get("average"), accepted))
