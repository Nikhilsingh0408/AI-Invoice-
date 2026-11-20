CREATE TABLE audit.invoice_audit (
    invoice_id             UUID PRIMARY KEY,
    file_path              VARCHAR(512) NOT NULL,
    file_name              VARCHAR(255),
    file_type              VARCHAR(16),
    file_checksum          VARCHAR(64) UNIQUE,
    extraction_method      VARCHAR(32),
    raw_extracted_text     TEXT,
    translated_text        TEXT,
    source_language        VARCHAR(8),
    translation_confidence NUMERIC(4,3),
    invoice_number         VARCHAR(64),
    vendor_name            VARCHAR(255),
    vendor_id              VARCHAR(32),
    po_number              VARCHAR(64),
    invoice_date           DATE,
    currency               CHAR(3),
    subtotal_amount        NUMERIC(14,2),
    tax_amount             NUMERIC(14,2),
    total_amount           NUMERIC(14,2),
    processing_status      VARCHAR(32) NOT NULL DEFAULT 'detected',
    validation_status      VARCHAR(32),
    recommendation         VARCHAR(32),
    error_message          TEXT,
    extracted_raw          JSONB NOT NULL DEFAULT '{}'::jsonb,
    report_json            JSONB,
    report_html_path       VARCHAR(512),
    report_json_path       VARCHAR(512),
    run_number             INTEGER NOT NULL DEFAULT 0,
    thread_id              VARCHAR(128),
    indexed_at             TIMESTAMPTZ,
    detected_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_invoice_vendor ON audit.invoice_audit (vendor_name);
CREATE INDEX idx_invoice_number ON audit.invoice_audit (invoice_number);
CREATE INDEX idx_invoice_date   ON audit.invoice_audit (invoice_date);
CREATE INDEX idx_invoice_rec    ON audit.invoice_audit (recommendation);

CREATE TABLE audit.invoice_line_items (
    id           BIGSERIAL PRIMARY KEY,
    invoice_id   UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    line_number  INTEGER NOT NULL,
    item_code    VARCHAR(64),
    description  TEXT,
    quantity     NUMERIC(12,3),
    unit_price   NUMERIC(14,4),
    line_total   NUMERIC(14,2)
);

CREATE INDEX idx_line_items_invoice ON audit.invoice_line_items (invoice_id);

CREATE TABLE audit.discrepancies (
    id             BIGSERIAL PRIMARY KEY,
    invoice_id     UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    run_number     INTEGER NOT NULL,
    source         VARCHAR(16) NOT NULL,
    field_name     VARCHAR(64) NOT NULL,
    line_number    INTEGER,
    invoice_value  TEXT,
    erp_value      TEXT,
    deviation_pct  NUMERIC(14,4),
    severity       VARCHAR(16) NOT NULL,
    action         VARCHAR(32),
    explanation    TEXT,
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_disc_invoice ON audit.discrepancies (invoice_id);

CREATE TABLE audit.human_feedback (
    id               BIGSERIAL PRIMARY KEY,
    invoice_id       UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    scope            VARCHAR(16) NOT NULL DEFAULT 'header',
    line_number      INTEGER,
    field_name       VARCHAR(64) NOT NULL,
    original_value   TEXT,
    corrected_value  TEXT,
    comment          TEXT,
    corrected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    revalidated      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_feedback_invoice ON audit.human_feedback (invoice_id);

CREATE TABLE audit.file_events (
    id            BIGSERIAL PRIMARY KEY,
    file_name     VARCHAR(255),
    file_checksum VARCHAR(64),
    event_type    VARCHAR(32) NOT NULL,
    detail        TEXT,
    invoice_id    UUID REFERENCES audit.invoice_audit(invoice_id),
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit.rag_queries (
    id                BIGSERIAL PRIMARY KEY,
    question          TEXT NOT NULL,
    route             VARCHAR(16),
    answer            TEXT,
    attempts          INTEGER NOT NULL DEFAULT 1,
    answer_relevance  NUMERIC(4,3),
    groundedness      NUMERIC(4,3),
    context_relevance NUMERIC(4,3),
    avg_score         NUMERIC(4,3),
    accepted          BOOLEAN NOT NULL DEFAULT FALSE,
    asked_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
