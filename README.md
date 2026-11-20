# AI Invoice Auditor — Group 14

Containerized multilingual invoice validation and audit reporting.

The application extracts vendor invoices from PDF, DOCX and scanned images, translates non-English text, and validates invoice fields against ERP records. PostgreSQL retains extracted text, findings, decisions and human corrections. A Streamlit dashboard supports review and natural-language queries over the audit record.


## What's in it

Six containers, all started from `compose.yaml`:

- **agent** - FastAPI + LangGraph. Watches the incoming folder, runs the audit workflow and answers questions. Port 8000.
- **mock-erp** - small read-only API in front of the ERP tables (vendors, POs, SKUs). Port 8001.
- **streamlit-ui** - the dashboard. Port 8501.
- **postgres** - ERP data, the audit trail and the LangGraph checkpoints.
- **qdrant** - vector store for the chat.
- **redis** - disposable cache for model answers, translations and query results. PostgreSQL checksums determine duplicates.

The AI models run on AWS Bedrock through `ChatBedrock`.

The assessment specification names LiteLLM and Cohere Command R+. This implementation retains its existing ChatBedrock integration and configurable Bedrock model IDs by project-owner decision. It does not claim compliance with that specific gateway/model requirement.

## Running it

You need Docker and AWS credentials with Bedrock access (in `~/.aws`).

```sh
cp .env.example .env      # then fill in the DB password and model ids
mkdir -p data/incoming data/pending data/processed data/rejected data/failed outputs/reports
# Set LOCAL_UID and LOCAL_GID in .env to the output of id -u and id -g.
# The folders above must be writable by that account.
docker compose up --build -d
docker compose ps         # wait until everything is healthy
```

Open http://localhost:8501. The API docs are at http://localhost:8000/docs.

To add an invoice, drop the file into `data/incoming/`. It gets picked up within a few seconds.

`docker compose down` stops everything and keeps the data. `docker compose down -v` also wipes the database and the vectors.

## How an invoice is processed

1. **Monitor** - works out the file type and a SHA-256 checksum. If we've seen the same content before, it's a duplicate and gets skipped.
2. **Extract** - pdfplumber for digital PDFs, Tesseract OCR for scans and images, python-docx for Word files. Raw text is persisted and reused on retries after successful extraction; extraction failures can be retried.
3. **Translate** - langdetect, then the model translates anything that isn't English and gives a confidence score.
4. **Parse** - regex/label based, picks out invoice number, date, vendor, PO, amounts and line items.
5. **Data validation** - required fields, currency, line maths, subtotal + tax = total, tax rate.
6. **Business validation** - compares the invoice with the ERP through the mock-erp API: vendor, PO, quantities, prices (5% tolerance) and tax.
7. **Decision** - any "reject" rule hit means reject, any other problem means a person has to review it, low translation confidence also means review, otherwise approve. This part is plain Python. The model only writes the summaries.
8. **Report** - JSON and HTML reports go to `outputs/reports/`, and the invoice gets indexed in Qdrant for the chat.

If it needs a review, the workflow pauses (LangGraph `interrupt`, checkpointed in Postgres) until someone answers in the dashboard.

## The dashboard

- **Pending review** - invoices waiting for a person. You can fix wrong values and then Re-check, Approve or Reject.
- **Invoices** - everything that's been processed. Each one has its report, the text we read, an editable "Change values" tab and a history of corrections.
- **Ask** - chat over the audit data. Counting or listing questions ("how many were rejected?") go to SQL. Anything else goes to vector search. Each answer shows RAGAS faithfulness, answer relevance, and context precision scores.
- **History** - log of every file the agent has seen.

## Things worth knowing

- Corrections are stored as new rows in `audit.human_feedback`. The originally extracted values are never overwritten. A re-check uses the corrections, so the new report shows them, but the invoice list still shows what was originally read.
- Invoices waiting for review aren't in the vector index yet, so the chat can't find them by search until they're approved or rejected.
- The chat has no memory. Pick the invoice in the "About" box instead of asking "why was it rejected?".
- Rules and tolerances are in `config/rules.yaml`, and prompts are in `config/prompts/`. Changes there are picked up without a rebuild.
- If Qdrant loses its data, the agent rebuilds it from Postgres on start-up (or call `POST /reindex`).
- If an invoice fails because mock-erp was down, move the file from `failed/` back to `incoming/` and it'll be retried.

## Folder layout

```
compose.yaml
db/init/                    SQL that creates the schemas and loads the ERP data
config/                      rules, personas, prompts, RAGAS starter questions
data/                        incoming / pending / processed / rejected / failed
outputs/reports/             generated reports
agent/                      workflow, agents, RAG, RAGAS, API
mock_erp/                   ERP API
streamlit_ui/               dashboard
docs/TECHNICAL_REFERENCE.md the long version
```

## Verification and demonstration

See [requirements and evidence](docs/REQUIREMENTS.md) for the compliance matrix and
[five-slide presentation](docs/PRESENTATION.html) for the demonstration deck.

With the three application images already built and PyYAML installed, run:

```sh
python scripts/verify_deployment.py --output /tmp/group14-verification.json
```

The script starts a separate Compose project, runs regression tests, checks service
DNS, recreates containers with volumes retained, then deletes only its own test
volumes and verifies empty stores. Synthetic fixtures establish storage durability;
they do not establish live model quality or complete invoice processing.

Do not run `docker compose down -v` against a stack containing audit history you
need to retain. Named volumes survive `down`, but not `down -v`. Source credentials
from the runtime environment or AWS credentials mount; never submit `.env`.
