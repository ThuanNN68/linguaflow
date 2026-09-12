# Feature Operations Guide

This guide documents behavior implemented in the current application. It is an
operational companion to [API contract](../api/contract.md), which defines API payloads
and response shapes. Do not treat this document as a promise for unimplemented
features.

## Calls

Direct conversations support voice and video calls when the configured RTC
provider is available. Calls are not supported for group conversations. The
backend creates the provider room and short-lived participant access; provider
secrets and join tokens are never sent in messages or WebSocket broadcasts.

The lifecycle is `ringing`, then `accepted`, `rejected`, `missed`, `ended`, or
`failed`. A caller creates a call, the recipient accepts or rejects it, and either
participant may end it. Browser microphone and camera permission is local-device
state: a denied permission should let the user retry after changing browser or OS
settings rather than incorrectly ending the server-side call.

Verify with two accounts: accept and end a voice call, repeat for video, reject a
call, let a call time out, and confirm that blocked users and unavailable RTC
credentials produce safe API errors.

## Internal calendar and reminders

Calendar events are stored in LinguaFlow's database. The product does not connect
to, synchronize with, or create links in external calendar or conferencing
services. Users can create, edit, and cancel internal events. Assistant-generated
events remain proposals until approved.

`calendar_read` permits the assistant to read the internal calendar for planning;
`calendar_write` permits a confirmed assistant action to change it. A reminder is
stored durably in PostgreSQL. The scheduler scans due reminders, claims each row
atomically, writes a durable assistant notice, and only then records delivery.
Failed delivery releases the claim and retries with bounded exponential backoff.
This prevents false “delivered” state and repeated delivery after a restart.

Operations checklist:

- Confirm the scheduler appears healthy in the system health endpoint.
- Check event timezone and reminder timestamps when a reminder seems early or late.
- Test a past reminder, a dismissed reminder, a canceled event, an offline client,
  and a backend restart.
- Treat WebSocket toast delivery as best effort; the durable record is the source
  of truth.
- A sleeping free-tier backend can deliver overdue reminders after it wakes, but
  cannot guarantee minute-accurate delivery while no worker is running.

## Languages and translation

`interface_language` controls labels, menus, buttons, dates, statuses, and system
help. `preferred_language` controls the language into which incoming messages are
translated. They are intentionally independent. Changing the display language
must not rewrite user-authored content or silently alter the translation language.

New UI must use `frontend/src/features/chat/i18n.ts`; do not introduce hard-coded
labels in components. After changing language settings, refresh the client and
check chat, calendar, task inbox, details drawer, attachment menu, error state,
and authentication screens.

## Assistant consent, summaries, and proposals

Assistant data access is opt-in by scope. Conversation membership alone is not
consent. `read_conversations` allows an explicit assistant request or conversation
summary; `proactive_scan` additionally enables proposal detection; `store_memory`
allows durable assistant memory; calendar scopes are described above.

The summary action posts a short assistant request into the active conversation.
The assistant response is a normal persisted chat message rather than a separate
temporary panel. The application no longer exposes per-message ambiguity analysis
or a `clarify` message endpoint.

Calendar proposals must be reviewed before writes. A proposal may request missing
event fields during review; this is proposal completion, not a separate message
clarification feature. Rejecting a proposal must not create or modify an event.

For calendar requests, the assistant waits briefly for an immediately following
time or note, then examines bounded nearby context. Later corrections update the
matching pending proposal using the newest explicit value. A missing date or
start time disables approval until the reviewer supplies it. The proposal review
surface identifies the message that provided extracted time and details.

Test each consent separately, revoke it between requests, and verify that a user
cannot access another user's conversation, calendar, consent, or attachment.

## Files, voice, and message privacy

Attachment downloads require authentication and conversation membership. Voice
audio is not listed as a shared document in the conversation details drawer.
Transcripts participate in search and assistant context only after successful
processing. A successful voice transcript may therefore create or update an
assistant proposal exactly as typed text can. The message's browser timezone is
used when resolving relative dates. A failed transcription can be retried through
the supported message workflow.

Saved-message state is personal and is not broadcast. Reactions are scoped to
conversation members. Forwarding creates a new message in the destination and
does not transfer the original message's visibility permissions.

## Health and incident response

`/health/live` confirms process liveness; `/health` and `/health/ready` confirm
application readiness and database connectivity. The administrator-only system
health endpoint reports operational signals without returning chat text or tokens.

For an incident, first check the health endpoint, recent server logs, database
connectivity, rate-limit status, provider quota, and WebSocket origin settings.
Do not restart repeatedly to bypass a circuit breaker; fix the upstream provider,
credentials, or configuration and let the breaker recover normally.
