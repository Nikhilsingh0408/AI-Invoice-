"""Document readers tested with generated, non-sensitive fixtures; no model calls."""
from pathlib import Path
import tempfile
import unittest

from docx import Document
from PIL import Image, ImageDraw, ImageFont

from app.agents.extractor import read_docx, read_image, read_pdf


class DocumentExtraction(unittest.TestCase):
    def test_docx_keeps_paragraphs_and_table_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invoice.docx"
            document = Document()
            document.add_paragraph("Invoice number: TEST-001")
            cells = document.add_table(rows=1, cols=3).rows[0].cells
            for cell, value in zip(cells, ("Widget", "2", "20.00")):
                cell.text = value
            document.save(path)
            text, method = read_docx(path)
            self.assertEqual(method, "docx")
            self.assertIn("Invoice number: TEST-001", text)
            self.assertIn("Widget | 2 | 20.00", text)

    def test_png_and_scanned_pdf_use_ocr(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Image.new("RGB", (1400, 600), "white")
            drawing = ImageDraw.Draw(image)
            drawing.multiline_text((50, 50), "Invoice TEST-001\nVendor: Example Company\nTotal amount: 120.00 USD",
                                   fill="black", font=ImageFont.load_default(size=40), spacing=25)
            png = Path(folder) / "invoice.png"
            pdf = Path(folder) / "invoice.pdf"
            image.save(png)
            image.save(pdf, "PDF", resolution=150)
            for reader, path in ((read_image, png), (read_pdf, pdf)):
                with self.subTest(format=path.suffix):
                    text, method = reader(path)
                    self.assertEqual(method, "tesseract")
                    self.assertIn("120.00", text)


if __name__ == "__main__":
    unittest.main()
