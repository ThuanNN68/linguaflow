# Test suite

The suite is organized by the production boundary each test protects:

- `test_core/`: configuration, resilience, pagination, compression, health, and cache primitives.
- `test_agents/`: agent graphs, guardrails, parsing, tool contracts, and observability.
- `test_api/`: HTTP and WebSocket contracts through the FastAPI application.
- `test_database/`: PostgreSQL constraints and pgvector-compatible schema behavior.
- `test_services/`: domain service behavior and background-job lifecycle.
- `test_schemas/`: request and response validation contracts.
- `test_eval/`: deterministic evaluation data and scoring behavior.
- `test_scripts/`: deployment and release-control safety contracts.

PostgreSQL with the `vector` and `pg_trgm` extensions is required for the full
suite. Each database-backed test receives an isolated schema inside the
dedicated `_test` database; the fixtures refuse a non-PostgreSQL URL and never
drop the configured development database.

Run everything with:

```bash
docker compose up -d postgres
pytest tests/ -v --tb=short
```

The collection hook labels tests from their complete fixture graph:

- `pytest -m isolated` runs the fast suite without a database.
- `pytest -m postgres` runs only PostgreSQL-backed integration tests.
- `pytest tests/` remains the authoritative full gate.

The marker is derived from fixture dependencies rather than copied onto every
function, so a test automatically moves into the PostgreSQL group when it starts
using `test_db`, `client`, or `ws_client`.
