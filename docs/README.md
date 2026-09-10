# LinguaFlow documentation

This directory contains the maintained product and operations documentation.
Historical reports, generated artifacts, and temporary notes do not belong here.

## Architecture

- [System diagrams](architecture/diagrams.md) covers components, agent flow,
  data flow, the entity model, and important request sequences.
- [Assistant and admin design](architecture/assistant-admin.md) documents
  authorization boundaries, human review, and glossary/correction workflows.
- [Architecture decisions](../ARCHITECTURE.md) is the primary source for system
  boundaries and architectural decisions.

## API and realtime behavior

- [API and data contract](api/contract.md) describes REST resources, WebSocket
  events, shared schemas, and standard errors.
- [API versioning policy](api/versioning.md) defines compatibility, deprecation,
  sunset, and WebSocket evolution rules.
- [WebSocket reconnect contract](api/websocket-reconnect.md) covers
  authentication, heartbeat, resend, idempotency, and message recovery.

## Operations

- [Deployment](operations/deployment.md) covers free-tier hosting and the VPS
  production topology.
- [Environments](operations/environments.md) records public URLs and health endpoints.
- [Feature runbooks](operations/features.md) covers RTC, calendar, assistant,
  attachment, and voice operations.
- [Runtime reliability](operations/reliability.md) covers rate limits, cache,
  circuit breakers, backups, and incident response.

## Documentation rules

- Update `api/contract.md` after changing a REST route, WebSocket event, or DTO.
- Apply `api/versioning.md` before making a breaking or deprecating API change.
- Update `ARCHITECTURE.md` and relevant diagrams after changing topology,
  dependencies, or an architectural decision.
- Update deployment documentation after adding environment variables or release steps.
- Update runbooks after adding recovery behavior, alerts, or failure modes.
- Keep internal links relative and verify them before committing.
