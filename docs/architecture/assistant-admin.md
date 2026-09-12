# Assistant and administration design

This document defines the trust and authorization boundaries for the private
assistant and administration features.

## Design goals

- Help users understand and act on conversations without exposing unrelated data.
- Keep model output advisory until deterministic application code validates it.
- Require explicit consent before assistant retrieval or analysis.
- Separate platform administration metadata from private conversation content.
- Preserve provenance and review state for every proposed action.

## Assistant entry points

The assistant can be invoked in a private assistant conversation or through an
explicit mention in an authorized conversation. It may answer questions,
summarize permitted context, retrieve relevant messages, and propose internal
calendar or task actions. It cannot bypass membership, consent, or approval.

## Trust boundary

```text
User request
  → authentication and membership
  → consent and scope checks
  → permitted retrieval
  → guarded model workflow
  → structured output validation
  → response or persisted proposal
  → explicit human approval for side effects
```

Prompts, retrieved text, and model responses are untrusted data. Tool names,
arguments, resource identifiers, and side effects are validated against an
application-owned allowlist.

## Consent

Consent records are versioned by policy and scope. A missing, revoked, expired,
or outdated consent blocks assistant access. Consent does not expand the user's
underlying authorization: a user can only grant access to data they may already read.

## Retrieval and memory

- Retrieval filters by user, conversation membership, visibility, and deletion state.
- pgvector similarity is a ranking mechanism, never an authorization mechanism.
- Private assistant messages remain visible only to their owner.
- Stored memory is minimal, attributable, and removable through application rules.
- Retrieval telemetry records identifiers and timing, not private message bodies.

## Action proposals

Assistant-detected commitments, meetings, reminders, or tasks are stored as
proposals with source references, structured fields, confidence, status, and
timestamps. A user can approve, edit, or dismiss a proposal. Only approved and
revalidated proposals create internal calendar or task records.

Calendar proposal processing is deliberately stateful:

- An assistant request is first persisted as a durable job. A restart can resume
  pending or interrupted work; an in-memory task is only the current executor.
- A short context-settling window lets a follow-up such as a time or note join
  the original request before a proposal is created.
- A later amendment updates the newest matching pending proposal rather than
  creating a duplicate. If several pending proposals are plausible and no
  title identifies one, the assistant asks the user to clarify.
- The newest explicit date, time, location, and note wins over older context.
  Proposal fields retain their source-message references so review can show
  where each value came from.
- A proposal with no resolved date and start time cannot be approved. The user
  can complete those fields in the review form before approval.

Voice transcripts enter the same authorized assistant path after transcription
has completed. The browser timezone captured with the voice message is retained
for relative expressions such as “tomorrow at 10 pm”.

## Administration boundary

Administrators may access operational metrics, account state, provider health,
translation feedback, glossary proposals, and moderation workflows exposed by
admin APIs. The admin role does not imply unrestricted access to private message
content. Any exceptional content-access workflow must be separately authorized,
audited, and documented.

## Glossary workflow

Correction signals may produce glossary proposals. Automated mining cannot
publish glossary entries directly. An administrator reviews source evidence,
language pair, term, replacement, scope, and conflicts before approval.

## Failure handling

- Provider timeout: record the attempt and return a controlled retryable status.
- Invalid structured output: reject it without applying side effects.
- Retrieval unavailable: answer without private context or return a clear error.
- Process restart: recover pending assistant jobs and expired work leases.
- Revoked consent: block new work immediately; existing stored data follows the
  documented retention and deletion policy.

## Required tests

- Cross-user and cross-conversation retrieval denial.
- Private assistant visibility and group-message visibility.
- Consent grant, policy-version change, revocation, and expiry.
- Prompt injection and malformed tool arguments.
- Proposal approve, edit, dismiss, duplicate, and stale-source behavior.
- Admin metadata access without implicit private-content access.
- Provider timeouts, retries, circuit breakers, and process recovery.

## Observability

Track request IDs, actor IDs, operation type, provider, latency, token or cost
metadata, proposal state, and categorized failure reason. Never log secrets,
raw credentials, or private message bodies.
