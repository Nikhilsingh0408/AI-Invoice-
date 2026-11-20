# AI Invoice Auditor – Technical Reference

This document describes the whole system down to the detail: every container, every table and column, every step of the workflows, every button in the dashboard, the exact HTTP request each button sends, the response it gets back, and which rows and columns change as a result.

The short overview is in [`README.md`](../README.md). This file is the place to look things up.

---

## Contents

1. [System overview](#1-system-overview)
2. [Containers, images and networking](#2-containers-images-and-networking)
3. [Start-up sequence](#3-start-up-sequence)
4. [Configuration](#4-configuration)
5. [Database schema (every table, every column)](#5-database-schema)
6. [Redis keys](#6-redis-keys)
7. [Qdrant collection](#7-qdrant-collection)
8. [Files on disk](#8-files-on-disk)
9. [How the AI models are called](#9-how-the-ai-models-are-called)
10. [The invoice workflow, node by node](#10-the-invoice-workflow-node-by-node)
11. [The folder watcher](#11-the-folder-watcher)
12. [Human review, re-check and decisions](#12-human-review-re-check-and-decisions)
13. [The RAG question-answering graph](#13-the-rag-question-answering-graph)
14. [RAGAS evaluation](#14-ragas-evaluation)
15. [Agent API – every endpoint](#15-agent-api--every-endpoint)
16. [Mock ERP API – every endpoint](#16-mock-erp-api--every-endpoint)
17. [The dashboard – every screen and button](#17-the-dashboard--every-screen-and-button)
18. [Life of one invoice: what changes where](#18-life-of-one-invoice-what-changes-where)
19. [Status values and event types](#19-status-values-and-event-types)
20. [Error handling and failure modes](#20-error-handling-and-failure-modes)
21. [Known limitations](#21-known-limitations)
22. [Dependency versions](#22-dependency-versions)

---

## 1. System overview

The system takes invoice files (PDF, DOCX, PNG/JPG/TIFF), reads the text (OCR if needed), translates non-English invoices, extracts the fields, validates them (arithmetic and against an ERP), decides **approve / review / reject**, writes a report, and makes everything searchable for a question-answering chat.

```
 browser ──► streamlit-ui (8501) ──HTTP──► agent (8000) ──HTTP──► mock-erp (8000 → host 8001)
                 │ reads                     │  │  │  │                │ reads
                 ▼                           ▼  │  │  ▼                ▼
             postgres ◄──────────────────────┘  │  │  AWS Bedrock    postgres (erp schema)
             (audit schema)                     │  └──► redis (cache)
                                                ▼
                                             qdrant (vectors)
```

Data ownership rules:

| Data | Written by | Read by |
|---|---|---|
| `erp.*` tables | seed SQL only | mock-erp only |
| `audit.*` tables | agent only | agent, dashboard (read only) |
| `langgraph.*` tables | agent (LangGraph checkpointer) | agent |
| Qdrant `invoice_chunks` | agent | agent |
| Redis | agent | agent |

The dashboard never writes to the database. Every change it makes goes through an agent HTTP endpoint.

---

## 2. Containers, images and networking

Defined in [`compose.yaml`](../compose.yaml). Compose project name: `invoice-auditor`. Network: `invoice-auditor-net` (bridge). Containers reach each other by service name and **container** port.

| Service | Image | Host port → container port | Restart | Health check | Depends on (healthy) |
|---|---|---|---|---|---|
| `postgres` | `postgres:16.6-alpine` | 5432 → 5432 | `unless-stopped` | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` every 10 s, 10 retries, 20 s start period | – |
| `qdrant` | `qdrant/qdrant:v1.12.5` | 6333 → 6333 | `unless-stopped` | raw TCP `GET /readyz` expecting 200, every 10 s | – |
| `redis` | `redis:7.4.1-alpine` | 6379 → 6379 | `unless-stopped` | `redis-cli ping` every 10 s | – |
| `mock-erp` | built `invoice-auditor-mock-erp:1.0` | 8001 → 8000 | `unless-stopped` | Python `urlopen('http://127.0.0.1:8000/health')` every 15 s | postgres |
| `agent` | built `invoice-auditor-agent:1.0` | 8000 → 8000 | `on-failure:3` | `curl --fail http://127.0.0.1:8000/health` every 20 s, 60 s start period | postgres, qdrant, redis, mock-erp |
| `streamlit-ui` | built `invoice-auditor-ui:1.0` | 8501 → 8501 | `unless-stopped` | `urlopen('http://127.0.0.1:8501/_stcore/health')` every 15 s | postgres, agent |

Redis runs with `--save "" --appendonly no --maxmemory 256mb --maxmemory-policy allkeys-lru`, so it never writes to disk and evicts the least recently used keys when full.

### Volumes and mounts

| Mount | Into | Mode | Purpose |
|---|---|---|---|
| named volume `postgres_data` | postgres `/var/lib/postgresql/data` | rw | database files |
| `./db/init` | postgres `/docker-entrypoint-initdb.d` | ro | SQL run once on an empty volume |
| named volume `qdrant_data` | qdrant `/qdrant/storage` | rw | vectors |
| `./data` | agent `/data` | rw | incoming / pending / processed / rejected / failed folders |
| `./outputs/reports` | agent `/reports` | rw | JSON + HTML reports |
| `./config` | agent `/app/config` | **ro** | rules, personas, prompts, RAGAS starter questions |
| `${HOME}/.aws` | agent `/aws` | ro | AWS credentials (`AWS_SHARED_CREDENTIALS_FILE=/aws/credentials`, `AWS_CONFIG_FILE=/aws/config`) |

### Environment variables set by compose

All three Python services get `env_file: .env` plus:

| Service | Variables |
|---|---|
| mock-erp | `POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432` |
| agent | `POSTGRES_HOST`, `POSTGRES_PORT`, `QDRANT_HOST=qdrant`, `QDRANT_PORT=6333`, `REDIS_HOST=redis`, `REDIS_PORT=6379`, `MOCK_ERP_URL=http://mock-erp:8000`, `INCOMING_DIR=/data/incoming`, `PROCESSED_DIR=/data/processed`, `PENDING_DIR=/data/pending`, `REJECTED_DIR=/data/rejected`, `FAILED_DIR=/data/failed`, `REPORTS_DIR=/reports`, `CONFIG_DIR=/app/config`, AWS file paths |
| streamlit-ui | `POSTGRES_HOST`, `POSTGRES_PORT`, `AGENT_URL=http://agent:8000` |

### Images

All three are built from `python:3.11.11-slim-bookworm`, set `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `PIP_NO_CACHE_DIR=1`, and run as a non-root system user:

| Image | User (uid) | Extra OS packages | Command |
|---|---|---|---|
| agent | `auditor` (1000) | `tesseract-ocr` + `eng spa deu fra` language packs, `poppler-utils`, `curl` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| mock-erp | `erp` (1001) | – | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| ui | `dashboard` (1002) | – (sets `STREAMLIT_BROWSER_GATHER_USAGE_STATS=false`) | `streamlit run app/main.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true` |

No secret is baked into any image. Secrets come from `.env` and `~/.aws` at run time.

---

## 3. Start-up sequence

1. **postgres** starts. If `postgres_data` is empty it runs `db/init/*.sql` in name order: `00_schemas.sql` → `10_erp_schema.sql` → `11_erp_seed.sql` → `20_audit_schema.sql`.
2. **qdrant** and **redis** start.
3. **mock-erp** starts once postgres is healthy. Its startup hook opens a connection pool (1–5 connections) and checks `SELECT count(*) FROM erp.vendors`. If it is 0 it raises `RuntimeError("erp.vendors is empty - the seed SQL did not run")` and the container fails.
4. **agent** starts once postgres, qdrant, redis and mock-erp are healthy. `lifespan()` in [`main.py`](../agent/app/main.py) runs, in this order:
   1. `database.open_pools()` – opens the main pool (1–10 connections, `dict_row`) and the checkpoint pool (1–4 connections, autocommit, `search_path=langgraph`). Then checks that `audit.invoice_audit`, `invoice_line_items`, `discrepancies` and `human_feedback` exist; if not: `RuntimeError("audit.<name> is missing. Run 'docker compose down -v' and start again.")`.
   2. `workflow.setup()` – `PostgresSaver(checkpoint_pool).setup()` creates the LangGraph checkpoint tables in the `langgraph` schema, then compiles the invoice graph with that checkpointer.
   3. `rag.qdrant.get_collections()` – fails start-up if Qdrant is not reachable.
   4. `erp_client.is_healthy()` – `GET http://mock-erp:8000/health`; if not 200: `RuntimeError("mock-erp is not reachable at …")`.
   5. `config.load_rules()` – fails if `rules.yaml` is missing.
   6. `cache.ping()` – if Redis does not answer only a warning is logged (`Redis is not reachable - running without a cache`).
   7. Creates the six data folders if they do not exist.
   8. If Qdrant has 0 points but Postgres has approved/rejected invoices with text, starts `rag.rebuild_index()` in a background thread.
   9. `watcher.start()` – starts the folder-watching thread.
   10. Logs `Agent is ready`.
5. **streamlit-ui** starts once agent is healthy.

On shutdown the agent stops the watcher and closes both pools.

---

## 4. Configuration

### 4.1 `.env`

| Variable | Used by | Meaning | Example |
|---|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | all | database login | `auditor` / … / `invoice_auditor` |
| `AWS_REGION_NAME` | agent | Bedrock region | `us-east-1` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | agent | only if no `~/.aws` | – |
| `LLM_REASONING_MODEL` | agent | role `reasoning` (validation summaries, report, router, RAGAS judge) | `global.amazon.nova-2-lite-v1:0` |
| `LLM_RAG_MODEL` | agent | role `rag` (answer generation, reflection) | `global.amazon.nova-2-lite-v1:0` |
| `LLM_TRANSLATION_MODEL` | agent | role `translation` | `global.amazon.nova-2-lite-v1:0` |
| `EMBEDDING_MODEL` | agent | embeddings for Qdrant and RAGAS | `cohere.embed-english-v3` |
| `LOG_LEVEL` | agent | optional, default `INFO` | – |

A model id may be written with a `bedrock/` prefix; `llm.model_id()` strips it.

### 4.2 Fixed settings in [`config.py`](../agent/app/config.py)

| Name | Value | Meaning |
|---|---|---|
| `QDRANT_COLLECTION` | `invoice_chunks` | collection name |
| `REDIS_TTL_SECONDS` | 86400 | every cache key expires after 24 h |
| `OCR_LANGUAGES` | `eng+spa+deu+fra` | Tesseract languages |
| `WATCH_INTERVAL_SECONDS` | 5 | folder scan interval |
| `RAG_SCORE_THRESHOLD` | 0.7 | an answer is accepted if the triad average is **strictly greater** |
| `RAG_MAX_RETRIES` | 3 | max attempts for a semantic question |
| `RAG_TOP_K` | 6 | chunks kept for the context |

`read_yaml(name)` reads `CONFIG_DIR/name` on every call, so YAML changes are used immediately without a restart. A missing file raises `FileNotFoundError("Config file … is missing. Is the config folder mounted?")`.

### 4.3 [`rules.yaml`](../config/rules.yaml)

| Key | Value | Used in |
|---|---|---|
| `required_fields.header` | `invoice_no, invoice_date, vendor_id, currency, total_amount` | `check_required_fields()` |
| `required_fields.line_item` | `item_code, description, qty, unit_price, total` | `check_required_fields()` |
| `data_types` | documentation only | not used by code |
| `tolerances.price_difference_percent` | 5 | business validation, unit price |
| `tolerances.quantity_difference_percent` | 0 | business validation, quantity |
| `tolerances.tax_difference_percent` | 2 | business validation, tax |
| `accepted_currencies` | USD, EUR, INR, GBP | `check_currency()` |
| `currency_symbol_map` | `$`→USD, `€`→EUR, `₹`→INR, `£`→GBP | parser |
| `validation_policies.*_action` | see below | `rules.action_for()` |
| `validation_policies.auto_approve_confidence_threshold` | 0.95 | translation cache + `decide()` |
| `reporting.include_translation_confidence` | true | adds `translation` to the report |
| `reporting.include_discrepancy_summary` | true | adds `discrepancy_summary` to the report |
| `reporting.report_format`, `output_dir`, `logging.*` | documentation only | not used by code |

Policies (action → severity: `reject`→`high`, `manual_review`→`medium`, `flag`→`low`; an unknown policy name defaults to `manual_review`):

| Policy | Action | Raised when |
|---|---|---|
| `missing_field_action` | flag | a required field or line item is missing |
| `total_mismatch_action` | manual_review | line total, subtotal or total arithmetic is off |
| `invalid_currency_action` | reject | currency not accepted, or invoice currency ≠ PO currency |
| `unknown_vendor_action` | reject | vendor not found in the ERP |
| `vendor_mismatch_action` | reject | PO belongs to another vendor |
| `po_not_found_action` | manual_review | PO not found |
| `item_not_on_po_action` | manual_review | an invoice line has no matching PO line |
| `quantity_mismatch_action` | manual_review | qty deviation > tolerance |
| `price_mismatch_action` | manual_review | unit price deviation > tolerance |
| `tax_mismatch_action` | manual_review | tax vs rate on the invoice, or vs ERP SKU rates |
| `inconsistent_field_action` | manual_review | future date, negative amount, quantity ≤ 0 |

### 4.4 [`personas.yaml`](../config/personas.yaml)

Each persona has a `system` message, a `temperature` and a `model_role`.

| Persona | Role | Temp. | Used by prompt |
|---|---|---|---|
| `translation` | translation | 0.0 | `translation/translate` |
| `data_validation` | reasoning | 0.0 | `data_validation/assess_data_quality` |
| `business_validation` | reasoning | 0.0 | `business_validation/assess_erp_match` |
| `reporting` | reasoning | 0.1 | `reporting/write_report` |
| `query_router` | reasoning | 0.0 | `rag/classify_question` |
| `rag_generation` | rag | 0.1 | `rag/generate_answer` |
| `rag_reflection` | rag | 0.0 | `rag/reflect_answer` |

### 4.5 Prompts ([`config/prompts/`](../config/prompts/))

Each prompt names its persona and a `user` template filled with Python `str.format()` (so literal JSON braces are written `{{ }}`).

| File / prompt | Inputs | JSON the model must return |
|---|---|---|
| `translation.yaml / translate` | `source_language`, `text` (first 12 000 chars) | `translated_text`, `source_language`, `confidence` (0–1), `notes` |
| `data_validation.yaml / assess_data_quality` | `invoice` (JSON), `findings` (JSON) | `summary` |
| `business_validation.yaml / assess_erp_match` | `invoice`, `erp`, `findings` | `summary`, `risk_level` (`low`/`medium`/`high`) |
| `reporting.yaml / write_report` | `invoice`, `discrepancies`, `missing_fields`, `recommendation`, `reason` | `summary`, `next_steps` (list) |
| `rag.yaml / classify_question` | `question` | `route` (`structured`/`semantic`) |
| `rag.yaml / generate_answer` | `question`, `context` | `answer` |
| `rag.yaml / reflect_answer` | `question`, `context`, `answer` | `answer_relevance`, `groundedness`, `context_relevance`, `better_query` |

The AI never decides an outcome. Validation findings and the approve/review/reject decision are plain Python; the model only writes summaries, translates, routes questions and answers them.

### 4.6 [`rag_test_questions.yaml`](../config/rag_test_questions.yaml)

A list of `{question, answer}` pairs. Copied into `audit.rag_test_questions` only when that table is empty (see [section 14](#14-ragas-evaluation)).

---

## 5. Database schema

One Postgres database with three schemas, created by `00_schemas.sql`: `langgraph`, `erp`, `audit`.

### 5.1 `erp` schema ([`10_erp_schema.sql`](../db/init/10_erp_schema.sql), data in [`11_erp_seed.sql`](../db/init/11_erp_seed.sql))

**`erp.vendors`**

| Column | Type | Notes |
|---|---|---|
| `vendor_id` | VARCHAR(32) | primary key, e.g. `VEND-001` |
| `vendor_name` | VARCHAR(255) NOT NULL | |
| `country` | VARCHAR(64) | |
| `currency` | CHAR(3) NOT NULL | |

Seed: VEND-001 Global Logistics Ltd (UK, USD), VEND-002 BlueOcean Transport Co. (USA, USD), VEND-003 Transporte Ibérico S.A. (Spain, EUR), VEND-004 HafenLogistik GmbH (Germany, EUR), VEND-005 SwiftMove Couriers (USA, USD), VEND-006 OceanFreight Pvt Ltd (India, INR).

**`erp.sku_master`**

| Column | Type | Notes |
|---|---|---|
| `item_code` | VARCHAR(64) | primary key |
| `category` | VARCHAR(64) | |
| `uom` | VARCHAR(32) | unit of measure |
| `gst_rate` | NUMERIC(6,3) NOT NULL DEFAULT 0 | tax rate in percent |

Seed: SKU-001, 002, 003, 101, 201, 202, 301, 302, 401, 402, 501 – all with `gst_rate` 10.

**`erp.purchase_orders`**

| Column | Type | Notes |
|---|---|---|
| `po_number` | VARCHAR(64) | primary key |
| `vendor_id` | VARCHAR(32) NOT NULL | FK → `erp.vendors` |

Seed: PO-1001 … PO-1006, one per vendor in the same order.

**`erp.po_line_items`** (index `idx_po_lines_po` on `po_number`)

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | primary key |
| `po_number` | VARCHAR(64) NOT NULL | FK → `erp.purchase_orders` |
| `line_number` | INTEGER NOT NULL | |
| `item_code` | VARCHAR(64) NOT NULL | |
| `description` | TEXT | |
| `qty` | NUMERIC(14,3) NOT NULL | |
| `unit_price` | NUMERIC(14,4) NOT NULL | |
| `currency` | CHAR(3) NOT NULL | |

Seed lines:

| PO | Line | Item | Description | Qty | Unit price | Cur. |
|---|---|---|---|---|---|---|
| PO-1001 | 1 | SKU-001 | Pallet Wrapping Film | 50 | 12.00 | USD |
| PO-1001 | 2 | SKU-002 | Industrial Gloves | 120 | 3.00 | USD |
| PO-1001 | 3 | SKU-003 | Safety Helmets | 30 | 15.00 | USD |
| PO-1002 | 1 | SKU-101 | Container Seals | 200 | 1.25 | USD |
| PO-1003 | 1 | SKU-201 | Cajas de carton | 100 | 2.50 | EUR |
| PO-1003 | 2 | SKU-202 | Etiquetas adhesivas | 300 | 0.30 | EUR |
| PO-1004 | 1 | SKU-301 | Transportkisten | 40 | 25.00 | EUR |
| PO-1004 | 2 | SKU-302 | Sicherheitswesten | 60 | 10.00 | EUR |
| PO-1005 | 1 | SKU-401 | Fuel Surcharge | 1 | 120.00 | USD |
| PO-1005 | 2 | SKU-402 | Express Delivery Boxes | 25 | 4.50 | USD |
| PO-1006 | 1 | SKU-501 | Container Transport | 5 | 8000.00 | INR |

### 5.2 `audit` schema ([`20_audit_schema.sql`](../db/init/20_audit_schema.sql))

**`audit.invoice_audit`** – one row per unique invoice file (by content checksum). Indexes on `vendor_name`, `invoice_number`, `invoice_date`, `recommendation`.

| Column | Type | Default | Written by | Meaning |
|---|---|---|---|---|
| `invoice_id` | UUID PK | – | `create_invoice()` (monitor) | generated with `uuid4` |
| `file_path` | VARCHAR(512) NOT NULL | – | monitor; `update_file_path()` after every move | current location of the file |
| `file_name` | VARCHAR(255) | – | monitor | original file name |
| `file_type` | VARCHAR(16) | – | monitor | `pdf`, `docx` or `image` |
| `file_checksum` | VARCHAR(64) UNIQUE | – | monitor | SHA-256 hex of the file content |
| `extraction_method` | VARCHAR(32) | – | extractor | `pdfplumber`, `docx` or `tesseract` |
| `raw_extracted_text` | TEXT | – | extractor | full text as read |
| `translated_text` | TEXT | – | translator | English text, NULL for English invoices or failed translation |
| `source_language` | VARCHAR(8) | – | translator | langdetect code, e.g. `es` |
| `translation_confidence` | NUMERIC(4,3) | – | translator | 1.0 for English, 0.0 if translation failed |
| `invoice_number` … `total_amount` | typed | – | parser, **first run only** (`save_fields()`) | `invoice_number` VARCHAR(64), `vendor_name` VARCHAR(255), `po_number` VARCHAR(64), `invoice_date` DATE, `currency` CHAR(3), `subtotal_amount` / `tax_amount` / `total_amount` NUMERIC(14,2). **Never overwritten by corrections.** |
| `vendor_id` | VARCHAR(32) | – | business validation (`save_vendor_id()`) | the ERP vendor id that was found, on every run |
| `processing_status` | VARCHAR(32) NOT NULL | `detected` | several | see [section 19](#19-status-values-and-event-types) |
| `validation_status` | VARCHAR(32) | – | reporting (`save_outcome()`) | `passed`, `partial`, `failed` |
| `recommendation` | VARCHAR(32) | – | reporting | `approve`, `review`, `reject` (the rules' recommendation; **not** changed by a human decision) |
| `error_message` | TEXT | – | `mark_failed()` | first 2000 chars of the error |
| `extracted_raw` | JSONB NOT NULL | `{}` | parser, first run | extras, currently `{"tax_rate_percent": …}` |
| `report_json` | JSONB | – | reporting; `save_decision()` merges `human_decision` | the full report of the latest run |
| `report_html_path` / `report_json_path` | VARCHAR(512) | – | report files node | paths inside the container (`/reports/…`) |
| `run_number` | INTEGER NOT NULL | 0 | reporting | +1 per validation run |
| `thread_id` | VARCHAR(128) | – | monitor / retry / re-check | LangGraph thread id: `invoice-<uuid>` or `recheck-<invoice_id>-<uuid>` |
| `indexed_at` | TIMESTAMPTZ | – | `mark_indexed()` | last time it was written to Qdrant |
| `detected_at` | TIMESTAMPTZ NOT NULL | now() | insert | |
| `processed_at` | TIMESTAMPTZ NOT NULL | now() | insert; `save_outcome()` sets now() | used for ordering lists |

**`audit.invoice_line_items`** – one row per extracted line, first run only. Index on `invoice_id`.

| Column | Type | Meaning |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `invoice_id` | UUID NOT NULL | FK → `invoice_audit` |
| `line_number` | INTEGER NOT NULL | 1, 2, 3 … in reading order |
| `item_code` | VARCHAR(64) | e.g. `SKU-001`, may be NULL |
| `description` | TEXT | |
| `quantity` | NUMERIC(12,3) | |
| `unit_price` | NUMERIC(14,4) | |
| `line_total` | NUMERIC(14,2) | |

`save_fields()` deletes all lines of the invoice and inserts them again, so a retried invoice does not get duplicate lines.

**`audit.discrepancies`** – one row per finding per validation run. Old runs are kept. Index on `invoice_id`.

| Column | Type | Meaning |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `invoice_id` | UUID NOT NULL | FK |
| `run_number` | INTEGER NOT NULL | matches `invoice_audit.run_number` of that run |
| `source` | VARCHAR(16) NOT NULL | `data` or `business` |
| `field_name` | VARCHAR(64) NOT NULL | e.g. `total_amount`, `line_2.unit_price`, `vendor`, `po_number` |
| `line_number` | INTEGER | for line findings |
| `invoice_value` | TEXT | value on the invoice |
| `erp_value` | TEXT | ERP or calculated expected value |
| `deviation_pct` | NUMERIC(14,4) | `abs(inv − exp) / abs(exp) × 100`, 4 decimals |
| `severity` | VARCHAR(16) NOT NULL | `high`, `medium`, `low` |
| `action` | VARCHAR(32) | `reject`, `manual_review`, `flag` |
| `explanation` | TEXT | human-readable sentence |
| `detected_at` | TIMESTAMPTZ | now() |

The "current" discrepancies of an invoice are always `WHERE d.run_number = invoice_audit.run_number`.

**`audit.human_feedback`** – every correction and every human decision, insert-only. Index on `invoice_id`.

| Column | Type | Meaning |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `invoice_id` | UUID NOT NULL | FK |
| `scope` | VARCHAR(16) NOT NULL DEFAULT `header` | `header` (invoice field), `line_item`, or `decision` |
| `line_number` | INTEGER | for `line_item` |
| `field_name` | VARCHAR(64) NOT NULL | header: `invoice_number`, `invoice_date`, `vendor_name`, `vendor_id`, `po_number`, `currency`, `subtotal_amount`, `tax_amount`, `total_amount`; line: `item_code`, `description`, `quantity`, `unit_price`, `line_total`; decision: `recommendation` |
| `original_value` | TEXT | for corrections: the **extracted** value from `invoice_audit` / `invoice_line_items`; for decisions: the rules' recommendation |
| `corrected_value` | TEXT | the new value, or `approve` / `reject` |
| `comment` | TEXT | note from the reviewer (decisions) |
| `corrected_at` | TIMESTAMPTZ | now() |
| `revalidated` | BOOLEAN NOT NULL DEFAULT FALSE | set to TRUE for all of the invoice's rows after a re-check |

The latest value per field is read with `SELECT DISTINCT ON (scope, line_number, field_name) … ORDER BY …, corrected_at DESC` (only scopes `header` and `line_item`).

**`audit.file_events`** – a log of everything that happened to a file.

| Column | Type | Meaning |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `file_name` | VARCHAR(255) | |
| `file_checksum` | VARCHAR(64) | may be NULL (e.g. unsupported type) |
| `event_type` | VARCHAR(32) NOT NULL | see [section 19](#19-status-values-and-event-types) |
| `detail` | TEXT | free text |
| `invoice_id` | UUID | FK, may be NULL |
| `occurred_at` | TIMESTAMPTZ | now() |

**`audit.rag_queries`** – one row per question asked through `POST /query`.

| Column | Type | Meaning |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `question` | TEXT NOT NULL | |
| `route` | VARCHAR(16) | `structured` / `semantic` |
| `answer` | TEXT | final answer shown |
| `attempts` | INTEGER NOT NULL DEFAULT 1 | |
| `answer_relevance`, `groundedness`, `context_relevance`, `avg_score` | NUMERIC(4,3) | RAG Triad of the final attempt |
| `accepted` | BOOLEAN NOT NULL DEFAULT FALSE | `avg_score > 0.7` |
| `asked_at` | TIMESTAMPTZ | now() |

**`audit.rag_test_questions`** and **`audit.ragas_results`** – created by the agent at run time (not in the init SQL), see [section 14](#14-ragas-evaluation).

| `rag_test_questions` column | Type |
|---|---|
| `id` | BIGSERIAL PK |
| `question` | TEXT NOT NULL |
| `answer` | TEXT NOT NULL (the correct answer) |
| `added_at` | TIMESTAMPTZ DEFAULT now() |

| `ragas_results` column | Type | Meaning |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `run_id` | INTEGER NOT NULL | `max(run_id) + 1` at the start of a run |
| `run_at` | TIMESTAMPTZ DEFAULT now() | |
| `question` | TEXT NOT NULL | |
| `expected_answer` | TEXT | copy of the test answer at run time |
| `rag_answer` | TEXT | what the RAG answered |
| `route` | VARCHAR(16) | |
| `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall` | NUMERIC(4,3) | NULL if the metric failed |

### 5.3 `langgraph` schema

Created by `PostgresSaver.setup()`: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`. They hold the serialised state of every invoice graph run, keyed by `thread_id`. This is what lets a paused invoice resume after days or restarts. They are working memory, not a business record.

---

## 6. Redis keys

All keys are written with `SET … EX 86400`. Values are JSON. If Redis is down every read returns "not found" and every write is skipped with a warning – nothing breaks.

| Key | Value | Written | Read |
|---|---|---|---|
| `checksum:<file sha256>` | invoice_id (string) | `reporting_node()` after every validation run | `monitor_node()` – fast duplicate check |
| `translation:<sha256 of language + "\n" + text>` | `{"translated_text", "confidence"}` | translator, only if confidence ≥ 0.95 | translator |
| `llm:<sha256 of model + "\n" + system + "\n" + user prompt>` | the parsed JSON answer | `llm.ask()` when `use_cache=True` | `llm.ask()` |

Translation calls use `use_cache=False` on `llm.ask()` because the translator has its own key with the confidence rule. Because the `llm:` key contains the complete prompt text, editing a prompt automatically stops old cached answers from being used.

---

## 7. Qdrant collection

| Property | Value |
|---|---|
| Name | `invoice_chunks` |
| Distance | cosine |
| Vector size | taken from the first embedding (1024 for `cohere.embed-english-v3`); collection created on first index |
| Point id | `uuid5(NAMESPACE_URL, "<invoice_id>-<chunk number>")` – stable, so re-indexing overwrites |
| Payload | `invoice_id` (string), `invoice_number`, `vendor`, `language`, `source_type` (`invoice` or `report`), `chunk_text` |

**What is indexed** (`index_invoice()`):

1. The invoice text: `translated_text` if present, otherwise `raw_extracted_text`.
2. If `report_json` exists, a text version of the report (`report_text()`): a header line with invoice number and vendor, recommendation, status and reason, total/currency/PO/date (from the `invoice_audit` columns), the report summary, the auditor decision if any, missing fields, and one line per discrepancy.

Both are cut by `split_text()` into chunks of at most ~800 characters, splitting on line breaks. Before inserting, all existing points with the same `invoice_id` are deleted. Then `invoice_audit.indexed_at = now()`.

**When indexing happens:** only in the `index` node, the last node of the workflow, i.e. after an invoice ends **approved or rejected**. Invoices waiting in `pending_review` are not indexed. `rebuild_index()` (start-up when Qdrant is empty, or `POST /reindex`) indexes every invoice with text and status `approved` or `rejected`.

---

## 8. Files on disk

Inside the agent (host paths are under the project root):

| Folder | Holds |
|---|---|
| `/data/incoming` | new files waiting to be processed |
| `/data/pending` | files of invoices in `pending_review` |
| `/data/processed` | approved invoices and duplicates |
| `/data/rejected` | rejected invoices |
| `/data/failed` | unsupported, unreadable or failed files |
| `/reports` | `<invoice_number or "invoice">_<first 8 chars of invoice_id>.json` and `.html` |

When a file is moved and the target name already exists, it becomes `<stem>_<unix time><suffix>`. After every move the new path is saved in `invoice_audit.file_path`. The file itself is never stored in the database.

---

## 9. How the AI models are called

All in [`llm.py`](../agent/app/llm.py).

`ask(prompt_file, prompt_name, values, use_cache=True)`:

1. `config.load_prompt()` loads the prompt and its persona → `(system, template, temperature, role)`.
2. `user = template.format(**values)`; model = `MODELS[role]` with the `bedrock/` prefix removed.
3. If `use_cache`, looks up `llm:<sha256>` in Redis; a hit returns immediately (log: `LLM cache hit for …`).
4. Calls `ChatBedrock(model_id, region_name, temperature, beta_use_converse_api=True).invoke([SystemMessage, HumanMessage])`. One `ChatBedrock` object is kept per `(model, temperature)`.
5. If the content is a list of blocks, the `text` of each block is joined.
6. `parse_json()` strips markdown fences, takes the text from the first `{` to the last `}` and `json.loads` it.
7. On any exception, or if no JSON is found, it logs a warning and returns `None`. Every caller has a plain fallback, so the workflow never stops because of the model.
8. Successful answers are cached.

`embed(texts)` uses one `BedrockEmbeddings(model_id=EMBEDDING_MODEL)` and `embed_documents()`.

---

## 10. The invoice workflow, node by node

Defined in [`workflow.py`](../agent/app/workflow.py). A LangGraph `StateGraph(InvoiceState)` compiled with `PostgresSaver`.

```
START ─(revalidation?)─► parse
  │
  └─► monitor ─stop?─► END
         └─► extract ─stop?─► END
                └─► translate ─► parse ─► data_validation ─► business_validation ─stop?─► END
                                  ▲                                │
                                  │                            reporting
                                  │                   review ┌─────┴─────┐ approve/reject
                                  │                          ▼           ▼
                                  └── recheck ──── human_review     report_files ─► index ─► END
                                                    (interrupt)          ▲
                                                         └─ approve/reject ─► decision
```

Routing functions: `where_to_start` (`parse` if `revalidation`, else `monitor`), `go_on_or_stop` (`stop` if `state["stop"]` is set), `after_reporting` (`human_review` if recommendation is `review`, else `report_files`), `after_human_review` (`parse` for `recheck`, else `decision`).

### State keys (`InvoiceState`)

`file_path`, `file_name`, `thread_id`, `invoice_id`, `checksum`, `file_type`, `stop`, `raw_text`, `extraction_method`, `language`, `translated_text`, `translation_confidence`, `revalidation`, `invoice` (parsed dict), `applied_corrections`, `data_findings`, `missing_fields`, `data_summary`, `business_findings`, `business_summary`, `risk_level`, `erp`, `validation_status`, `recommendation`, `report`, `human_checked`, `human_action`, `review_comment`, `chunk_count`.

A **finding** (discrepancy) is always this dict (`rules.finding()`): `source`, `field_name`, `line_number`, `invoice_value` (str), `erp_value` (str), `deviation_pct`, `action`, `severity`, `explanation`.

### 10.1 `monitor` – [`agents/monitor.py`](../agent/app/agents/monitor.py)

| Step | Detail | DB / cache effect |
|---|---|---|
| File type | `.pdf`→pdf, `.docx`→docx, `.png .jpg .jpeg .tif .tiff`→image. Anything else: stop `unsupported file type`. | `file_events` row `failed` ("Unsupported file type .xyz") |
| Checksum | SHA-256 of the content, read in 1 MB blocks | `file_events` row `detected`, detail `type=<type>` |
| Redis duplicate | `checksum:<sha>` exists → stop `duplicate` | `file_events` `duplicate` ("Same content was already audited (Redis)") |
| Postgres lookup | `SELECT * FROM audit.invoice_audit WHERE file_checksum = …` | – |
| Retry | row exists **and** status `failed` → reuse it | `file_path` and `thread_id` updated |
| Duplicate | row exists, any other status → stop `duplicate` | `file_events` `duplicate` ("Same content as <first file name>") |
| New | insert row (`create_invoice`) | new `invoice_audit` row: id, path, name, type, checksum, thread_id, status `detected` |

Returns `invoice_id`, `checksum`, `file_type` (or `stop`).

### 10.2 `extract` – [`agents/extractor.py`](../agent/app/agents/extractor.py)

| Type | How |
|---|---|
| PDF | `pdfplumber` text of every page joined by newlines. If < 50 characters → scanned: `pdf2image.convert_from_path(dpi=300)` and `pytesseract.image_to_string(lang="eng+spa+deu+fra")` per page. Method `pdfplumber` or `tesseract`. |
| DOCX | `python-docx`: non-empty paragraphs, then every table row as `cell | cell | cell`. Method `docx`. |
| Image | Pillow; converted to RGB unless L/RGB; Tesseract. Method `tesseract`. |

On exception: `mark_failed("Extraction failed: …")`, `file_events` `failed`, stop `extraction failed`. Empty text: `mark_failed("No text could be read from the file")`, event `failed` ("No text found"), stop `no text found`.

Success: `save_raw_text()` → `extraction_method`, `raw_extracted_text`, `processing_status = 'extracted'`.

### 10.3 `translate` – [`agents/translator.py`](../agent/app/agents/translator.py)

1. `langdetect.detect(text[:3000])` with `DetectorFactory.seed = 0` (deterministic). Exception → `en`.
2. English: `save_translation(lang="en", translated_text=NULL, confidence=1.0)`.
3. Otherwise Redis `translation:<sha>`; on a hit uses it.
4. Otherwise `llm.ask("translation", "translate", {source_language, text[:12000]}, use_cache=False)`. Confidence is clamped to 0–1 and rounded to 3 decimals. No answer → `translated=None`, `confidence=0.0`.
5. Cached in Redis only if `translated` and `confidence ≥ auto_approve_confidence_threshold` (0.95); otherwise a warning is logged.
6. `save_translation()` → `source_language`, `translated_text`, `translation_confidence`.

Low confidence does not stop the workflow; `decide()` turns it into a `review` later.

### 10.4 `parse` – [`agents/parser.py`](../agent/app/agents/parser.py)

`parse_invoice(raw_text, translated_text, symbol_map)` parses the original text first, then parses the English translation and fills any field that is still empty.

**Label matching** (`label_values()`): for each line that has no `|`, the line is folded (lower case, accents removed) and compared to the labels below; the label must be followed by a non-alphanumeric character. The value is the text after `:` if there is one, otherwise the text after the label.

| Field | Labels (en / es / de / fr) |
|---|---|
| invoice_number | invoice number, invoice no, invoice nr, invoice #, factura no, factura n, numero de factura, rechnungsnummer, rechnung nr, numero de facture, facture no |
| invoice_date | invoice date, date, fecha, rechnungsdatum, datum, date de facture |
| po_number | po reference, po number, po no, po, purchase order, referencia de pedido, orden de compra, pedido, bestellnummer, bestellung, bon de commande |
| vendor_id | vendor id, vendor code, supplier id, lieferantennummer, codigo proveedor |
| vendor_name | vendor, supplier, proveedor, lieferant, fournisseur |
| currency | currency, moneda, wahrung, devise |
| subtotal_amount | subtotal, sub total, sub-total, zwischensumme, base imponible, sous-total, net amount |
| tax_amount | tax, vat, mwst, ust, iva, tva, impuesto, mehrwertsteuer |
| total_amount | total amount due, total amount, total due, grand total, total, amount due, gesamtbetrag, importe total, montant total |
| description / quantity / unit_price | used for single-item invoices |

**Field rules:**

| Field | Rule |
|---|---|
| invoice_number, po_number, vendor_id | first word of the first labelled value |
| po_number fallback | regex `\bPO-?\d+\b` anywhere in the text |
| vendor_name | labelled value (ignoring "vendor id" lines); otherwise the first of the first 5 lines that has 3+ letters and does not start with invoice/factura/rechnung/facture |
| currency | `Currency:` label (first 3 letters, upper case); else first of USD EUR GBP INR JPY CHF CAD AUD CNY SGD as a word; else a symbol from `currency_symbol_map` |
| amounts | `to_decimal()` takes the last number in the value; handles `1,470.00`, `1.470,00`, `1470,5` |
| invoice_date | `to_date()`: `YYYY-MM-DD`; `DD.MM.YYYY` / `DD/MM/YYYY` (swaps if month > 12); `14 March 2025` / `10 de abril de 2025` (month names in 4 languages); `March 14, 2025` |
| tax rate | the `NN %` on a tax line → `extras.tax_rate_percent` |

**Line items** (`find_line_items()`), three strategies in order, the first that finds anything wins:

1. **Table rows** – lines containing `|` with ≥ 4 cells whose last 3 cells are numbers. The first text cell becomes `item_code` if it matches `[A-Z]{2,5}-?\d{2,6}`; the rest is the description. Last three cells: quantity, unit price, line total.
2. **OCR rows** – `^(text) (num) (num) (num)$`, accepted only if `qty × price` is within 0.05 of the total. An item code inside the description is pulled out.
3. **Single item** – `Quantity:` and `Unit price:` labels; total = qty × price rounded to cents.

Lines are numbered 1, 2, 3 …

**First run vs re-check:**

| Run | What happens |
|---|---|
| First run (`revalidation` not set) | `save_fields()` writes the header columns and `extracted_raw` of `invoice_audit`, deletes and re-inserts `invoice_line_items`. |
| Re-check (`revalidation = True`) | Nothing is written. `latest_corrections()` is applied on top of the freshly parsed values (`apply_corrections()`): amounts and quantities converted with `to_decimal()`, dates with `to_date()`; unreadable values are skipped with a warning. `line_item` corrections are matched by `line_number`. The list of applied corrections is put in the state and later into the report. |

### 10.5 `data_validation` – [`agents/data_validation.py`](../agent/app/agents/data_validation.py)

All checks are plain Python. Money comparisons allow 0.01.

| Check | Finding `field_name` | Policy |
|---|---|---|
| Required header fields (`invoice_no`→invoice_number, `invoice_date`, `vendor_id` (a vendor **name** also counts), `currency`, `total_amount`) | the rule name, e.g. `invoice_no` | missing_field_action |
| No line items at all | `line_items` | missing_field_action |
| Each line: `item_code`, `description`, `qty`→quantity, `unit_price`, `total`→line_total | `line_<n>.<rule>` | missing_field_action |
| Currency not in accepted list | `currency` | invalid_currency_action |
| qty × unit price ≠ line total | `line_<n>.total` | total_mismatch_action |
| Sum of line totals ≠ subtotal | `subtotal` | total_mismatch_action |
| (subtotal or line sum) + tax ≠ total | `total_amount` | total_mismatch_action |
| subtotal × printed tax rate ≠ tax | `tax_amount` | tax_mismatch_action |
| Invoice date in the future | `invoice_date` | inconsistent_field_action |
| Negative subtotal / tax / total | that field | inconsistent_field_action |
| Line quantity ≤ 0 | `line_<n>.qty` | inconsistent_field_action |

Missing field names are also collected in `missing_fields`. The model then writes `data_summary` (`assess_data_quality`); fallback "All required fields are present and the totals add up." or "N data problem(s) found.". No database write in this node.

### 10.6 `business_validation` – [`agents/business_validation.py`](../agent/app/agents/business_validation.py)

All ERP data comes through HTTP calls to mock-erp ([`erp_client.py`](../agent/app/erp_client.py), 15 s timeout). 404 → `None`; ≥ 500 or connection error → `ErpUnavailable`.

1. **Vendor:** `GET /vendor/{vendor_id}` if the invoice has a vendor id, else `GET /vendors?name=<vendor_name>` (first match), else none. Not found → finding `vendor` (unknown_vendor_action = reject).
2. **PO:** `GET /po/{po_number}`. Not found (or no PO number) → finding `po_number` (po_not_found_action = manual_review) and **the rest is skipped**.
3. **Vendor vs PO:** vendor found but `vendor_id ≠ po.vendor_id` → finding `vendor_id` (reject).
4. **Currency:** invoice currency ≠ PO currency → finding `currency` (invalid_currency_action = reject).
5. **Each invoice line:** matched to a PO line by item code (case-insensitive), else by description (folded, equal or one contains the other). No match → finding `line_<n>.item` (manual_review) and the tax check is disabled.
   - Quantity deviation % > `quantity_difference_percent` (0) → `line_<n>.qty`.
   - Unit price deviation % > `price_difference_percent` (5) → `line_<n>.unit_price`.
   - `GET /sku/{item_code}` of the PO line; expected tax += line total × `gst_rate` / 100.
6. **Tax:** only if every line matched and every SKU was found: deviation of the stated tax from the expected tax > `tax_difference_percent` (2) → `tax_amount`.

Then: if a vendor was found, `save_vendor_id()` writes `invoice_audit.vendor_id`. Risk level default: `low` (no findings), `high` (any reject), else `medium`; the model (`assess_erp_match`) can replace the summary and risk level.

If mock-erp is unreachable: `mark_failed(<error>)`, `file_events` `failed`, stop `erp unavailable` → the watcher moves the file to `failed/`.

### 10.7 `reporting` – [`agents/reporting.py`](../agent/app/agents/reporting.py) and [`rules.py`](../agent/app/rules.py)

`decide(discrepancies, translation_confidence, rules, human_checked)`:

| Condition (checked in order) | validation_status | recommendation | reason text |
|---|---|---|---|
| any finding with action `reject` | `failed` | `reject` | at least one finding has the action 'reject' |
| any other finding | `partial` | `review` | N finding(s) need a human to check them |
| not human-checked and confidence < 0.95 | `passed` | `review` | translation confidence X is below 0.95 |
| otherwise | `passed` | `approve` | no discrepancies and translation confidence is high enough |

Then the model writes `summary` and `next_steps` (`write_report`). The report dict (`report_json`):

`invoice_id`, `file_name`, `generated_at` (UTC ISO, seconds), `run_number`, `invoice_number`, `vendor_name`, `vendor_id` (from the ERP vendor), `po_number`, `invoice_date`, `currency`, `subtotal_amount`, `tax_amount`, `total_amount`, `line_items`, `extraction_method`, `missing_fields`, `discrepancies` (data + business findings), `validation_status`, `recommendation`, `reason`, `risk_level`, `data_summary`, `business_summary`, `summary`, `next_steps`, `applied_corrections`, and – depending on `rules.yaml` – `translation: {source_language, confidence}` and `discrepancy_summary: {total, by_severity}`. After a human decision `human_decision` is merged in.

Writes:

| Target | Change |
|---|---|
| `audit.discrepancies` | one row per finding with the new `run_number` |
| `audit.invoice_audit` | `validation_status`, `recommendation`, `processing_status` (`approve`→`approved`, `review`→`pending_review`, `reject`→`rejected`), `report_json`, `run_number`, `processed_at = now()` |
| Redis | `checksum:<sha>` = invoice_id |
| `audit.file_events` | `pending_review` or `completed`, detail `<recommendation>: <reason>` |

### 10.8 `human_review` and `decision`

See [section 12](#12-human-review-re-check-and-decisions).

### 10.9 `report_files`

Reads `report_json` from the database (so a `human_decision` is included), renders [`templates/report.html`](../agent/app/templates/report.html) with Jinja2 (autoescape on), writes `<name>_<id8>.json` and `.html` to `/reports`, and saves both paths in `invoice_audit`. A write error is only logged.

The HTML report shows: a coloured recommendation badge, validation status and reason, the auditor decision, summary and next steps, an invoice table (number, date, vendor + vendor id or "not in ERP", PO, subtotal, tax, total, extraction method, language and confidence), line items, discrepancies (severity, field, invoice value, ERP/expected, deviation %, explanation), missing fields, and "Human corrections used in this run".

### 10.10 `index`

`indexing_node()` → `index_invoice()` ([section 7](#7-qdrant-collection)). Any error is logged and ignored (`chunk_count = 0`); the invoice outcome is not affected.

---

## 11. The folder watcher

[`watcher.py`](../agent/app/watcher.py). A daemon thread named `watcher` runs `scan_once()` every 5 seconds until shutdown.

`scan_once()` lists `/data/incoming` (sorted; skips directories, hidden files and `.json` files) and skips files modified less than 2 seconds ago. For each file it calls `process_one()`.

`process_one(path)` runs under a global lock (the same lock `respond()` uses, so a review request waits while a file is being processed and the other way round):

| Situation | Result returned | File moved to |
|---|---|---|
| File vanished | `{"file", "status": "gone"}` | – |
| Exception in the workflow | marks the invoice `failed` (if it has a row), adds `file_events` `failed` | `failed/` |
| Stop `duplicate` | `{"file", "status": "duplicate", "invoice_id"}` | `processed/` |
| Stopped without an invoice id (unsupported type) | `{"file", "status": "failed", "reason"}` | `failed/` |
| Otherwise | `{"file", "invoice_id", "status": <processing_status>}` | folder for the status: approved→`processed/`, pending_review→`pending/`, rejected→`rejected/`, failed→`failed/` |

Each run gets a new LangGraph thread id `invoice-<uuid4>`.

---

## 12. Human review, re-check and decisions

### 12.1 The pause

`human_review_node()` calls `interrupt({"invoice_id", "reason"})`. The graph stops; its state is in the checkpoint tables under the invoice's `thread_id`. `workflow.is_waiting_for_human(thread_id)` checks whether `human_review` is in `get_state(...).next`.

### 12.2 `watcher.respond(invoice_id, action, comment)` (behind `POST /review`)

1. Takes the lock and loads the invoice. Not found → `LookupError` (HTTP 404). No raw text → `ValueError` (HTTP 409).
2. **Paused invoice:** `workflow.resume(thread_id, action, comment)` = `invoice_graph.invoke(Command(resume={"action", "comment"}))`. The node returns `human_action`, `review_comment`, `revalidation = True`, `human_checked = True`.
3. **Finished invoice + `recheck`:** `workflow.recheck_finished(row)` starts a **new** thread `recheck-<invoice_id>-<uuid>` (saved in `invoice_audit.thread_id`) with the stored text, language, confidence, `revalidation = True`, `human_checked = True`. It starts directly at `parse`.
4. **Finished invoice + approve/reject:** `ValueError("This invoice is not waiting for a review")` (HTTP 409).
5. If the result has `stop` → `RuntimeError` (HTTP 503).
6. For `recheck`: `UPDATE audit.human_feedback SET revalidated = TRUE WHERE invoice_id = … AND revalidated = FALSE`, and a `file_events` row `rechecked` (detail = comment).
7. Moves the file to the folder of the new status.
8. Returns `{"status", "recommendation", "reason", "discrepancies": <count>}`.

### 12.3 What each action does to the data

| Action | Graph path | Rows / columns changed |
|---|---|---|
| **Re-check** | `parse` (corrections applied in memory) → `data_validation` → `business_validation` → `reporting` → `human_review` again or `report_files` → `index` | new `discrepancies` rows (`run_number + 1`); `invoice_audit`: `run_number`, `validation_status`, `recommendation`, `processing_status`, `report_json` (with corrected values and `applied_corrections`), `processed_at`, `vendor_id`; `human_feedback.revalidated = TRUE`; `file_events` `pending_review`/`completed` + `rechecked`; Redis `checksum:`; report files + Qdrant if it ends approved/rejected. **Not changed:** the extracted header columns and `invoice_line_items`. |
| **Approve** | `decision` → `report_files` → `index` | `human_feedback` insert (scope `decision`, field `recommendation`, original = rules' recommendation, corrected `approve`, comment); `invoice_audit.processing_status = 'approved'`, `report_json` gets `human_decision: "approve"`; `file_events` `approved`; report files; Qdrant. `recommendation` stays `review`. |
| **Reject** | same as approve | same with `rejected` / `reject`. |

Because the paused graph resumes with `human_checked = True`, a low translation confidence on its own does not send the invoice back to review after a human has looked at it.

---

## 13. The RAG question-answering graph

[`rag_runtime.py`](../agent/app/rag_runtime.py). A second, separate `StateGraph(RagState)` compiled **without** a checkpointer (no memory between questions).

```
START ─► router ─┬─ structured ─────────────────────────────► reflect ─► END
                 │                                               ▲   │
                 └─ semantic ─► retrieve ─► augment ─► generate ─┘   │ not accepted and
                                   ▲                                 │ attempt < 3 and
                                   └─────────────────────────────────┘ route is semantic
```

State: `question`, `invoice_id`, `route`, `query`, `attempt`, `chunks`, `context`, `answer`, `sources`, `scores`, `accepted`.

| Node | Detail |
|---|---|
| `router` | `llm.ask("rag", "classify_question")`. If the model returns `structured` or `semantic` that is used; otherwise keyword fallback: `structured` if the lower-case question contains any of `how many`, `count`, `sum of`, `which invoices`, `number of`, `all invoices`, `statistics`. `invoice_id` = the one given, else the first invoice whose `invoice_number` appears in the question. Sets `query = question`, `attempt = 0`. |
| `structured` | `invoice_summary_rows(invoice_id)`: for each invoice (or the chosen one) `invoice_number, file_name, vendor_name, vendor_id, po_number, invoice_date, currency, subtotal_amount, tax_amount, total_amount, processing_status, recommendation, source_language, translation_confidence` and a JSON list of the current run's discrepancies (`field, invoice_value, erp_value, deviation_pct, severity, explanation`), ordered by `processed_at`. Rows are JSON lines = the context. The model answers with `generate_answer`; fallback "Invoices in the audit trail:" + the table. Sets `sources = ["audit.invoice_audit"]`, `attempt = 1`. |
| `retrieve` | `attempt += 1`; `limit = RAG_TOP_K × 2 × attempt` (12, 24, 36). Embeds `query`, `query_points` in Qdrant with a filter on `invoice_id` if set. Errors → no chunks. |
| `augment` | Re-ranks: `score = vector score + 0.3 × (share of question words, 3+ chars, found in the chunk) + 0.1` if the question contains why/flag/reject/approve/discrepan/review and the chunk is a report chunk. Keeps the top 6. Context = `[n] invoice <number> (<source_type>):\n<text>` joined by blank lines, or `(nothing found)`. |
| `generate` | `generate_answer`. Fallbacks: "The model is not available. The most relevant passage is: …" or "No processed invoice contains information about this.". `sources` = sorted invoice numbers (or ids) of the chunks. |
| `reflect` | `reflect_answer` → the three scores (0 if missing/unreadable), `average` rounded to 3 decimals, `accepted = average > 0.7`. If not accepted and the model gave `better_query`, `query` is replaced for the next retrieval. |
| after `reflect` | `done` if accepted, or route is `structured`, or `attempt ≥ 3`; else back to `retrieve`. |

`answer_question(question, invoice_id)` invokes the graph, inserts a row into `audit.rag_queries`, and returns `answer, sources, route, attempts, scores, accepted`.

**Reflection prompt rules** (`reflect_answer`): answer relevance = how much of the question is really answered, 0.0 if the answer says the information is not available; groundedness = how many facts are in the context, cannot be 1.0 if one fact is wrong or missing; context relevance = how many needed facts are in the context; 1.0 only when nothing is missing or wrong; half right = 0.5.

---

## 14. RAGAS evaluation

[`ragas_eval.py`](../agent/app/ragas_eval.py). ragas 0.2.15.

### 14.1 Tables and starter data

`setup()` runs once per process (flag `tables_ready`): `CREATE TABLE IF NOT EXISTS` for `audit.rag_test_questions` and `audit.ragas_results`; if `rag_test_questions` is empty, inserts every `{question, answer}` from `config/rag_test_questions.yaml`. It is called by every read/delete function, so the tables exist the moment the Evaluate tab is opened. Existing databases need no reset.

### 14.2 Running an evaluation

`POST /ragas/evaluate` → `evaluate_all()`:

1. Starts `python -m app.ragas_eval` with `subprocess.run(stdout=PIPE, timeout=1800)`. Reason: importing ragas calls `nest_asyncio.apply()`, which cannot patch uvicorn's uvloop event loop (`ValueError: Can't patch loop of type uvloop.Loop`). In a plain Python process it works, and the running agent is never patched.
2. The child process configures logging to stderr (so its logs appear in `docker compose logs agent`), opens the pools, runs `run_evaluation()`, prints the `run_id` on stdout, closes the pools.
3. The parent reads the last stdout line as the run id. Non-zero exit → `RuntimeError` → HTTP 503 "The RAGAS evaluation failed, see the agent logs".

`run_evaluation()`:

1. `run_id = coalesce(max(run_id), 0) + 1`.
2. For every test question (ordered by id):
   - `rag.rag_graph.invoke({"question"})` directly – **not** `answer_question()`, so nothing is written to `audit.rag_queries`. An exception becomes the answer `(the RAG failed: …)`.
   - Contexts = the `chunk_text` of the final chunks (semantic), or the context text (structured table), or `(nothing found)`.
   - `score_answer()` builds a `SingleTurnSample(user_input, response, retrieved_contexts, reference)` and calls `metric.single_turn_score()` for each metric. NaN or an exception → NULL for that metric (logged as `RAGAS <metric> failed for …`).
   - Inserts one `audit.ragas_results` row.

### 14.3 Metrics

| Column | ragas class | What it measures | Needs |
|---|---|---|---|
| `faithfulness` | `Faithfulness` | claims in the answer supported by the context ÷ all claims | judge LLM |
| `answer_relevancy` | `ResponseRelevancy` | the judge generates questions from the answer; mean cosine similarity to the real question | judge LLM + embeddings |
| `context_precision` | `LLMContextPrecisionWithReference` | whether the chunks useful for the reference answer are ranked at the top | judge LLM, reference |
| `context_recall` | `LLMContextRecall` | claims of the reference answer found in the context ÷ all claims | judge LLM, reference |

Judge: `LangchainLLMWrapper(ChatBedrock(REASONING_MODEL, temperature 0.0, converse API))` – a separate object from the one in `llm.py`, because ragas changes model settings while scoring. Embeddings: `LangchainEmbeddingsWrapper(BedrockEmbeddings(EMBEDDING_MODEL))`. Telemetry is off (`RAGAS_DO_NOT_TRACK=true`). The metrics are created once per process.

Typical time: about 1–2 minutes for 8 questions.

### 14.4 Reading results

- `list_runs()`: per `run_id` – `min(run_at)`, `count(*)` as `questions`, and the average of each metric rounded to 3 decimals (NULLs ignored), newest first.
- `run_results(run_id)`: `question, expected_answer, rag_answer, route` and the four metrics, in insert order.

---

## 15. Agent API – every endpoint

Base URL inside Docker `http://agent:8000`, from the host `http://localhost:8000`. Interactive docs at `/docs`.

### `GET /health`
Response 200 (or 503 if Postgres is down):
```json
{"status": "healthy", "checks": {"postgres": true, "mock_erp": true, "qdrant": true, "redis": true}, "indexed_chunks": 42}
```
Only Postgres decides healthy/unhealthy.

### `POST /process`
No body. Runs `watcher.scan_once()` synchronously. Response:
```json
{"results": [{"file": "INV_EN_001.pdf", "invoice_id": "…", "status": "approved"}]}
```
Files modified in the last 2 seconds are skipped.

### `POST /feedback`
Request:
```json
{"invoice_id": "uuid", "field_name": "total_amount", "corrected_value": "1617.00",
 "original_value": "1671.00", "scope": "header", "line_number": null, "comment": null}
```
`scope` defaults to `header`. Unknown invoice → 404. Inserts one `audit.human_feedback` row. Response `{"saved": true}`. Nothing else changes until a re-check.

### `POST /review`
Request: `{"invoice_id": "uuid", "action": "recheck" | "approve" | "reject", "comment": "optional"}`.
Errors: bad action 400; unknown invoice 404; no text / not waiting 409; workflow stopped (e.g. ERP down) 503.
Response:
```json
{"status": "approved", "recommendation": "approve", "reason": "no discrepancies and …", "discrepancies": 0}
```
For approve/reject `recommendation` is the human decision and `reason`/`discrepancies` come from the last report. Effects: [section 12.3](#123-what-each-action-does-to-the-data).

### `GET /invoices/{invoice_id}/report.html`
Renders the HTML report from `report_json` (not from the file). 404 "Invoice not found" or "No report yet".

### `POST /query`
Request `{"question": "…", "invoice_id": null}`. Response:
```json
{"answer": "…", "sources": ["INV-1001"], "route": "semantic", "attempts": 1,
 "scores": {"answer_relevance": 1.0, "groundedness": 1.0, "context_relevance": 1.0, "average": 1.0},
 "accepted": true}
```
Inserts one `audit.rag_queries` row.

### `GET /ragas/questions`
Response: `[{"id": 1, "question": "…", "answer": "…"}, …]`. Creates the tables and loads the starter questions if needed.

### `POST /ragas/questions`
Request `{"question": "…", "answer": "…"}`. Either empty after trimming → 400 "Both the question and the correct answer are needed". Inserts a `rag_test_questions` row. Response `{"saved": true}`.

### `DELETE /ragas/questions/{question_id}`
Deletes the row (no error if it does not exist). Response `{"deleted": true}`. Old results in `ragas_results` are kept.

### `POST /ragas/evaluate`
No body. 400 "Add a test question first" if the set is empty; 503 if the evaluation process failed. Response `{"run_id": 3}`. Can take minutes.

### `GET /ragas/runs`
Response: `[{"run_id", "run_at", "questions", "faithfulness", "answer_relevancy", "context_precision", "context_recall"}, …]`, newest first.

### `GET /ragas/runs/{run_id}`
Response: `[{"question", "expected_answer", "rag_answer", "route", "faithfulness", "answer_relevancy", "context_precision", "context_recall"}, …]`.

### `POST /reindex`
Rebuilds Qdrant from Postgres (approved/rejected invoices). Response `{"chunks_written": 57}`.

---

## 16. Mock ERP API – every endpoint

[`mock_erp/app/main.py`](../mock_erp/app/main.py). Read-only; host port 8001. All responses go through `reply()` (JSON with `default=str`, so NUMERIC values become strings).

| Endpoint | Response | Errors |
|---|---|---|
| `GET /health` | `{"status": "healthy"}` | 503 `{"status": "unhealthy", "detail"}` |
| `GET /vendor/{vendor_id}` | `{"vendor_id", "vendor_name", "country", "currency"}` | 404 |
| `GET /vendors?name=…` | list of vendors where `vendor_name ILIKE %name%`, ordered by id | – (empty list) |
| `GET /po/{po_number}` | `{"po_number", "vendor_id", "line_items": [{"line_number", "item_code", "description", "qty", "unit_price", "currency"}], "currency"}` – `currency` is the single currency of all lines, or `null` if they differ | 404 |
| `GET /sku/{item_code}` | `{"item_code", "category", "uom", "gst_rate"}` | 404 |

---

## 17. The dashboard – every screen and button

[`streamlit_ui/app/main.py`](../streamlit_ui/app/main.py). Streamlit re-runs the whole script on every interaction.

Two ways it talks to the backend:

- **Reads** – direct SQL with a new psycopg connection per query (`query()`), `dict_row`.
- **Writes** – `call_agent(path, body, timeout=300)` = `POST AGENT_URL+path` with JSON; `agent_request(method, path, timeout=60)` for GET/DELETE (Evaluate tab). On a connection error or status ≥ 400 they show `st.error(...)` with the `detail` and return `None`.

Theme ([`.streamlit/config.toml`](../streamlit_ui/.streamlit/config.toml)): Streamlit's default light theme, minimal toolbar, usage stats off. Status text is coloured (approved green, rejected red, needs review amber, failed/in progress grey).

### 17.1 On every page load

| Element | What happens |
|---|---|
| Invoice list | `SELECT invoice_id, invoice_number, vendor_name, po_number, currency, total_amount, processing_status, file_name FROM audit.invoice_audit ORDER BY processed_at`. If Postgres is not reachable: error and stop. |
| Auto refresh (`watch_for_changes`, fragment every 5 s) | `SELECT count(*), max(processed_at)` of `invoice_audit` and `max(occurred_at)` of `file_events`. If the combination changed since the last check, the page reruns. |
| Header | title "Invoice Auditor" and four `st.metric` tiles: To review, Approved, Rejected, Failed (counts of `pending_review`, `approved`, `rejected`, `failed` from the list). |
| View selector | Pending review · Invoices · Ask · Evaluate · History (default Pending review). |

### 17.2 Adding invoices

The dashboard has no upload. Invoices are added by copying files into `data/incoming/`; the watcher picks them up within about 5 seconds. (`POST /process` on the agent processes the folder immediately; it is not used by the dashboard.)

### 17.3 Pending review

Lists invoices with `processing_status = 'pending_review'`; "Nothing to review." if none. Each is an expander titled `<invoice number or file name> | <vendor> | <total> <currency>`. Opening it loads `SELECT *` of the invoice and shows the **review card**:

| Element | Source / request | Effect |
|---|---|---|
| "Why it stopped" | `report_json.reason` | – |
| Problem list | `SELECT severity, field_name, explanation FROM audit.discrepancies … AND d.run_number = a.run_number` | – |
| **Fix values** – invoice fields table | 9 rows: invoice_number, invoice_date, vendor_name, vendor_id, po_number, currency, subtotal_amount, tax_amount, total_amount. Value = latest correction (`human_feedback`, scopes header/line_item, `DISTINCT ON`) or else the `invoice_audit` column. "Field" column is locked. | – |
| Line items table | from `audit.invoice_line_items`, with corrections on top; columns item_code, description, quantity, unit_price, line_total; "line" locked | – |
| **Save changes** | For every cell whose trimmed value differs from what was shown: `POST /feedback` `{invoice_id, scope: "header"|"line_item", line_number, field_name, corrected_value, original_value}` where `original_value` is the **extracted** value (not the previous correction) | one `human_feedback` row per changed cell. "Nothing was changed." if none. Toast "Saved N correction(s). Press Re-check to validate again.", rerun. |
| **Re-check** | `POST /review {action: "recheck", comment: null}` | [section 12.3](#123-what-each-action-does-to-the-data). Toast "Still needs review: <reason>" if it is still pending, else "Invoice <status>". Rerun. |
| **Approve** | `POST /review {action: "approve", comment: null}` | status → approved, file → `processed/`, report files, Qdrant |
| **Reject** | `POST /review {action: "reject", comment: null}` | status → rejected, file → `rejected/`, report files, Qdrant |

### 17.4 Invoices

| Element | Request | Effect |
|---|---|---|
| Search box | `… WHERE invoice_number ILIKE %s OR vendor_name ILIKE %s OR po_number ILIKE %s` | filters the list |
| List row | – | title, vendor, PO, total, status (newest first) |
| **Open** | – | `session_state.open_invoice = invoice_id`; the invoice sheet appears below |

Invoice sheet: title, status, vendor, invoice date, language, `error_message` (red) if set. Four tabs:

| Tab | Request | Shows / does |
|---|---|---|
| **Report** | `GET /invoices/{id}/report.html` (timeout 20 s) | the HTML report in an 800 px frame, or "No report available right now." |
| **Extracted text** | – (`raw_extracted_text`, `translated_text`) | raw text; English translation if any |
| **Change values** | as in 17.3 | the same correction tables and **Save changes**; plus **Re-check** → `POST /review {action: "recheck", comment: null}`. Works for finished invoices too (starts a new re-check thread). "Nothing was read from this file…" if there is no text. |
| **History** | `SELECT corrected_at, scope, line_number, field_name, original_value, corrected_value FROM audit.human_feedback … ORDER BY corrected_at DESC` | one line per correction or decision: `time - field: old -> new`. Empty: "No corrections yet." |

### 17.5 Ask

| Element | Request | Effect |
|---|---|---|
| **About** select | – | "All invoices" or one invoice → its `invoice_id` is sent |
| Chat input | `POST /query {question, invoice_id}` (timeout 300 s), spinner "Searching..." | answer appended to the chat; note under it: `<route> | score <average> | <attempts> attempt(s)` and "low confidence, please double-check" if not accepted. (`sources` is still in the API response but not shown.) One `rag_queries` row. |

The chat history lives only in `st.session_state.chat` (this browser session). It is not sent to the agent.

### 17.6 Evaluate

| Element | Request | Effect |
|---|---|---|
| Test question list | `GET /ragas/questions` | question in bold, correct answer below. First visit creates the tables and loads the starter questions. |
| **Delete** (per question) | `DELETE /ragas/questions/{id}` | row removed from `rag_test_questions`; rerun |
| **Question** + **Correct answer** + **Add question** | `POST /ragas/questions {question, answer}` | new `rag_test_questions` row; toast "Question added"; error 400 if a field is empty |
| **Evaluate all questions** | `POST /ragas/evaluate` (timeout 1800 s), spinner | one new run in `ragas_results`; rerun |
| **Run** select | `GET /ragas/runs` | lists `Run <id> - <time>`, newest first; "No evaluation has been run yet." if none |
| Metric tiles | from the chosen run | four averages, `-` if no value |
| Results table | `GET /ragas/runs/{run_id}` | question, expected answer, RAG answer, route, four scores |
| **History** table | from `GET /ragas/runs` | the averages of every run, for comparison |

### 17.7 History

| Element | Request | Effect |
|---|---|---|
| Event list | `SELECT occurred_at, file_name, event_type, detail FROM audit.file_events ORDER BY occurred_at DESC LIMIT 200` | one line per event |

---

## 18. Life of one invoice: what changes where

Example: `INV_EN_001.pdf` copied into `incoming/`, one price is 10 % too high.

| # | Moment | `invoice_audit` | Other tables | Redis / Qdrant / files |
|---|---|---|---|---|
| 1 | watcher sees the file (≥ 2 s old) | – | – | – |
| 2 | monitor | new row: id, file_path, file_name, file_type `pdf`, checksum, thread_id, status `detected` | `file_events` `detected` | – |
| 3 | extract | `extraction_method`, `raw_extracted_text`, status `extracted` | – | – |
| 4 | translate | `source_language` `en`, `translated_text` NULL, `translation_confidence` 1.0 | – | – |
| 5 | parse | invoice_number … total_amount, `extracted_raw` | `invoice_line_items` rows | – |
| 6 | data_validation | – | – | (`llm:` cache) |
| 7 | business_validation | `vendor_id` | – | mock-erp: `/vendor` or `/vendors`, `/po`, `/sku` × lines |
| 8 | reporting | `run_number` 1, `validation_status` `partial`, `recommendation` `review`, status `pending_review`, `report_json`, `processed_at` | `discrepancies` (run 1), `file_events` `pending_review` | `checksum:<sha>` |
| 9 | human_review (interrupt) | – | checkpoint tables | file moved to `pending/`, `file_path` updated |
| 10 | auditor saves a corrected price | – | `human_feedback` (scope `line_item`) | – |
| 11 | auditor presses Re-check | `run_number` 2, `validation_status` `passed`, `recommendation` `approve`, status `approved`, `report_json` (corrected price, `applied_corrections`), `vendor_id` | `discrepancies` (run 2, none), `human_feedback.revalidated` TRUE, `file_events` `completed` + `rechecked` | `checksum:` refreshed |
| 12 | report_files | `report_json_path`, `report_html_path` | – | `/reports/INV-…_<id8>.json/.html` |
| 13 | index | `indexed_at` | – | Qdrant points for the invoice text and report |
| 14 | watcher moves the file | `file_path` | – | file in `processed/` |

The extracted header columns and line items from step 5 are never changed afterwards. The corrected price is in `human_feedback`, in run 2's `report_json`, in the HTML/JSON report and in the indexed report text.

---

## 19. Status values and event types

**`invoice_audit.processing_status`**

| Value | Set by | Meaning |
|---|---|---|
| `detected` | insert default | row created, nothing read yet |
| `extracted` | `save_raw_text()` | text read; validation still running (or crashed) |
| `pending_review` | `save_outcome()` | waiting for a human |
| `approved` | `save_outcome()` or `save_decision()` | done |
| `rejected` | `save_outcome()` or `save_decision()` | done |
| `failed` | `mark_failed()` | extraction failed, no text, ERP down or a workflow error; dropping the same file again retries it |

**`validation_status`**: `passed`, `partial`, `failed` (see `decide()`). **`recommendation`**: `approve`, `review`, `reject`.

**`file_events.event_type`**

| Event | Written by |
|---|---|
| `detected` | monitor, every supported file |
| `duplicate` | monitor |
| `failed` | monitor (unsupported type), extractor, business validation (ERP down), watcher (crash) |
| `pending_review` | reporting, recommendation `review` |
| `completed` | reporting, recommendation `approve` or `reject` |
| `approved` / `rejected` | decision node (human decision) |
| `rechecked` | `respond()` after a re-check |

---

## 20. Error handling and failure modes

| Failure | What happens |
|---|---|
| Unsupported file type | event `failed`, file → `failed/`, no invoice row |
| Corrupt / unreadable file | invoice `failed` with `error_message`, file → `failed/` |
| No text found | invoice `failed`, file → `failed/` |
| mock-erp down during validation | invoice `failed`, file → `failed/`. Move the file back to `incoming/` to retry with the same row. |
| mock-erp down at start-up | agent refuses to start (`restart: on-failure:3`) |
| Bedrock error / no JSON | `llm.ask()` returns `None`; summaries use plain fallbacks; translation confidence 0 → review; router falls back to keywords; reflection scores 0 → retries |
| Redis down | warnings only; no cache, duplicate check falls back to Postgres |
| Qdrant down at start-up | agent refuses to start. During indexing: logged, invoice outcome unaffected. During a question: no chunks → "No processed invoice contains information about this." |
| Qdrant data lost | rebuilt from Postgres at start-up or with `POST /reindex` |
| Agent restarted while an invoice waits | nothing lost; the checkpoint lets Re-check/Approve/Reject resume it |
| Same file twice | second one is a `duplicate`, moved to `processed/` |
| A RAGAS metric fails | NULL in `ragas_results`, `-` in the UI, warning in the logs |
| The RAGAS process fails | HTTP 503 "The RAGAS evaluation failed, see the agent logs" |

---

## 21. Known limitations

- **Pending invoices are not searchable.** Indexing is the last workflow node, so an invoice in `pending_review` has no Qdrant chunks. Semantic questions about it return "not available".
- **Corrections never reach the extracted columns.** Structured answers, the invoice list and the header line keep showing the originally extracted values; the corrected values live in `human_feedback` and the latest `report_json`.
- **Invoices without a number** are shown as `None` in RAG contexts and answers (e.g. "Invoice None was rejected").
- **No chat memory.** Follow-up questions do not know what "it" refers to.
- **`recommendation` keeps the rules' value** after a human decision; the decision is in `processing_status`, `report_json.human_decision` and `human_feedback`.
- **One watcher.** The agent cannot be scaled to more than one replica without a queue; two watchers would process the same files.
- **Starter RAGAS answers are tied to the current data.** If invoices change, update `rag_test_questions.yaml` (for new databases) or the questions in the tab.
- `rules.yaml` keys `data_types`, `reporting.report_format/output_dir` and `logging` are documentation only.

---

## 22. Dependency versions

**Agent** ([`requirements.txt`](../agent/requirements.txt)): langgraph 0.3.34, langgraph-checkpoint-postgres 2.0.25, langchain-core 0.3.86, langchain-aws 0.2.35, boto3 1.40.40, fastapi 0.115.6, uvicorn[standard] 0.34.0, pydantic 2.10.4, python-multipart 0.0.20, pdfplumber 0.11.4, python-docx 1.1.2, pytesseract 0.3.13, pdf2image 1.17.0, pillow 11.0.0, langdetect 1.0.9, psycopg[binary] 3.2.3, psycopg-pool 3.2.4, qdrant-client 1.12.1, redis 5.2.1, httpx 0.27.2, Jinja2 3.1.4, PyYAML 6.0.2, ragas 0.2.15, datasets 3.2.0, pyarrow 18.1.0.

Why the last three are pinned: ragas 0.3+/0.4 pulls langchain-core 1.x and langgraph 1.x; newer `datasets`/`pyarrow` require NumPy 2 while langchain-aws needs NumPy 1.

**mock-erp:** fastapi 0.115.6, uvicorn[standard] 0.34.0, pydantic 2.10.4, psycopg[binary] 3.2.3, psycopg-pool 3.2.4.

**Dashboard:** streamlit 1.41.1, httpx 0.27.2, psycopg[binary] 3.2.3.
