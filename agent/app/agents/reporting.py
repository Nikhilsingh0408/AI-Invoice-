import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import cache, config, database, llm, rules

log = logging.getLogger(__name__)

templates = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
                        autoescape=select_autoescape(["html"]))


def render_html(report):
    return templates.get_template("report.html").render(report=report)


def write_report_files(report):
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    name = report["invoice_number"] or "invoice"
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    base = config.REPORTS_DIR / f"{name}_{report['invoice_id'][:8]}"
    json_path = base.with_suffix(".json")
    html_path = base.with_suffix(".html")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return str(json_path), str(html_path)


def reporting_node(state):
    settings = config.load_rules()
    invoice = state["invoice"]
    discrepancies = state["data_findings"] + state["business_findings"]
    confidence = state.get("translation_confidence")

    validation_status, recommendation, reason = rules.decide(discrepancies, confidence, settings,
                                                             state.get("human_checked", False))

    by_severity = {}
    for d in discrepancies:
        by_severity[d["severity"]] = by_severity.get(d["severity"], 0) + 1

    answer = llm.ask("reporting", "write_report", {
        "invoice": json.dumps(invoice, default=str, indent=2),
        "discrepancies": json.dumps(discrepancies, default=str, indent=2),
        "missing_fields": ", ".join(state["missing_fields"]) or "none",
        "recommendation": recommendation,
        "reason": reason,
    }) or {}

    row = database.get_invoice(state["invoice_id"])
    run_number = row["run_number"] + 1

    report = {
        "invoice_id": state["invoice_id"],
        "file_name": state["file_name"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_number": run_number,
        "invoice_number": invoice["invoice_number"],
        "vendor_name": invoice["vendor_name"],
        "vendor_id": (state["erp"].get("vendor") or {}).get("vendor_id"),
        "po_number": invoice["po_number"],
        "invoice_date": invoice["invoice_date"],
        "currency": invoice["currency"],
        "subtotal_amount": invoice["subtotal_amount"],
        "tax_amount": invoice["tax_amount"],
        "total_amount": invoice["total_amount"],
        "line_items": invoice["line_items"],
        "extraction_method": state.get("extraction_method") or row["extraction_method"],
        "missing_fields": state["missing_fields"],
        "discrepancies": discrepancies,
        "validation_status": validation_status,
        "recommendation": recommendation,
        "reason": reason,
        "risk_level": state.get("risk_level"),
        "data_summary": state.get("data_summary"),
        "business_summary": state.get("business_summary"),
        "summary": answer.get("summary") or f"Recommendation: {recommendation} ({reason}).",
        "next_steps": answer.get("next_steps") or [],
        "applied_corrections": state.get("applied_corrections", []),
    }
    report_settings = settings.get("reporting", {})
    if report_settings.get("include_translation_confidence", True):
        report["translation"] = {"source_language": state.get("language"), "confidence": confidence}
    if report_settings.get("include_discrepancy_summary", True):
        report["discrepancy_summary"] = {"total": len(discrepancies), "by_severity": by_severity}

    database.save_discrepancies(state["invoice_id"], run_number, discrepancies)
    database.save_outcome(state["invoice_id"], run_number, validation_status, recommendation, report)
    cache.put(f"checksum:{state['checksum']}", state["invoice_id"])
    event = "pending_review" if recommendation == "review" else "audited"
    database.add_file_event(state["file_name"], event, state["checksum"],
                            f"{recommendation}: {reason}", state["invoice_id"])

    log.info("Outcome for %s: %s / %s (%d discrepancies, run %d)",
             state["file_name"], validation_status, recommendation, len(discrepancies), run_number)
    return {"recommendation": recommendation, "validation_status": validation_status, "report": report}


def report_files_node(state):
    row = database.get_invoice(state["invoice_id"])
    report = row["report_json"]
    try:
        json_path, html_path = write_report_files(report)
        database.save_report_paths(state["invoice_id"], json_path, html_path)
        log.info("Wrote report files for %s", state["file_name"])
    except OSError as error:
        log.warning("Could not write report files: %s", error)
    return {}
