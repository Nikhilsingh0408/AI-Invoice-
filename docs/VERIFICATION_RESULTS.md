# Verification results — 19 October 2026

## Environment and method

Tests ran in uniquely named `group14-verify-*` Compose projects. Test PostgreSQL
credentials, volumes and file directories were isolated from the normal stack.
Live checks used the configured AWS Bedrock account with synthetic invoice content.
No existing audit records or production volumes were deleted.

## Executed checks

| Check | Evidence |
| --- | --- |
| Application build | Three application images built from current Dockerfiles with separate verification tags. Docker dependency layers may be cached. |
| Startup | Six isolated services reached their health checks, including agent and Streamlit. |
| Regression suite | 23 tests: 19 backend regressions, two PostgreSQL integration tests, two document-reader tests. All passed in the isolated database-enabled run. |
| Offline suite | 21 tests passed; two database tests skipped when PostgreSQL was intentionally unavailable. |
| Document extraction | Real DOCX paragraphs/table cells and Tesseract OCR for generated PNG/scanned PDF. |
| Live invoice | Synthetic German DOCX uploaded through the API; German detection, translated text, persisted report and embeddings asserted. |
| Live RAG | Invoice-scoped question about total/currency passed reflection using real Bedrock. |
| Persistence | Synthetic PostgreSQL/Qdrant fixtures survived container recreation; the same live invoice retained its report/index marker and answered a query after restart. |
| Deliberate loss | Passed: audit and vector stores were empty after removing only the isolated named volumes. |
| Service networking | Agent resolved PostgreSQL, Qdrant, Redis and mock ERP by service name. |
| Dashboard | Streamlit AppTest rendered Overview, Invoices, Human review and Ask without exceptions. No browser visual/layout assertion was made. |
| Image inspection | Non-root users confirmed. Docker metadata sizes: agent 300,686,314 bytes; ERP 64,347,517 bytes; UI 195,745,119 bytes for inspected pre-review images. No credential-setting commands found in their build histories. |
| Source checks | Python AST syntax checks and `git diff --check` passed. `docker compose config --quiet` passed without printing secrets. No direct ERP-schema query found in agent/UI Python source. |

## Fixes verified during this review

- Upload and status handlers now import `Path` and `uuid` correctly.
- Successful extraction is reused when retrying a failed invoice.
- Reflection rejects invalid/non-finite scores and enforces the strict 0.7 threshold.
- Structured and semantic routes stop after three unsuccessful attempts.
- Answers that remain unverified are replaced with an explicit inability-to-verify response.
- Reflection scores are returned and cached alongside answer metadata.
- The malformed `.env.example` introductory line is now a comment.

## Qualifications

The machine-readable record is [verification-evidence.json](verification-evidence.json).
The synthetic invoice finished as approved, with German source language and a RAG
reflection average of 1.0. This is the model's score for one fixture, not an independent
accuracy benchmark. Test resources were removed after verification.

The project owner chose to retain ChatBedrock and existing configurable models.
LiteLLM/Cohere-specific compliance is therefore an agreed deviation, not a completed
migration. Structured queries use SQL context but still use the model for routing
and answer wording.

The suite is evidence for the tested cases, not proof for every invoice layout or
language. Digital-PDF text extraction, all correction/recheck combinations, browser
visual behavior and long-running batch performance were not exhaustively tested.
Streamlit reported ignored theme settings unsupported by its installed version;
all four tested pages nevertheless rendered. LangGraph emitted a pre-existing
checkpoint-serialization pending-deprecation warning.

Image-history review cannot prove the absence of secrets in every historical file
layer. Credentials are excluded from the delivered source archive.

## Reproduction

```sh
python scripts/verify_deployment.py --build --live --output /tmp/group14-final-verification.json
```

This uses real Bedrock calls and incurs usage charges. The script removes its own
temporary containers and volumes; verification image tags remain for inspection.
The normal Compose project is unaffected. Do not substitute a production project
name into the destructive demonstration commands.
