# LinguaFlow

[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![Backend tests](https://img.shields.io/badge/backend%20tests-1%2C134%20passing-brightgreen)](tests/README.md)
[![Frontend tests](https://img.shields.io/badge/frontend%20tests-59%20passing-brightgreen)](frontend/README.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](requirements.txt)
[![Next.js 16](https://img.shields.io/badge/Next.js-16-black?logo=next.js)](frontend/package.json)

LinguaFlow is a real-time multilingual collaboration application. A Next.js
client and FastAPI service provide direct and group chat, per-user message
translation, attachments, voice messages, a private assistant, internal calendar
events, and reminders. The original message is retained while translated variants
are prepared for each recipient.

**Live staging:** [Open LinguaFlow](https://c3-lingua-flow-217.dquangminh2003.id.vn/login) ·
[API health](https://api-c3-lingua-flow-217.dquangminh2003.id.vn/health/ready) ·
[OpenAPI](https://api-c3-lingua-flow-217.dquangminh2003.id.vn/docs)

> Staging is a shared evaluation environment. Availability and external AI
> features can be affected by free-tier cold starts or provider quotas.

## What the application provides

- Authenticated direct and group conversations over REST and WebSocket.
- Language detection, translation, retry, feedback, and glossary-aware paths.
- Attachments, voice recording and transcription, reactions, replies, search,
  forwarding, and personal saved state.
- A private assistant that can answer chat requests, summarize a conversation,
  and create reviewable internal-calendar proposals after the required consent.
- Internal calendar events and durable reminders. External calendar syncing and
  external meeting-link creation are not part of the product.
- JWT authentication, rate limiting, health checks, role-protected admin APIs,
  structured logging, and Alembic migrations.

## Architecture

```text
Browser (Next.js, :3000)
          | HTTP and WebSocket
          v
FastAPI (:8000)
  |- auth, chat, files, calendar, administration
  |- translation and assistant services
  |- WebSocket manager and reminder scheduler
          |
          v
PostgreSQL 16 + pgvector (:5432 locally)
```

Each process owns its local WebSocket connections. When `REDIS_URL` is set, the
connection manager uses Redis pub/sub as a backplane so broadcasts reach clients
connected to other workers or replicas. A single-process deployment can leave
`REDIS_URL` empty.

## Technology

| Area | Implementation |
|---|---|
| Client | Next.js, React, TypeScript |
| API | FastAPI, Pydantic, SQLAlchemy async, WebSocket |
| Data | PostgreSQL 16, pgvector, Alembic |
| AI | Configurable LLM providers; Groq is the usual local default |
| Operations | Docker Compose, health endpoints, pytest, Ruff |

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker Desktop and Docker Compose for local PostgreSQL
- An API key for the selected LLM provider when real AI output is required

## Local setup

### 1. Create a private environment file

Create `.env` at the repository root. Do not commit it. Generate a secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Minimal example:

```dotenv
APP_ENV=development
JWT_SECRET=replace-with-a-long-random-secret
DATABASE_URL=postgresql+asyncpg://linguaflow:linguaflow@127.0.0.1:5432/linguaflow
CORS_ORIGINS=http://127.0.0.1:3000,http://localhost:3000
LLM_PROVIDER=groq
GROQ_API_KEY=
```

The API can start without an LLM key, but translation and assistant generation
will use a fallback or return the original text. `src/config.py` is the complete
configuration reference.

### 2. Start PostgreSQL and the backend

```powershell
docker compose up -d postgres
docker compose ps

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

Alembic is the schema source of truth. Do not use ad-hoc table creation or delete
a database to apply a regular schema change. Local PostgreSQL normally binds to
`127.0.0.1:5432`.

### 3. Start the frontend

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

For a production-like local run:

```powershell
npm run build
npm run start
```

Open [http://127.0.0.1:3000/login](http://127.0.0.1:3000/login). The backend
health endpoint is [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

After installing dependencies, the development launchers can start both services:

```powershell
.\scripts\development\dev.ps1
```

```bash
./scripts/development/dev.sh
```

## Verification

```powershell
# repository root, with the virtual environment enabled
python -m ruff check src tests
python -m pytest tests -q

# frontend
cd frontend
npm run lint
npm run build
```

The frontend build validates TypeScript, imports, routes, and static assets.
Some backend integration tests require PostgreSQL and current migrations.

## Important configuration

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development`, `test`, or `production` |
| `JWT_SECRET` | Required signing secret; use a unique, strong production value |
| `DATABASE_URL` | Async PostgreSQL URL |
| `CORS_ORIGINS` | Allowed browser origins; also used for WebSocket origin checks |
| `FRONTEND_URL` | Canonical public client URL |
| `LLM_PROVIDER` and provider key | Select assistant and translation provider |
| `ATTACHMENT_STORAGE_BACKEND` | `local`, `supabase`, `s3`, or automatic provider selection |
| `S3_*` / `SUPABASE_*` | Durable object-storage credentials for production attachments |
| `REDIS_URL` | WebSocket backplane and distributed rate-limit storage |
| `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW` | Per-process database connection budget |
| `TTS_PROVIDER` | Server speech fallback: `openai`, `elevenlabs`, `google`, or `disabled` |
| `WEB_PUSH_VAPID_*` | Optional PWA background-notification key pair |
| `WEBHOOK_*` | Signed outbound-webhook timeout, retry, and network policy |
| `EMAIL_PROVIDER`, `SMTP_*` | Email delivery configuration |

`NEXT_PUBLIC_*` frontend values are embedded at build time. Rebuild and redeploy
the frontend after changing `NEXT_PUBLIC_API_URL`.

## Production baseline

1. Use managed PostgreSQL with TLS, backups, and no public database access.
2. Set `APP_ENV=production`, a new `JWT_SECRET`, database URL, valid public
   origins, and LLM credentials in the deployment secret store.
3. Apply migrations before serving traffic. The Docker workflow runs
   `alembic upgrade head` before Uvicorn.
4. Use durable storage or object storage for uploads; container filesystems are
   not persistent across deployments.
5. Use one backend process when `REDIS_URL` is empty. Configure the same Redis
   backplane for every worker/replica before scaling horizontally.
6. Verify login, REST, WebSocket messaging, assistant output, and reminders on
   the public HTTPS domain after each release.
7. Back up database data and uploaded files independently.

## Current limitations

- Horizontal WebSocket scaling requires Redis; without it, run one backend
  worker and one replica.
- Translation, embeddings, transcription, email, and RTC depend on separately
  configured providers and their quotas.
- Browser text-to-speech voice quality varies by device. Authenticated OpenAI,
  ElevenLabs, and Google Cloud TTS fallbacks are selectable server-side.
- Web Push and outbound webhooks are dormant until their keys/endpoints are
  configured. Webhook deliveries are signed and persisted for bounded retry.
- Calendar events and reminders are internal to LinguaFlow; external calendar
  synchronization is intentionally not included in the current release.
- Local attachment storage is suitable for development only. Multi-replica
  production deployments can select Supabase or an S3-compatible backend.
- The staging environment is intended for evaluation, not for sensitive or
  production data.

## Project status

The backend identifies the current API baseline as `1.0.0`. The project is being
reviewed before its first public release and includes the complete chat,
translation, voice, assistant, calendar, admin, migration, and automated-test
baseline. See [SECURITY.md](SECURITY.md) for responsible disclosure.

## Repository layout

```text
frontend/       Next.js client
src/            FastAPI application, domain services, agents, schemas, database models
alembic/        Versioned schema migrations
tests/          Backend and contract tests
scripts/        Development, maintenance, and deployment utilities
docs/           Product, API, architecture, and deployment documentation
```

See [the documentation index](docs/README.md) for the maintained architecture,
API, realtime, deployment, and operations guides.
Script ownership and safety conventions are documented in
[scripts/README.md](scripts/README.md).
Service ownership and domain boundaries are documented in
[src/services/README.md](src/services/README.md).

## Security

Keep secrets only in local environment files or the deployment secret store.
Never expose PostgreSQL, LLM keys, JWT secrets, reset codes, or private uploads.
Review CORS origins, access control, consent scopes, and backup policy before a
production release.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [API and behavior contract](docs/api/contract.md)
- [Feature operations](docs/operations/features.md)
- [Deployment runbook](docs/operations/deployment.md)
- [Contribution guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
