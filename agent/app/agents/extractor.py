import logging
from pathlib import Path

import pdfplumber
import pytesseract
from docx import Document
from pdf2image import convert_from_path
from PIL import Image

from .. import config, database

log = logging.getLogger(__name__)


def read_pdf(path):
    with pdfplumber.open(path) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages).strip()
    if len(text) >= 50:
        return text, "pdfplumber"
    # ocr
    log.info("%s has no text layer, using OCR", path.name)
    pages = convert_from_path(str(path), dpi=300)
    text = "\n".join(pytesseract.image_to_string(page, lang=config.OCR_LANGUAGES) for page in pages)
    return text.strip(), "tesseract"


def read_docx(path):
    document = Document(str(path))
    lines = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines).strip(), "docx"


def read_image(path):
    image = Image.open(path)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    return pytesseract.image_to_string(image, lang=config.OCR_LANGUAGES).strip(), "tesseract"


def extractor_node(state):
    path = Path(state["file_path"])
    file_type = state["file_type"]
    row = database.get_invoice(state["invoice_id"])
    if row and row["raw_extracted_text"]:
        return {"raw_text": row["raw_extracted_text"], "extraction_method": row["extraction_method"]}
    try:
        if file_type == "pdf":
            text, method = read_pdf(path)
        elif file_type == "docx":
            text, method = read_docx(path)
        else:
            text, method = read_image(path)
    except Exception as error:
        log.exception("Could not read %s", path.name)
        database.mark_failed(state["invoice_id"], f"Extraction failed: {error}")
        database.add_file_event(path.name, "failed", state["checksum"], f"Extraction failed: {error}",
                                state["invoice_id"])
        return {"stop": "extraction failed"}

    if not text:
        database.mark_failed(state["invoice_id"], "No text could be read from the file")
        database.add_file_event(path.name, "failed", state["checksum"], "No text found", state["invoice_id"])
        return {"stop": "no text found"}

    database.save_raw_text(state["invoice_id"], method, text)
    database.add_file_event(path.name, "extracted", state["checksum"], f"Text extracted using {method}",
                            state["invoice_id"])
    log.info("Read %d characters from %s using %s", len(text), path.name, method)
    return {"raw_text": text, "extraction_method": method}
