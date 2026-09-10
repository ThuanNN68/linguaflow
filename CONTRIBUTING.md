# Contributing to LinguaFlow

Thank you for improving LinguaFlow. This guide keeps changes reviewable,
reproducible, and safe across the FastAPI backend and Next.js frontend.

## 1. Prepare the environment

Requirements: Python 3.11+, Node.js 20+, Docker Desktop, and Docker Compose.

```powershell
git clone <repository-url>
cd linguaflow
Copy-Item .env.example .env
docker compose up -d postgres

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m alembic upgrade head

cd frontend
npm ci
```

Use only test credentials locally. Never commit `.env`, API keys, access tokens,
private user data, database dumps, or generated uploads.

## 2. Choose a focused scope

- Keep each pull request focused on one feature, fix, refactor, or documentation change.
- Discuss broad architecture or contract changes in an issue before implementation.
- Preserve unrelated work already present in the working tree.
- Add or update tests for every behavior change.

## 3. Branches and commits

Create a short-lived branch from the current `main` branch:

```bash
git switch main
git pull --ff-only
git switch -c feat/short-description
```

Use Conventional Commit-style subjects such as `feat:`, `fix:`, `test:`,
`docs:`, `refactor:`, `ci:`, or `chore:`. Write imperative, specific subjects
and explain non-obvious tradeoffs in the commit body.

## 4. Code conventions

### Python

- Follow the existing async SQLAlchemy and FastAPI dependency patterns.
- Keep business logic in the appropriate `src/services/<domain>/` package.
- Keep API handlers thin and validate public data with Pydantic schemas.
- Add an Alembic migration for every persistent schema change.
- Run Ruff and relevant pytest suites before committing.

### TypeScript and React

- Keep feature code under `frontend/src/features/` and shared primitives under
  `frontend/src/shared/`.
- Avoid `any` when a stable API or component type can be expressed.
- Keep network access in API modules and reusable behavior in hooks.
- Include accessible labels, keyboard behavior, and visible error states.

### API and database

- Maintain backward compatibility unless the pull request documents a breaking change.
- Treat WebSocket payloads as versioned public contracts.
- Scope database sessions to operations; do not retain sessions in long-lived sockets.
- Verify migration upgrade and schema-head uniqueness.

## 5. Documentation

Update documentation in the same pull request as the implementation:

- REST, DTO, or WebSocket change: `docs/api/contract.md`.
- Topology or architectural decision: `ARCHITECTURE.md` and diagrams.
- Environment or release change: `.env.example` and deployment docs.
- User-visible feature or limitation: `README.md` and the relevant operations
  or feature documentation.

All maintained documentation is written in English. Keep links relative and avoid
references to private infrastructure, personal credentials, or temporary reports.

## 6. Verification

Backend:

```powershell
python -m ruff check src tests eval scripts
python -m alembic heads
python -m alembic upgrade head
python -m alembic check
python -m pytest tests -q
```

Frontend:

```powershell
cd frontend
npm ci
npm run lint -- --max-warnings=0
npx tsc --noEmit
npm test
npm run build
npm audit --omit=dev
```

Tests marked `postgres` require the PostgreSQL/pgvector service. Never weaken an
assertion or suppress a broad warning merely to make CI green.

## 7. Pull requests

Describe the problem, chosen solution, compatibility impact, migrations, and
verification evidence. Complete the pull-request template, link related issues,
and keep generated files out of the diff. CI must pass before merge.

Reviewers should be able to answer:

- Is the behavior correct and covered by tests?
- Are authorization and data boundaries preserved?
- Can the migration and deployment be rolled forward safely?
- Are failure modes observable and documented?

## 8. Security and conduct

Do not report vulnerabilities in public issues. Follow [SECURITY.md](SECURITY.md)
for private disclosure.
