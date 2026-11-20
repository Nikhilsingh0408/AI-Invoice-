"""Explicit live check for the isolated deployment verifier; incurs Bedrock usage."""
import io
import json
import time

import httpx
from docx import Document

from app import database


def main():
    document = Document()
    for line in (
        "Rechnung", "Rechnungsnummer: VERIFY-DE-001", "Rechnungsdatum: 2026-01-15",
        "Lieferant: HafenLogistik GmbH", "Lieferantennummer: VEND-004",
        "Bestellnummer: PO-1004", "Währung: EUR", "Zwischensumme: 1600.00",
        "MwSt 10%: 160.00", "Gesamtbetrag: 1760.00",
        "Vielen Dank für Ihren Auftrag. Die Lieferung umfasst Transportkisten und Sicherheitswesten.",
    ):
        document.add_paragraph(line)
    table = document.add_table(rows=0, cols=5)
    for values in (("SKU-301", "Transportkisten", "40", "25.00", "1000.00"),
                   ("SKU-302", "Sicherheitswesten", "60", "10.00", "600.00")):
        for cell, value in zip(table.add_row().cells, values):
            cell.text = value
    data = io.BytesIO()
    document.save(data)
    client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=180)
    database.open_pools()
    try:
        uploaded = client.post("/uploads", files={"file": ("verification.docx", data.getvalue())})
        uploaded.raise_for_status()
        name = uploaded.json()["file_name"]
        deadline = time.monotonic() + 240
        row = None
        while time.monotonic() < deadline:
            row = database.fetch_one("SELECT * FROM audit.invoice_audit WHERE file_name = %s", (name,))
            if row and row["processing_status"] in ("approved", "rejected", "pending_review", "failed"):
                break
            time.sleep(2)
        assert row and row["report_json"], "Invoice did not produce an audit report"
        assert row["raw_extracted_text"] and row["translated_text"], "Live extraction/translation failed"
        assert row["source_language"] == "de", "German fixture was not detected"
        invoice_id = str(row["invoice_id"])
        if row["processing_status"] == "pending_review":
            review = client.post("/review", json={"invoice_id": invoice_id, "action": "approve", "comment": "Isolated verification fixture"})
            review.raise_for_status()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            row = database.get_invoice(invoice_id)
            if row["indexed_at"]:
                break
            time.sleep(2)
        assert row["indexed_at"], "Live embedding/indexing failed"
        report = client.get(f"/invoices/{invoice_id}/report.html")
        report.raise_for_status()
        answer = client.post("/query", json={"question": "What is the total amount and currency on this invoice?", "invoice_id": invoice_id})
        answer.raise_for_status()
        payload = answer.json()
        assert payload["accepted"], "Live RAG answer did not pass reflection"
        print(json.dumps({"invoice_id": invoice_id, "status": row["processing_status"],
                          "language": row["source_language"], "indexed": True,
                          "query_accepted": payload["accepted"], "scores": payload.get("scores")}))
    finally:
        client.close()
        database.close_pools()


if __name__ == "__main__":
    main()
