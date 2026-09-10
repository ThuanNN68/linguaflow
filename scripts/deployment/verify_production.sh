#!/usr/bin/env sh

# Performs bounded, public post-rollout checks. It is independently runnable
# from any host with curl and never reads the VPS runtime environment.

set -eu

API_URL=${LINGUAFLOW_API_URL:-https://api-c3-lingua-flow-217.dquangminh2003.id.vn/health}
FRONTEND_URL=${LINGUAFLOW_FRONTEND_URL:-https://c3-lingua-flow-217.dquangminh2003.id.vn}
VERIFY_ATTEMPTS=${VERIFY_ATTEMPTS:-12}
VERIFY_DELAY_SECONDS=${VERIFY_DELAY_SECONDS:-15}
VERIFY_TIMEOUT_SECONDS=${VERIFY_TIMEOUT_SECONDS:-10}

fail() {
  printf '%s\n' "verify_production: $*" >&2
  exit 1
}

usage() {
  printf '%s\n' 'Usage: scripts/deployment/verify_production.sh [--dry-run]' >&2
  exit 2
}

is_positive_integer() {
  case "$1" in
    '' | *[!0123456789]*) return 1 ;;
  esac
  [ "$1" -gt 0 ] 2>/dev/null
}

dry_run=false
if [ "${1:-}" = '--dry-run' ]; then
  dry_run=true
  shift
fi
[ "$#" -eq 0 ] || usage

is_positive_integer "$VERIFY_ATTEMPTS" || fail 'VERIFY_ATTEMPTS must be a positive integer'
is_positive_integer "$VERIFY_DELAY_SECONDS" || fail 'VERIFY_DELAY_SECONDS must be a positive integer'
is_positive_integer "$VERIFY_TIMEOUT_SECONDS" || fail 'VERIFY_TIMEOUT_SECONDS must be a positive integer'

if [ "$dry_run" = true ]; then
  printf '%s\n' "DRY-RUN verify: API $API_URL must return HTTP 200 JSON with service=LinguaFlow API and database=ok"
  printf '%s\n' "DRY-RUN verify: frontend $FRONTEND_URL must return final HTTP 200 with LinguaFlow marker"
  printf '%s\n' "DRY-RUN verify: at most $VERIFY_ATTEMPTS attempts, ${VERIFY_TIMEOUT_SECONDS}s request timeout, ${VERIFY_DELAY_SECONDS}s delay"
  exit 0
fi

api_body=$(mktemp) || fail 'cannot allocate API response file'
frontend_body=$(mktemp) || fail 'cannot allocate frontend response file'

cleanup() {
  rm -f "$api_body" "$frontend_body"
}
trap cleanup EXIT HUP INT TERM

request_status() {
  url=$1
  body=$2
  status=$(curl --silent --show-error --location \
    --connect-timeout "$VERIFY_TIMEOUT_SECONDS" \
    --max-time "$VERIFY_TIMEOUT_SECONDS" \
    --output "$body" \
    --write-out '%{http_code}' \
    "$url") || status=000
  printf '%s\n' "$status"
}

attempt=1
while [ "$attempt" -le "$VERIFY_ATTEMPTS" ]; do
  api_status=$(request_status "$API_URL" "$api_body")
  frontend_status=$(request_status "$FRONTEND_URL" "$frontend_body")

  if [ "$api_status" = 200 ] && [ "$frontend_status" = 200 ] \
    && grep -Eq '"service"[[:space:]]*:[[:space:]]*"LinguaFlow API"' "$api_body" \
    && grep -Eq '"database"[[:space:]]*:[[:space:]]*"ok"' "$api_body" \
    && grep -Fq 'LinguaFlow' "$frontend_body"; then
    printf '%s\n' "Production verification passed on attempt $attempt/$VERIFY_ATTEMPTS."
    exit 0
  fi

  printf '%s\n' "Verification attempt $attempt/$VERIFY_ATTEMPTS did not pass (API HTTP $api_status, frontend HTTP $frontend_status)." >&2
  if [ "$attempt" -lt "$VERIFY_ATTEMPTS" ]; then
    sleep "$VERIFY_DELAY_SECONDS"
  fi
  attempt=$((attempt + 1))
done

fail "public verification failed after $VERIFY_ATTEMPTS bounded attempts"
