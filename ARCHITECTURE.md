# LinguaFlow architecture

This document defines the current system boundaries and the architectural
decisions that contributors must preserve. Source code, migrations, and tests
remain the executable source of truth.

## System context

LinguaFlow is a multilingual collaboration application with realtime messaging,
translation, voice transcription, a private assistant, internal calendar events,
reminders, attachments, and administration tools.

```text
Browser / Next.js
  ├─ HTTPS REST requests
  └─ authenticated WebSocket
             │
             ▼
FastAPI application
  ├─ API and WebSocket adapters
  ├─ domain services
  ├─ assistant and translation agents
  └─ background recovery and reminder tasks
       │          │           │
       ▼          ▼           ▼
 PostgreSQL    Redis       External providers
 + pgvector   backplane   LLM / STT / TTS / email / RTC / storage
```

## Component boundaries

### Frontend

`frontend/` is a Next.js and React application. Feature modules own screens,
components, API clients, hooks, state transitions, and tests. Shared primitives
must not import feature-specific modules. `NEXT_PUBLIC_*` values are build-time
configuration and require a rebuild after changes.

### API

`src/api/` translates HTTP and WebSocket requests into domain operations. REST
handlers are owned by domain routers (`auth`, `profile`, `calendar`, `chat`,
`intelligence`, and `attachments`); `routes.py` only aggregates them for the
application entry point. API handlers authenticate, authorize, validate, invoke
services, and serialize responses. They should not contain reusable business
rules or long-running work.

### Domain services

`src/services/` is organized by domain: assistant, calendar, identity,
intelligence, language, messaging, shared infrastructure, and voice. Services
receive explicit sessions or factories and expose operations with clear
transaction boundaries. Long-lived objects must not retain request-scoped
database sessions.

### Agents

`src/agents/` contains LangGraph state, prompts, tools, guardrails, and workflow
nodes. Agent output is treated as untrusted structured input. Authorization,
consent, schema validation, and human approval are enforced outside model text.

### Persistence

PostgreSQL 16 is the system of record. pgvector supports semantic retrieval;
Alembic is the only supported schema-migration mechanism. ORM declarations are
organized under `src/database/models/` by auth, chat, translation, assistant,
and calendar domain while `src.database.models` remains the stable public
registry for services and Alembic. Every test uses an isolated schema.
Application code uses async SQLAlchemy sessions scoped to one request, event,
or background operation.

### Realtime delivery

Each process owns its connected WebSockets. Redis pub/sub is the backplane for
cross-process broadcasts. Without Redis, production must use one backend worker
and one replica. Application heartbeat and idle cleanup remove half-open clients.

## Primary flows

### Message delivery

1. Authenticate the sender and verify conversation membership.
2. Validate and persist the original message.
3. Commit before broadcasting durable identifiers.
4. Publish recipient-specific events locally and through Redis when configured.
5. Run translation or voice processing in separately scoped operations.
6. Persist results and emit idempotent status updates.

### Translation

The original text is immutable source data. Language detection, glossary context,
provider selection, fallback, cache, and output guardrails produce versioned
translations. Provider failures degrade to a controlled result and remain
observable; they do not corrupt the original message.

### Voice transcription

Uploads are validated and stored before background transcription begins. Voice
messages move through durable lifecycle states. Startup recovery finds pending
work after process restarts. Conversion concurrency is bounded per event loop,
and provider retry behavior uses public SDK configuration.

### Assistant actions

Assistant requests require explicit consent and operate only within the caller's
authorization scope. Retrieval returns permitted content. Side-effect proposals
are persisted for human review; model output never performs an external action
directly.

## Security boundaries

- JWT authentication establishes identity; every resource operation also checks ownership or membership.
- Admin metadata access and conversation-content access are separate permissions.
- Secrets are provided through environment or deployment secret stores.
- Uploaded files require type, size, ownership, and storage-path validation.
- Prompt content and model output never override application authorization.
- CORS and WebSocket origin checks use explicit production origins.
- Rate limits protect authentication, uploads, messaging, and expensive AI paths.

## Reliability model

- Liveness proves the process is running; readiness checks required dependencies.
- Circuit breakers and timeouts isolate external providers.
- Redis enables horizontal realtime fanout but PostgreSQL remains authoritative.
- Background tasks use durable lifecycle state and startup recovery where loss
  would leave user-visible work pending.
- Structured logs and metrics include operation identifiers without recording
  secrets or private message bodies.
- Optional OpenTelemetry spans connect inbound HTTP, database, and outbound HTTP
  operations through an OTLP collector; model traces remain a separate concern.
- Database and attachment backups are independent and periodically restored in a test environment.

## Architectural decisions

| ID | Decision | Rationale |
|---|---|---|
| ADR-001 | FastAPI and async SQLAlchemy | Async HTTP, WebSocket, and provider workloads share one model. |
| ADR-002 | PostgreSQL as the system of record | Strong transactions and operational maturity. |
| ADR-003 | pgvector in PostgreSQL | Retrieval remains inside existing authorization and backup boundaries. |
| ADR-004 | Alembic-only schema changes | Reviewable, repeatable upgrades across environments. |
| ADR-005 | Original messages remain immutable | Translation can be retried and audited without losing source text. |
| ADR-006 | Redis backplane for horizontal WebSockets | Process-local connection maps cannot broadcast across replicas. |
| ADR-007 | Human approval for assistant side effects | Model output is probabilistic and cannot be trusted as authorization. |
| ADR-008 | Domain-oriented services | Ownership and dependencies remain visible as features grow. |
| ADR-009 | Internal calendar for v1 | Avoids external OAuth and synchronization complexity in the core release. |

## Deployment profiles

### Local

Next.js and FastAPI run on the host; Docker Compose supplies PostgreSQL/pgvector.
Redis and external providers are optional for focused development.

### Evaluation/free tier

Frontend and backend may run on separate managed platforms with hosted
PostgreSQL and object storage. Expect cold starts and provider quotas. Do not use
the evaluation environment for sensitive data.

### Production

Use TLS, managed secrets, private PostgreSQL networking, durable object storage,
Redis for more than one backend replica, pre-traffic migrations, independent
backups, health probes, and post-deployment smoke tests.

## Change rules

- Update this document when topology, ownership, or an ADR changes.
- Update `docs/api/contract.md` for public REST, DTO, or WebSocket changes.
- Add migrations and upgrade tests for persistence changes.
- Add failure-path tests for every new provider or background workflow.
- Document user-visible behavior in `README.md` and the relevant runbook.

## Related documentation

- [Documentation index](docs/README.md)
- [API and data contract](docs/api/contract.md)
- [System diagrams](docs/architecture/diagrams.md)
- [Runtime reliability](docs/operations/reliability.md)
- [Deployment runbook](docs/operations/deployment.md)
