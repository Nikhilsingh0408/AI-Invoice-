"""Run with the agent dependencies: python -m unittest discover -s tests -v."""
import os
import asyncio
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal
from uuid import uuid4

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

from app import cache, config, database, main, watcher, rag_runtime
from app.agents import monitor, parser, translator, data_validation, extractor
from starlette.datastructures import UploadFile
from fastapi import HTTPException


class BackendRegressions(unittest.TestCase):
    def test_reflection_retries_both_routes_and_stops_at_limit(self):
        for route, target in (("structured", "structured"), ("semantic", "retry")):
            self.assertEqual(rag_runtime.after_reflection({"accepted": False, "route": route, "attempt": 1}), target)
            self.assertEqual(rag_runtime.after_reflection({"accepted": False, "route": route, "attempt": 3}), "done")

    def test_invalid_reflection_scores_cannot_accept_answer(self):
        with patch.object(rag_runtime.llm, "ask", return_value={"groundedness": "Infinity", "answer_relevance": 99}):
            result = rag_runtime.reflection_node({"question": "q", "answer": "a", "context": "c", "query": "q", "attempt": 1})
        self.assertFalse(result["accepted"])

    def test_unverified_answer_is_not_displayed(self):
        result = {"answer": "unsupported claim", "accepted": False, "route": "semantic", "attempt": 3,
                  "scores": {"average": 0.1}}
        with patch.object(database, "fetch_one", return_value={"invoices": "", "feedback": ""}), \
             patch.object(cache, "get", return_value=None), patch.object(cache, "put"), \
             patch.object(rag_runtime.rag_graph, "invoke", return_value=result), \
             patch.object(database, "save_rag_query"):
            response = rag_runtime.answer_question("q")
        self.assertNotIn("unsupported claim", response["answer"])
        self.assertEqual(response["scores"], result["scores"])

    def test_upload_saves_file_without_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(config, "INCOMING_DIR", Path(folder)):
                result = asyncio.run(main.upload_invoice(UploadFile(
                    filename="../../invoice.pdf", file=io.BytesIO(b"invoice fixture"))))
            self.assertEqual((Path(folder) / result["file_name"]).read_bytes(), b"invoice fixture")
            self.assertNotIn("/", result["file_name"])

    def test_retry_reuses_persisted_extraction(self):
        row = {"raw_extracted_text": "Stored invoice text", "extraction_method": "tesseract"}
        with patch.object(database, "get_invoice", return_value=row), patch.object(extractor, "read_pdf") as read:
            result = extractor.extractor_node({"file_path": "/data/invoice.pdf", "file_type": "pdf", "invoice_id": "known"})
            read.assert_not_called()
            self.assertEqual(result["raw_text"], row["raw_extracted_text"])

    def test_bad_line_arithmetic_is_retained_for_validation(self):
        invoice = parser.parse_text("Widget 2 10 99", {})
        self.assertEqual(invoice["line_items"][0]["line_total"], Decimal("99"))
        findings = data_validation.check_totals(invoice, {})
        self.assertTrue(any(f["field_name"] == "line_1.total" for f in findings))

    def test_pipe_line_arithmetic_is_retained(self):
        self.assertEqual(parser.find_line_items(["Widget | 2 | 10 | 99"])[0]["line_total"], Decimal("99"))

    def test_corrections_cannot_replace_structured_fields(self):
        invoice = parser.parse_text("Widget 2 10 20", {})
        for field in ("line_items", "extras"):
            result, applied = parser.apply_corrections(invoice, [
                {"scope": "header", "field_name": field, "corrected_value": "broken"}])
            self.assertEqual(applied, [])
            self.assertIsInstance(result[field], list if field == "line_items" else dict)

    def test_valid_line_correction(self):
        invoice = parser.parse_text("Widget 2 10 20", {})
        result, applied = parser.apply_corrections(invoice, [
            {"scope": "line_item", "line_number": 1, "field_name": "quantity", "corrected_value": "3"}])
        self.assertEqual(result["line_items"][0]["quantity"], Decimal(3))
        self.assertEqual(len(applied), 1)

    def test_invalid_feedback_rejected_before_database_write(self):
        cases = [dict(field_name="line_items"), dict(field_name="total_amount", corrected_value="invalid"),
                 dict(field_name="invoice_date", corrected_value="invalid"),
                 dict(scope="line_item", field_name="quantity", line_number=0)]
        with patch.object(database, "add_feedback") as save:
            for case in cases:
                values = dict(invoice_id=str(uuid4()), corrected_value="1")
                values.update(case)
                with self.assertRaises(HTTPException) as error:
                    main.add_correction(main.Correction(**values))
                self.assertEqual(error.exception.status_code, 422)
            save.assert_not_called()

    def test_valid_ui_feedback_payload(self):
        with patch.object(database, "get_invoice", return_value={}), patch.object(database, "add_feedback") as save:
            self.assertEqual(main.add_correction(main.Correction(
                invoice_id=str(uuid4()), field_name="total_amount", corrected_value="12.50")), {"saved": True})
            save.assert_called_once()

    def test_database_outage_not_reported_as_missing_invoice(self):
        with patch.object(database, "get_invoice", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                main.get_invoice_or_404(str(uuid4()))

    def test_invalid_uuid_is_validation_error(self):
        with self.assertRaises(HTTPException) as error:
            main.get_invoice_or_404("invalid")
        self.assertEqual(error.exception.status_code, 422)

    def test_failed_invoice_retries_despite_stale_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invoice.pdf"
            path.write_bytes(b"invoice")
            row = {"invoice_id": "known", "processing_status": "failed"}
            with patch.object(cache, "get", return_value="known"), \
                 patch.object(database, "find_by_checksum", return_value=row), \
                 patch.object(database, "add_file_event"), \
                 patch.object(database, "update_file_path"), patch.object(database, "set_thread_id"):
                result = monitor.monitor_node({"file_path": str(path), "thread_id": "retry"})
            self.assertEqual(result["invoice_id"], "known")
            self.assertNotIn("stop", result)

    def test_existing_invoice_is_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invoice.pdf"
            path.write_bytes(b"invoice")
            row = {"invoice_id": "known", "processing_status": "approved", "file_name": path.name}
            with patch.object(database, "find_by_checksum", return_value=row), patch.object(database, "add_file_event"):
                result = monitor.monitor_node({"file_path": str(path), "thread_id": "new"})
            self.assertEqual(result["stop"], "duplicate")

    def test_incoming_symlinks_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "invoice.pdf").write_bytes(b"invoice")
            (root / "link.pdf").symlink_to(root / "invoice.pdf")
            with patch.object(config, "INCOMING_DIR", root):
                self.assertEqual(monitor.list_incoming_files(), [root / "invoice.pdf"])

    def test_same_second_moves_preserve_every_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(watcher.time, "time", return_value=123):
            root = Path(folder)
            target = root / "processed"
            target.mkdir()
            (target / "invoice.pdf").write_text("original")
            for content in ("second", "third"):
                source = root / "invoice.pdf"
                source.write_text(content)
                watcher.move_file(source, target)
            self.assertEqual({p.read_text() for p in target.iterdir()}, {"original", "second", "third"})

    def test_scan_continues_after_file_disappears(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            present = root / "present.pdf"
            present.write_bytes(b"invoice")
            os.utime(present, (1, 1))
            with patch.object(watcher, "list_incoming_files", return_value=[root / "gone.pdf", present]), \
                 patch.object(watcher, "process_one", return_value={"status": "approved"}):
                self.assertEqual(watcher.scan_once(), [{"status": "approved"}])

    def test_bad_translation_confidence_requires_review(self):
        state = {"raw_text": "foreign invoice", "invoice_id": "known", "file_name": "invoice.pdf"}
        for confidence in (None, "invalid", "NaN", "Infinity"):
            with self.subTest(confidence=confidence), \
                 patch.object(translator, "detect_language", return_value="de"), \
                 patch.object(cache, "get", return_value=None), \
                 patch.object(translator.llm, "ask", return_value={"translated_text": "invoice", "confidence": confidence}), \
                 patch.object(config, "load_rules", return_value={"validation_policies": {}}), \
                 patch.object(database, "save_translation"):
                self.assertEqual(translator.translation_node(state)["translation_confidence"], 0)


if __name__ == "__main__":
    unittest.main()
