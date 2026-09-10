#!/usr/bin/env sh

# Writes last-known-good only after fresh public verification and running-image
# identity verification succeed. deploy_release.sh deliberately never invokes it.

set -eu

LKG_FILE=${LINGUAFLOW_LKG_FILE:-/opt/linguaflow/state/last-known-good-sha}
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
RUNTIME_ENV_FILE=/etc/linguaflow/production.env
COMPOSE_PROJECT=linguaflow
COMPOSE_FILE=docker-compose.production.yml
BACKEND_IMAGE=${LINGUAFLOW_BACKEND_IMAGE:-ghcr.io/linguaflow/linguaflow-backend}
FRONTEND_IMAGE=${LINGUAFLOW_FRONTEND_IMAGE:-ghcr.io/linguaflow/linguaflow-frontend}

fail() {
  printf '%s\n' "finalize_release: $*" >&2
  exit 1
}

usage() {
  printf '%s\n' 'Usage: scripts/deployment/finalize_release.sh [--dry-run] RELEASE_SHA' >&2
  exit 2
}

normalize_sha() {
  value=$1
  case "$value" in
    *[!0123456789abcdefABCDEF]* | '') return 1 ;;
  esac
  [ "${#value}" -eq 40 ] || return 1
  printf '%s\n' "$value" | tr '[:upper:]' '[:lower:]'
}

dry_run=false
if [ "${1:-}" = '--dry-run' ]; then
  dry_run=true
  shift
fi

[ "$#" -eq 1 ] || usage
RELEASE_SHA=$(normalize_sha "$1") || fail 'RELEASE_SHA must be exactly 40 hexadecimal characters'
EXPECTED_BACKEND_IMAGE=$BACKEND_IMAGE:$RELEASE_SHA
EXPECTED_FRONTEND_IMAGE=$FRONTEND_IMAGE:$RELEASE_SHA

cd "$REPO_ROOT" || fail 'cannot enter repository root'

compose() {
  RELEASE_SHA=$RELEASE_SHA docker compose \
    --env-file "$RUNTIME_ENV_FILE" \
    -p "$COMPOSE_PROJECT" \
    -f "$COMPOSE_FILE" \
    "$@"
}

resolve_running_container() {
  service=$1
  container_ids=$(compose ps --status running -q "$service") \
    || fail "cannot resolve the running $service container"
  [ -n "$container_ids" ] || fail "no running $service container was found"

  container_count=$(printf '%s\n' "$container_ids" | wc -l | tr -d '[:space:]')
  [ "$container_count" = 1 ] \
    || fail "expected exactly one running $service container"
  printf '%s\n' "$container_ids"
}

verify_running_image() {
  service=$1
  expected_image=$2
  container_id=$(resolve_running_container "$service")
  actual_image=$(docker inspect --format '{{.Config.Image}}' "$container_id") \
    || fail "cannot inspect the running $service container"
  [ "$actual_image" = "$expected_image" ] \
    || fail "running $service image does not match the requested release SHA"
}

if [ "$dry_run" = true ]; then
  printf '%s\n' 'DRY-RUN finalize: re-run public verification'
  printf '%s\n' "DRY-RUN finalize: resolve one running backend container and require image $EXPECTED_BACKEND_IMAGE"
  printf '%s\n' "DRY-RUN finalize: resolve one running frontend container and require image $EXPECTED_FRONTEND_IMAGE"
  printf '%s\n' "DRY-RUN finalize: only then write $RELEASE_SHA to $LKG_FILE"
  exit 0
fi

if ! sh "$REPO_ROOT/scripts/deployment/verify_production.sh"; then
  fail 'public verification did not pass; last-known-good state remains unchanged'
fi

verify_running_image backend "$EXPECTED_BACKEND_IMAGE"
verify_running_image frontend "$EXPECTED_FRONTEND_IMAGE"

state_dir=$(dirname "$LKG_FILE")
umask 077
mkdir -p "$state_dir" || fail "cannot create state directory: $state_dir"
tmp_file=$(mktemp "$state_dir/.last-known-good-sha.XXXXXX") || fail 'cannot allocate state file'

cleanup() {
  if [ -n "${tmp_file:-}" ] && [ -f "$tmp_file" ]; then
    rm -f "$tmp_file"
  fi
}
trap cleanup EXIT HUP INT TERM

printf '%s\n' "$RELEASE_SHA" >"$tmp_file"
mv "$tmp_file" "$LKG_FILE" || fail 'cannot finalize last-known-good state'
tmp_file=
printf '%s\n' "Last-known-good release recorded: $RELEASE_SHA"
