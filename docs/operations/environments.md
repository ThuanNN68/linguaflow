# Deployment URLs and operational endpoints

This page records the public addresses of the LinguaFlow evaluation environment.
Always verify status with health checks; DNS resolution alone does not prove health.

## Current environment

| Service | URL |
|---|---|
| Frontend | <https://c3-lingua-flow-217.dquangminh2003.id.vn> |
| Backend API | <https://api-c3-lingua-flow-217.dquangminh2003.id.vn> |
| Liveness | <https://api-c3-lingua-flow-217.dquangminh2003.id.vn/health/live> |
| Readiness | <https://api-c3-lingua-flow-217.dquangminh2003.id.vn/health/ready> |
| OpenAPI | <https://api-c3-lingua-flow-217.dquangminh2003.id.vn/docs> |

Do not publish sample credentials. Create short-lived evaluation accounts, share
credentials privately, and revoke them afterward.

## Health-check semantics

- `/health/live` confirms that the API process is running.
- `/health/ready` confirms that the instance and dependencies can accept traffic.
- `/health` remains for hosting-platform compatibility. Prefer the dedicated endpoints.

## Manual verification

```bash
curl --fail --show-error --silent \
  https://api-c3-lingua-flow-217.dquangminh2003.id.vn/health/live

curl --fail --show-error --silent \
  https://api-c3-lingua-flow-217.dquangminh2003.id.vn/health/ready
```

After health checks pass, verify login, one REST request, and a real WebSocket
connection. Liveness does not guarantee SMTP, LLM, STT, RTC, or storage quota.

## Changing domains

Update DNS and reverse-proxy TLS, backend origins, `NEXT_PUBLIC_API_URL`, Google
OAuth origins when enabled, monitoring, and this page. Rebuild the frontend after
changing any `NEXT_PUBLIC_*` value.

See [deployment.md](deployment.md) and [features.md](features.md) for full runbooks.
