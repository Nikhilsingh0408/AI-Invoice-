"""Opt-in tests against an isolated database initialized with db/init SQL."""
import os
import unittest
from decimal import Decimal

from app import database, workflow
from app.agents.parser import parse_text
from psycopg import DataError


@unittest.skipUnless(os.getenv("RUN_DATABASE_TESTS") == "1", "Requires an isolated PostgreSQL database")
class DatabaseIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.open_pools()

    @classmethod
    def tearDownClass(cls):
        database.close_pools()

    def test_fields_and_lines_rollback_together(self):
        invoice_id = database.create_invoice("/tmp/test.pdf", "test.pdf", "pdf", "rollback-test", "test-thread")
        invoice = parse_text("Invoice number: ORIGINAL\nWidget 2 10 20", {})
        database.save_fields(invoice_id, invoice)
        invoice["invoice_number"] = "CHANGED"
        invoice["line_items"][0]["quantity"] = Decimal("1e30")
        with self.assertRaises(DataError):
            database.save_fields(invoice_id, invoice)
        self.assertEqual(database.get_invoice(invoice_id)["invoice_number"], "ORIGINAL")
        lines = database.fetch_all("SELECT quantity FROM audit.invoice_line_items WHERE invoice_id = %s", (invoice_id,))
        self.assertEqual(lines, [{"quantity": Decimal("2.000")}])

    def test_workflow_compiles_with_postgres_checkpointer(self):
        workflow.setup()
        self.assertIsNotNone(workflow.invoice_graph)
        self.assertFalse(workflow.is_waiting_for_human("unused-test-thread"))


if __name__ == "__main__":
    unittest.main()
