CREATE TABLE erp.vendors (
    vendor_id    VARCHAR(32) PRIMARY KEY,
    vendor_name  VARCHAR(255) NOT NULL,
    country      VARCHAR(64),
    currency     CHAR(3) NOT NULL
);

CREATE TABLE erp.sku_master (
    item_code    VARCHAR(64) PRIMARY KEY,
    category     VARCHAR(64),
    uom          VARCHAR(32),
    gst_rate     NUMERIC(6,3) NOT NULL DEFAULT 0
);

CREATE TABLE erp.purchase_orders (
    po_number    VARCHAR(64) PRIMARY KEY,
    vendor_id    VARCHAR(32) NOT NULL REFERENCES erp.vendors(vendor_id)
);

CREATE TABLE erp.po_line_items (
    id           BIGSERIAL PRIMARY KEY,
    po_number    VARCHAR(64) NOT NULL REFERENCES erp.purchase_orders(po_number),
    line_number  INTEGER NOT NULL,
    item_code    VARCHAR(64) NOT NULL,
    description  TEXT,
    qty          NUMERIC(14,3) NOT NULL,
    unit_price   NUMERIC(14,4) NOT NULL,
    currency     CHAR(3) NOT NULL
);

CREATE INDEX idx_po_lines_po ON erp.po_line_items (po_number);
