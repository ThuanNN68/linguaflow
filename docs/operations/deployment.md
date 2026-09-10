# Deploying LinguaFlow

This runbook covers an evaluation/free-tier deployment and the production VPS
baseline. Never commit deployment credentials or copy development secrets into production.

## Deployment topology

```text
Internet
  → DNS and TLS
  → Next.js frontend
  → FastAPI backend
      ├─ PostgreSQL + pgvector
      ├─ Redis for multi-worker WebSocket fanout
      ├─ durable object storage
      └─ LLM, STT, email, and RTC providers
```

## Prerequisites

- A PostgreSQL 16 database with the `vector` and `pg_trgm` extensions.
- A frontend host and a backend host or a Docker-capable VPS.
- TLS-enabled public frontend and API domains.
- Provider credentials for each enabled optional feature.
- Durable attachment storage for production.
- Redis before running more than one backend worker or replica.

## Required configuration

Start from `.env.example`. At minimum, configure `APP_ENV=production`, a newly
generated `JWT_SECRET`, `DATABASE_URL`, `FRONTEND_URL`,
`PUBLIC_FRONTEND_ORIGIN`, `CORS_ORIGINS`, and the selected provider credentials.

For attachments, set `ATTACHMENT_STORAGE_BACKEND=s3` with `S3_BUCKET` and the
appropriate region, endpoint, and credentials, or select `supabase`. AWS S3,
Cloudflare R2, MinIO, and other S3-compatible services share the same backend;
use `S3_ENDPOINT_URL` for non-AWS services and `S3_FORCE_PATH_STYLE=true` when
the provider requires path-style addressing. Do not use container-local storage
when more than one backend replica may serve requests.

`REDIS_URL` backs both cross-process WebSocket delivery and distributed rate
limits. Tune `DATABASE_POOL_SIZE` and `DATABASE_MAX_OVERFLOW` per backend
process so their combined maximum remains below the database connection limit.
Server-side TTS is optional. Select `openai`, `elevenlabs`, or `google` and set
the matching key plus provider-appropriate `TTS_MODEL` and `TTS_VOICE`.

For background PWA notifications, configure the same VAPID public/private key
pair on every backend replica. The public key is returned only to authenticated
clients; the private key never enters `NEXT_PUBLIC_*`. Browser permission is
requested only after the user enables Push notifications in Settings.

Outbound webhook endpoints are created by administrators through the API. A
random secret is shown once at creation; receivers must verify
`X-LinguaFlow-Timestamp` and the HMAC-SHA256 `X-LinguaFlow-Signature` over
`<timestamp>.<raw-body>`, reject stale timestamps, and deduplicate by
`X-LinguaFlow-Delivery`. Production accepts HTTPS targets and rejects private,
loopback, link-local, and otherwise non-global addresses unless the explicit
private-network escape hatch is enabled for a controlled internal deployment.

Treat all `NEXT_PUBLIC_*` values as public build-time values. Never put a secret
in them. Production builds fail when `NEXT_PUBLIC_API_URL` is missing or invalid.
Rebuild the frontend after changing the API URL or OAuth client ID.

## Database preparation

Use a dedicated application role, TLS, private networking where possible, and a
backup policy. Apply migrations exactly once before serving a new revision:

```bash
python -m alembic heads
python -m alembic upgrade head
python -m alembic check
```

There must be one Alembic head. Do not use `create_all`, manual table edits, or
database deletion as a production migration strategy.

## Free-tier evaluation deployment

1. Provision managed PostgreSQL/pgvector and object storage.
2. Deploy the FastAPI image or Dockerfile to the backend platform.
3. Set production environment variables in the platform secret store.
4. Run migrations as a pre-deploy or release command.
5. Deploy the frontend with the public API URL embedded at build time.
6. Configure HTTPS origins consistently in frontend, backend, and OAuth settings.
7. Verify liveness, readiness, login, REST, WebSocket, upload, and one AI operation.

Free tiers may sleep, cold-start, limit outbound connections, or cap AI and email
quota. They are suitable for portfolio evaluation, not availability guarantees.

## VPS deployment

The production Compose file and Caddy configuration provide the self-hosted
baseline. Store the production environment file outside the repository and
restrict its filesystem permissions.

The controlled script chain under `scripts/deployment/` performs backup,
immutable image deployment, migration, verification, and last-known-good updates.
Read [scripts/README.md](../../scripts/README.md) before using it.

## Release sequence

1. Confirm CI is green for the exact commit.
2. Build immutable backend and frontend images labeled with the commit SHA.
3. Back up PostgreSQL and record attachment-storage recovery state.
4. Pull images by digest, not by a mutable tag.
5. Run migrations.
6. Start services and wait for readiness.
7. Run public smoke tests and verify the deployed image identity.
8. Mark the revision last-known-good only after verification succeeds.

## Post-deployment verification

- `/health/live` returns success.
- `/health/ready` confirms required dependencies.
- Registration/login and password recovery behave as configured.
- A REST message and WebSocket message reach the intended recipient.
- Translation, voice/STT, assistant, email, RTC, and storage work when enabled.
- S3 attachment download uses an authorized, short-lived presigned URL.
- Background Web Push reaches an opted-in browser and an expired subscription is removed.
- A test webhook receives a valid signature; failed delivery appears in the admin delivery log and retries.
- Redis fanout works across two backend workers before horizontal scaling.
- Logs contain request correlation without secrets or message bodies.
- When `OTEL_ENABLED=true`, HTTP, SQLAlchemy, and outbound HTTP spans reach the
  configured OTLP collector and appear under `OTEL_SERVICE_NAME`.

## Backup and rollback

Back up database and attachments independently. Periodically restore both into a
non-production environment. Application rollback does not imply database rollback:
prefer forward-compatible migrations and a forward fix. Restore the database only
with an explicit incident decision and verified backup.

## Troubleshooting

| Symptom | First checks |
|---|---|
| API not ready | database URL/TLS, migrations, dependency health |
| Browser CORS failure | frontend origin, backend CORS list, public API URL |
| WebSocket disconnects | WSS URL, proxy upgrade headers, origin, heartbeat |
| Missing cross-worker messages | shared Redis URL and channel configuration |
| Upload disappears | object storage configuration and persistent volume |
| AI/voice failure | provider key, model name, timeout, quota, circuit breaker |
| Email missing | provider mode, SMTP TLS, sender authorization, logs |
| Traces missing | `OTEL_ENABLED`, OTLP `/v1/traces` endpoint, auth headers, collector logs |

See [environments.md](environments.md), [features.md](features.md), and
[reliability.md](reliability.md) for operational details.
