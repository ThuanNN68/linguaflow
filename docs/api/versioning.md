# API versioning and deprecation policy

LinguaFlow versions public HTTP routes by major version in the path. The current
stable contract is `/api/v1`. OpenAPI describes the exact current schema; this
policy defines how that schema may evolve.

## Compatibility within a major version

The following changes may ship without a new path version:

- adding an optional request field or a response field;
- adding a route, WebSocket event, enum value documented as extensible, or an
  optional capability;
- relaxing validation while preserving the meaning of previously valid input;
- fixing behavior that contradicted the documented contract.

Clients must ignore unknown response fields and WebSocket metadata. Servers must
continue accepting omitted optional fields. A field removal, rename, type change,
new required field, semantic change, or changed authorization boundary requires a
new major path such as `/api/v2`.

## Deprecation lifecycle

1. Mark the affected operation deprecated in OpenAPI and the API documentation.
2. Return `Deprecation: true`, a standards-based `Sunset` HTTP date, and a
   `Link` header with `rel="deprecation"` from the retiring HTTP operation.
3. Keep a stable public major version available for at least 90 days after the
   announcement. A security or legal emergency may shorten this period and must
   be called out in release notes.
4. Remove the operation only after its sunset date and after replacement usage
   has been observed in staging.

No endpoint is currently deprecated. Deprecation headers are therefore not added
globally; they belong only on an operation with an announced sunset.

## WebSocket compatibility

The WebSocket handshake remains under the same major prefix as REST. Existing
event names, required fields, acknowledgement semantics, and idempotency rules
cannot change within a major version. New event types and optional fields are
additive. A client receiving an unknown event must ignore it safely and continue
processing the connection.

## Database and release versions

Database migration revisions are internal and do not define API versions. The
application release version may advance independently while `/api/v1` remains
compatible. Breaking API work must run side-by-side under a new prefix during
the migration window rather than silently changing `/api/v1`.

## Review checklist

Every API pull request must answer:

- Is the change additive for existing clients?
- Does OpenAPI and `docs/api/contract.md` match the implementation?
- Do WebSocket reconnect and idempotency guarantees still hold?
- If breaking, where is the new major route and migration guidance?
- If deprecated, are OpenAPI metadata, headers, replacement link, announcement,
  and sunset date present?
