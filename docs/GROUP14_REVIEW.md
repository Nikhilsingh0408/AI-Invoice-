# Group 14 review and verification

Reviewed 19 October 2026. Changes preserve service layout, API routes, table schemas,
rules, model providers, and the LangGraph processing sequence. No Streamlit file
was edited. No production services were started and no existing database was changed.

## Review workflow and boundaries

1. Inspect clean Git baseline, configuration, backend modules, schema, and UI API callers.
2. Distinguish reproducible functional defects from style preferences.
3. Make focused backend fixes and verify the existing UI payloads remain accepted.
4. Run regression tests with real application imports and mocked external calls;
   separately exercise schema and transactions in disposable PostgreSQL.
5. Check syntax, dependencies, whitespace, unchanged Streamlit files, and ZIP contents.

Safe naming rule: use “AI Invoice Auditor – Group 14” in human-facing project titles.
No previous group identifier was found in reviewed project references. Retain Python
packages, service/image names, Compose project name, volumes, schemas, endpoints,
and model IDs: renaming them would risk breaking deployments or hiding existing data.
The repository directory and Streamlit display strings remain unchanged.

## Bugs fixed and modified files

| File | Severity / root cause | Minimal fix | Verification |
| --- | --- | --- | --- |
| `agent/app/agents/monitor.py` | High: cached checksum ID bypassed failed-invoice retry and could reference a deleted database row. Medium: incoming symlinks allowed processing outside the intended folder. | Check authoritative PostgreSQL state for duplicates; retain retry behavior; ignore symlink entries. Remove unused cache import. | Failed retry with stale cache, completed duplicate, and symlink regression tests. |
| `agent/app/watcher.py` | High: second-resolution archive names could overwrite a second collision in the same second. Medium: a disappearing file aborted a whole scan. | UUID suffix for colliding filenames; continue when a file disappears. | Real temporary-file tests for repeated same-second moves and disappearing-file scan. |
| `agent/app/agents/parser.py` | High: whitespace-table lines with incorrect arithmetic were dropped before validation, losing evidence of discrepancies. High: stored corrections could overwrite `line_items`/`extras` with strings or inject invalid line fields. | Keep numeric line rows for existing arithmetic validation; allow only the UI's editable header and line fields. | Incorrect arithmetic retained and flagged; pipe rows preserved; invalid structured-field corrections ignored; valid correction applied. |
| `agent/app/main.py` | High: feedback API accepted fields/scopes that could corrupt revalidation state. Medium: all database errors were incorrectly reported as invoice-not-found. | Validate correction fields, scope, line numbers, numeric/date values; validate lookup UUID and propagate infrastructure failures. Update API title only for Group 14. | Valid UI payload accepted, invalid payloads rejected before writes, invalid UUID rejected, DB outage preserved. |
| `agent/app/agents/translator.py` | High: null/nonnumeric confidence caused workflow failure; non-finite confidence could reach validation/storage. | Treat malformed/non-finite confidence as zero so existing review policy applies. | None, invalid string, NaN, infinity cases. |
| `agent/app/database.py` | High: header, deleted lines, and inserted lines committed separately, leaving partial records on failure. Medium: missing-table error advised deleting all database volumes. | Save header and line replacement in one transaction; replace destructive recovery advice with backup/schema guidance. | Real PostgreSQL test forces numeric overflow and confirms original header and lines survive rollback. |
| `agent/app/config.py`, `mock_erp/app/db.py` | High when credentials contain URL delimiters: unescaped credentials/database names corrupt PostgreSQL connection URLs. | Percent-encode URI user/password/database components. | Syntax/import checks; ordinary-credential PostgreSQL integration. Special-character encoding checked separately. |
| `README.md`, `docs/TECHNICAL_REFERENCE.md` | Naming consistency. | Group 14 document titles. | Diff review. |
| `tests/test_backend_regressions.py` | New regression coverage. | 14 focused tests using real backend modules, mocks for remote dependencies, and temporary files. | Passed in existing agent image. |
| `tests/test_database_integration.py` | New opt-in database coverage. | 2 tests for transaction rollback and Postgres-backed workflow compilation/checkpoint reads. | Passed against disposable PostgreSQL 16.6 initialized with existing SQL. |
| `scripts/package_project.py` | Requested reproducible deliverable. | ZIP tracked files and non-ignored additions; exclude credentials, Git history, symlinks, bytecode, and agent metadata. | ZIP CRC validation. |
| `docs/GROUP14_REVIEW.md` | Requested review deliverable. | Record scope, findings, changes, evidence, and remaining limitations. | Reviewed with final diff. |

