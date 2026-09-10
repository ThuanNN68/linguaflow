# Service domains

The service layer is grouped by business capability. API routes and agents may
depend on these packages; service packages must not import API route modules.

| Package | Responsibility |
|---|---|
| `assistant/` | Assistant consent, orchestration, retrieval, memory and indexing |
| `calendar/` | Internal events and durable reminders |
| `identity/` | Email, Google identity, user profiles and settings |
| `intelligence/` | Conversation analysis, commitments and action proposals |
| `language/` | Translation, corrections, glossary and customization |
| `messaging/` | Chat, realtime connections, attachments, visibility and calls |
| `voice/` | Audio validation/conversion, STT, TTS and voice-message lifecycle |
| `shared/` | LLM, embeddings, semantic search, pricing and operational metrics |

New modules should be placed with the domain that owns their business rules.
Only genuinely cross-domain infrastructure belongs in `shared/`.
