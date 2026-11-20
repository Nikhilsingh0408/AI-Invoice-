"""Typed state for the invoice processing graph.

Kept separate from agent.app.graph.state because the two graphs have
different state shapes.
"""

from __future__ import annotations

from typing import Any, TypedDict


class InvoiceProcessingState(TypedDict, total=False):
    """State passed between nodes of the invoice processing workflow."""

    # 1. Input / detection
    file_path: str
    file_name: str
    file_type: str
    checksum: str
    invoice_id: str
    thread_id: str
    is_duplicate: bool

    # 2. Extraction
    raw_text: str
    extraction_method: str

    # 3. Translation
    source_language: str
    translated_text: str
    translation_confidence: float

    # 4. Parsed fields
    fields: dict[str, Any]

    # 5. Validation
    discrepancies: list[dict[str, Any]]
    missing_fields: list[str]
    validation_status: str
    recommendation: str
    erp_matched: bool
    validation_notes: list[str]

    # 6. Reporting / indexing
    report: dict[str, Any]
    report_json_path: str
    report_html_path: str
    indexed: bool

    # 7. Observability
    status: str
    errors: list[str]
    trace: list[dict[str, str]]