# API and data contract

This document defines stable public behavior for the LinguaFlow REST and
WebSocket interfaces. The generated OpenAPI document is authoritative for the
complete route list and field-level schemas; this document records cross-route
semantics and compatibility rules.

## Scope and versioning

The complete compatibility and sunset process is defined in
[API versioning and deprecation policy](versioning.md).

- Public application routes use the `/api/v1` prefix.
- Additive optional fields are backward compatible.
- Removing, renaming, or changing the meaning of a field is a breaking change.
- WebSocket event names and acknowledgement behavior are versioned contracts.
- Database models are internal and must not be serialized directly.

## Authentication and authorization

REST requests use the configured JWT authentication flow. WebSocket connections
authenticate during connection establishment. Authentication identifies the
caller; every operation also validates ownership, role, conversation membership,
message visibility, and consent as applicable.

Never place access tokens in logs, error messages, analytics payloads, or public URLs.

## Security audit and account erasure

Security-sensitive actions create append-only audit events: message withdrawal,
group role/ownership changes, assistant-consent changes, and account erasure.
Events contain opaque actor/resource identifiers and allowlisted operational
metadata only; they never contain message text, credentials, attachments, or
model prompts.

`DELETE /api/v1/auth/me` requires `{ "confirmation": "DELETE" }` and, for a
password account, its current password. It revokes sessions, deletes direct
personal state and uploaded objects, withdraws the user's messages, and replaces
the account identity with an inert tombstone so other participants' conversation
history retains referential integrity. The operation is irreversible.

## Common conventions

- IDs are opaque and must not be interpreted by clients.
- Timestamps are UTC ISO 8601 strings.
- Pagination uses explicit limit and cursor parameters where available.
- Empty collections are returned as `[]`, not `null`.
- Error responses contain a stable machine-readable code and a safe message.
- Clients must ignore unknown additive response fields and WebSocket metadata.

## Language fields

`preferred_language` controls translation preference. `interface_language`
controls application UI language. Both use supported application language codes;
they are not interchangeable. Before authentication, the frontend may use local
browser preference, but the authenticated profile remains authoritative.

## Core resource families

| Area | Responsibilities |
|---|---|
| Authentication | registration, login, identity, password reset, Google identity |
| Users and profiles | preferences, language, avatar, settings |
| Conversations | direct/group creation, membership, roles, previews, unread state |
| Messages | create, edit, delete, reply, react, forward, search, save |
| Attachments and voice | validated upload, playback metadata, transcription lifecycle |
| Translation | translated variants, versions, retry, correction feedback |
| Assistant | consent, private messages, retrieval, summaries, tool proposals |
| Calendar and tasks | internal events, reminders, proposals, decisions |
| Administration | metrics, feedback, glossary, users, suggestions, health |

## Message representation

A message response keeps the original content and may include recipient-specific
translation, attachment, voice, reply, reaction, mention, forwarding, visibility,
and lifecycle metadata. The original content is never overwritten by a translation.

Clients must render lifecycle states explicitly: a voice message can remain
pending or processing before a transcript exists, and translation can be absent,
pending, completed, failed, or retried.

## Text-to-speech playback

`GET /api/v1/messages/{message_id}/tts` returns authorized text and language
metadata for browser-native speech. When the browser cannot synthesize speech,
`GET /api/v1/messages/{message_id}/tts/audio` returns authenticated MP3 audio if
the optional backend provider is enabled. Both routes apply the same membership,
message-visibility, deletion, and translation-selection rules. Generated audio
is private, is not cached by shared intermediaries, and the provider's raw error
details are never returned to the client.

## Attachments and direct downloads

The normal authenticated download remains available for local and Supabase
storage. `POST /conversations/{conversation_id}/attachments/{attachment_id}/presign`
first applies the same membership and private-message visibility checks, then
returns a short-lived S3-compatible URL. It returns `409` when the configured
backend cannot issue direct URLs. Clients fall back to the authenticated proxy;
they must never cache a presigned URL beyond its returned lifetime.

## Push subscriptions

Authenticated clients read `/push/vapid-public-key`, then upsert or delete their
own `/push/subscriptions`. Subscriptions are scoped to the authenticated user.
The server sends only a short notification preview and chat URL, and removes
provider endpoints that return `404` or `410`.

## Administration and outbound webhooks

Administrator-only user routes list accounts, suspend/unsuspend accounts, and
send the normal single-use password-reset email. Suspension revokes refresh
sessions immediately and prevents access-token and WebSocket authentication.
Admins cannot suspend themselves; every action is written to the audit log.

Administrator-only webhook routes create/list/delete endpoints and inspect the
last 100 deliveries. Supported event names are bounded by the server. Secrets
are returned once, payloads are HMAC-SHA256 signed, redirects are not followed,
private-network targets are rejected by default, and delivery state survives a
restart for bounded exponential retry.

## WebSocket connection contract

1. Connect to the versioned WebSocket route with valid authentication.
2. The server validates origin, token, and user state.
3. Client events are validated before database access.
4. Durable operations are committed before success acknowledgement or broadcast.
5. The server emits recipient-authorized payloads only.
6. Application heartbeat detects stale connections.
7. Clients reconnect with backoff and resynchronize REST state after interruption.

## WebSocket event families

| Family | Direction | Purpose |
|---|---|---|
| connection / heartbeat | both | readiness, ping/pong, idle detection |
| message | client → server | create durable chat messages |
| acknowledgement | server → client | correlate accepted or rejected client operations |
| message updates | server → client | create, edit, delete, reaction, translation, voice state |
| presence / typing | both | ephemeral collaboration state |
| call signaling | both | authorized RTC coordination |
| error | server → client | stable code, safe message, optional correlation ID |

Clients should attach a unique operation or client-message identifier to retryable
mutations. The server treats duplicates idempotently where the event contract
supports it. After reconnect, clients fetch current durable state rather than
assuming all broadcasts were received.

## Standard error semantics

| HTTP status | Meaning |
|---|---|
| `400` | malformed or semantically invalid request |
| `401` | missing, invalid, or expired authentication |
| `403` | authenticated but not authorized |
| `404` | resource absent or intentionally hidden from the caller |
| `409` | state conflict or duplicate operation |
| `413` | upload exceeds the configured limit |
| `422` | schema validation failed |
| `429` | rate limit exceeded |
| `503` | required dependency unavailable or service not ready |

Provider-specific error messages are never exposed directly. Retryability is
communicated through stable application codes and, when appropriate, `Retry-After`.

## Data and privacy invariants

- Conversation membership gates message and attachment access.
- Private assistant content is not broadcast to group members.
- Soft-deleted content is omitted from normal reads and retrieval.
- Model retrieval applies authorization filters before similarity ranking.
- Admin role alone does not grant unrestricted private-content access.
- Side-effect proposals require user approval and server-side revalidation.

## Change checklist

Before merging a public contract change:

- Update Pydantic/TypeScript schemas and this document.
- Add success, validation, authorization, and failure-path tests.
- Add an Alembic migration for persistence changes.
- Verify reconnect and idempotency for WebSocket mutations.
- Document user-visible behavior in `README.md` and the relevant feature guide.
- Regenerate or inspect OpenAPI and confirm no accidental breaking changes.

## Related documentation

- [WebSocket reconnect contract](websocket-reconnect.md)
- [Architecture](../../ARCHITECTURE.md)
- [Runtime reliability](../operations/reliability.md)
- [Public OpenAPI](https://api-c3-lingua-flow-217.dquangminh2003.id.vn/docs)
