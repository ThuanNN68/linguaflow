# WebSocket reconnect contract

## Connection Failure Handling

### Network / Server Drop

- Retry with exponential backoff: 1s → 2s → 4s → 8s → cap at 30s
- On each reconnect, open a new WebSocket and authenticate again

### Authentication Failure (auth_ok never received after auth frame)

- **Stop the reconnect loop immediately**
- Refresh credentials or prompt user to re-login
- Do NOT continue retrying with the same token

### Business Errors (conversation_not_found, not_conversation_member, client_message_id_conflict, etc.)

- These are application errors, not connection failures
- The socket remains usable after business errors
- Continue retrying if appropriate for the use case (but handle conflict appropriately)

## Reconnection Sequence

1. Connection lost
2. Retry with backoff: 1s → 2s → 4s → 8s → cap 30s
3. Open new WebSocket: `WS /api/v1/ws`
4. Send auth: `{ "type": "auth", "token": "<jwt>" }`
5. Wait for: `{ "type": "auth_ok", "user_id": "..." }`
   - If auth error: stop retry and refresh credentials
6. For each conversation: `GET /api/v1/conversations/{id}/messages?limit=50`
7. Deduplicate using server-assigned `message.id`
8. Resend pending messages with the SAME `client_message_id`

## Message Recovery

### Missed Messages

After reconnect, fetch history: `GET /api/v1/conversations/{id}/messages?limit=50`

- Deduplicate using **server `message.id`** (the canonical identifier assigned on persist)
- **Maximum 100 messages per request**; newest messages returned last

### Pending Message Resend

- Store `client_message_id` locally for messages without `message_created` acknowledgment
- After reconnect/auth, resend with the SAME `client_message_id`
- Same ID + same text → returns existing `message_created` (no duplicate fanout)
- Same ID + different text → returns `client_message_id_conflict` error
- `client_message_id` also lets the client reconcile a locally pending message with its canonical persisted server message

### Idempotency Key

`(sender_id, conversation_id, client_message_id)`

### ID Roles Summary

| ID | Owner | Purpose |
|----|-------|---------|
| `message.id` | Server | Canonical identifier for persisted/history messages; use for deduplication |
| `client_message_id` | Client | Correlates local pending sends, retries, and acknowledgements; idempotency key |

## MVP Limitation

Message history is bounded to a recent window (max 100 messages per request).
This mechanism does **NOT** guarantee recovery of arbitrarily large backlogs
after a long offline period.

Cursor/pagination/replay infrastructure is out of scope for MVP.
If a user is offline for an extended period, older messages beyond the 100-message
window cannot be recovered through the reconnect mechanism.

## WebSocket Events

### Endpoint

`WS /api/v1/ws`

### Client → Server

```json
{ "type": "auth", "token": "<jwt>" }
{ "type": "send_message", "client_message_id": "...", "conversation_id": "...", "text": "..." }
```

### Server → Client

```json
{ "type": "auth_ok", "user_id": "..." }
{ "type": "message_created", "client_message_id": "...", "message": { "id": "...", "conversation_id": "...", "sender_id": "...", "original_text": "...", "created_at": "..." } }
{ "type": "message_received", "message": { ... } }
{ "type": "error", "code": "...", "message": "..." }
```

### Error Codes

| Code | Meaning | Retry? |
|------|---------|--------|
| `authentication_required` | Auth frame not sent | Stop, refresh auth |
| `authentication_failed` | Invalid/expired token | Stop, refresh auth |
| `authentication_timeout` | Auth not received in time | Retry connect |
| `conversation_not_found` | Invalid conversation ID | Check conversation list |
| `not_conversation_member` | User not in conversation | Check membership |
| `client_message_id_conflict` | Same ID, different text | Handle conflict |
| `internal_error` | Server error | Retry may help |
| `invalid_event` | Malformed event | Fix event format |