## Verification and limits

- 16 tests passed: 14 focused regressions plus 2 real database integration tests.
- Tests loaded the actual FastAPI module, extraction/translation modules, workflow,
  RAG graph, and their transitive dependencies inside the existing agent image.
- `pip check` in that image: no broken requirements. This is not a fresh image build
  or a guarantee that installed versions exactly match every repository pin.
- PostgreSQL used disposable test credentials, fresh schema SQL, and no published
  host ports. Production invoice data and AWS credentials were not used.
- Python syntax and Compose configuration checked separately; no bytecode written
  into the repository. Existing tracked bytecode left unchanged.
- No pre-existing unit/integration test suite was found.
- No live Bedrock inference, Qdrant indexing/retrieval, Redis integration, OCR,
  RAGAS scoring, complete invoice workflow, or browser UI run was performed.
  Full-stack application correctness is therefore not claimed.
- LangGraph emitted an existing pending-deprecation warning about checkpoint
  deserialization defaults. No dependency upgrades were made.

Run focused tests with agent dependencies installed:

```sh
PYTHONPATH=agent PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Database tests skip unless `RUN_DATABASE_TESTS=1`; enable them only against an
isolated database initialized with `db/init`, using test PostgreSQL environment values.
Use a fresh database for each run (the integration test uses a fixed checksum).

## Remaining findings / intentionally unchanged

- **Streamlit, conditional high:** its PostgreSQL URL concatenates credentials
  without escaping too (`streamlit_ui/app/main.py`). Special-character credentials
  can still prevent the UI connecting. Reported only, as requested; do not assume
  backend URL fixes also fix the UI.
- **Deployment security:** API operations have no authentication and Compose
  publishes storage ports. Adding authentication/network policy requires deployment
  choices and potentially UI changes; no architecture changes made here.
- **Concurrency:** the watcher lock is process-local; do not run multiple ingestion
  workers against the same folder without a separate cross-process coordination design.
- **File completeness:** the existing two-second age heuristic cannot prove a slow
  upload is finished. Producers should publish complete files by atomic rename.
- **Recovery:** startup/shutdown resource cleanup and recovery of invoices left
  mid-processing by a hard crash merit further integration testing.
- **RAG/reporting:** indexing/report-file failures remain logged, not treated as
  invoice-validation failures; a prior vector may remain searchable during re-review.
  These policies were not redesigned.
- Generated invoice fixtures/runtime documents and tracked bytecode remain in Git;
  no user data or working functionality was removed for cleanup.
- Correction validation checks field/type eligibility; numeric parsing retains the
  existing permissive localized parser. It is not a complete schema/length validator.
- **Correction to earlier review:** agent and Streamlit Dockerfiles already define
  health checks. Their absence from Compose itself is not a defect.

## ZIP

The ZIP contains current project sources, config, SQL, tracked data, unchanged
Streamlit files, tests, and this report. Local `.env`, AWS credentials, Git history,
editor/agent metadata and bytecode are intentionally excluded. Use `.env.example`
to configure a deployment. Nothing is committed or pushed by this review.

```sh
python scripts/package_project.py /tmp/group14-deliverables/ai-invoice-auditor-group-14.zip
```
