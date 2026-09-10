# Runtime reliability

This document defines the production reliability baseline, expected degradation,
and incident response signals for LinguaFlow.

## Service objectives

- The API remains responsive when optional AI providers fail.
- Durable messages are committed before acknowledgement and broadcast.
- A process restart does not leave recoverable voice work permanently pending.
- Readiness fails when required dependencies cannot safely serve traffic.
- Private data is not exposed as part of fallback or diagnostic behavior.

## Rate limiting

Apply stricter limits to authentication, password recovery, uploads, WebSocket
connects, and provider-backed operations. Use Redis-backed coordination in a
multi-replica deployment. Return `429` with a stable code and retry guidance.

## Response compression and pagination

Compress suitable HTTP responses at the proxy or application boundary. Do not
compress already-compressed uploads. Bound list endpoints and use cursor
pagination for message history so response cost does not grow with conversation size.

## Cache strategy

Translation and retrieval caches are optimizations, not sources of truth. Cache
keys include language, model, glossary/version inputs, and authorization-relevant
scope. A cache outage degrades to direct computation within provider limits.

## Health checks

- Liveness reports process health only and must not depend on external providers.
- Readiness verifies the dependencies required to accept normal traffic.
- Optional provider status belongs in detailed system health and metrics.
- Health responses never reveal credentials, internal URLs, or private data.

## Timeouts, retries, and circuit breakers

Every network provider call has a bounded timeout. Retry only transient,
idempotent operations with exponential backoff and jitter. Circuit breakers stop
repeated calls to an unhealthy provider and periodically probe recovery. Invalid
credentials and validation errors are not retried.

## WebSocket reliability

- Application ping/pong and idle timeouts remove half-open clients.
- Database sessions are opened per event or operation, never for socket lifetime.
- Redis pub/sub is required for fanout across processes.
- Client mutation IDs support acknowledgement correlation and safe retry.
- Reconnecting clients reload durable REST state because broadcasts are not a log.

## Background work

User-visible long-running work uses a durable lifecycle state. Workers claim an
operation idempotently, record attempts, and persist terminal state. Startup
recovery scans pending voice transcription work. In-memory task sets are only
execution bookkeeping and must not be the sole record of durable work.

## Data durability

- PostgreSQL is the authoritative store for application records.
- Supabase or S3-compatible object storage is required for multi-replica
  production attachments; a persistent volume is acceptable only for a
  deliberately single-host deployment.
- Database and attachments have independent backups and retention policies.
- Restore drills verify that application, migration, and stored objects agree.
- Schema migrations favor backward-compatible expansion and controlled cleanup.

## Privacy erasure and audit retention

Account erasure is an explicit, irreversible authenticated operation. It must
complete storage-object deletion before mutating database records; a storage
failure returns an error so the user can retry rather than leaving a silently
partial erasure. Database identity is pseudonymised only to preserve foreign-key
integrity; direct profile, session, consent, memory, calendar, feedback and
attachment records are removed, and authored messages are withdrawn/blanked.

Audit logs are append-only operational evidence. Retain them for 365 days unless
the applicable legal policy requires a different period. They must contain only
opaque IDs and allowlisted metadata, never message bodies, email addresses,
credentials, request bodies, or object paths.

## Horizontal scaling boundaries

Redis coordinates WebSocket broadcasts and distributed rate limits. Reminder or
recovery schedulers require single-leader execution or idempotent claims when
multiple replicas run. Local files, semaphores, connection maps, and task sets are
process-local and cannot coordinate replicas.

## Required metrics and alerts

Monitor request rate, latency, error ratio, database pool wait, active WebSockets,
heartbeat timeouts, Redis publish failures, provider latency/errors, circuit state,
voice pending age, reminder lag, upload failures, and backup age.

## Distributed tracing

Set `OTEL_ENABLED=true` and configure an OTLP/HTTP traces endpoint to instrument
FastAPI requests, SQLAlchemy queries, and outbound HTTPX calls. Health probes are
excluded to avoid low-value spans. `OTEL_TRACE_SAMPLE_RATIO` controls head-based
sampling and defaults to `0.1`; parent sampling decisions are preserved across
service boundaries. Braintrust/Langfuse tracing remains separate because it
captures model workflow data rather than infrastructure spans.

The admin system-health response exposes only whether tracing is configured,
the service name, exporter kind, and sample ratio. Collector URLs, authorization
headers, query parameters, message content, and model prompts must not be added
to that response or span attributes.

Alert on symptoms that require action:

- readiness failure or sustained server errors;
- database pool exhaustion or lock accumulation;
- rapidly increasing stale WebSocket cleanup;
- pending voice operations older than the recovery objective;
- Redis fanout errors in a scaled deployment;
- provider circuit open beyond its expected recovery window;
- missing or failed backups.

## Degradation policy

| Dependency | Expected behavior when unavailable |
|---|---|
| PostgreSQL | readiness fails; reject durable operations safely |
| Redis | single-process local delivery may continue; scaled delivery is degraded |
| LLM/translation | preserve original text and return controlled provider status |
| Embeddings | disable semantic ranking; retain authorized non-semantic paths |
| STT | keep durable voice state retryable and expose failure status |
| Email | fail the email operation without exposing reset secrets |
| RTC provider | chat remains available; call creation returns a safe error |
| Object storage | reject new uploads; existing database records remain intact |

## Performance and recovery tests

- Concurrent REST and WebSocket traffic under realistic database pool limits.
- Multi-worker Redis fanout and reconnect synchronization.
- Provider timeout, retry, open-circuit, and recovery behavior.
- Process termination during voice transcription and startup recovery.
- Migration on a production-like dataset.
- Backup restore with attachment verification.

## Incident response

1. Confirm impact and affected features without exposing user content.
2. Check liveness, readiness, recent deploys, database saturation, Redis, and providers.
3. Reduce harm by disabling an optional feature, opening a circuit, or scaling safely.
4. Preserve logs, metrics, release SHA, and timestamps.
5. Restore service, verify critical flows, and monitor recurrence.
6. Document root cause, detection gap, corrective work, and owner.

## Release gate

A production release requires green CI, one Alembic head, successful migration,
frontend build, zero known production dependency vulnerabilities, current backup,
healthy readiness, and smoke tests for authentication, messaging, and WebSocket delivery.
