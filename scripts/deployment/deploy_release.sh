#!/usr/bin/env sh

# Production release coordinator. This is intended to run only on the VPS
# after a CI job has authenticated to GHCR and transferred the exact SHA.
# It does not log in to a registry, inspect secrets, roll back, restore a
# database, or write last-known-good state.

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
RUNTIME_ENV_FILE=/etc/linguaflow/production.env
COMPOSE_PROJECT=linguaflow
COMPOSE_FILE=docker-compose.production.yml
BACKEND_IMAGE=${LINGUAFLOW_BACKEND_IMAGE:-ghcr.io/linguaflow/linguaflow-backend}
FRONTEND_IMAGE=${LINGUAFLOW_FRONTEND_IMAGE:-ghcr.io/linguaflow/linguaflow-frontend}

fail() {
  printf '%s\n' "deploy_release: $*" >&2
  exit 1
}

usage() {
  printf '%s\n' 'Usage: scripts/deployment/deploy_release.sh [--dry-run] RELEASE_SHA' >&2
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

cd "$REPO_ROOT" || fail 'cannot enter repository root'

compose() {
  RELEASE_SHA=$RELEASE_SHA docker compose \
    --env-file "$RUNTIME_ENV_FILE" \
    -p "$COMPOSE_PROJECT" \
    -f "$COMPOSE_FILE" \
    "$@"
}

if [ "$dry_run" = true ]; then
  printf '%s\n' "DRY-RUN release SHA: $RELEASE_SHA"
  printf '%s\n' "DRY-RUN image: $BACKEND_IMAGE:$RELEASE_SHA"
  printf '%s\n' "DRY-RUN image: $FRONTEND_IMAGE:$RELEASE_SHA"
  sh "$REPO_ROOT/scripts/deployment/backup_postgres.sh" --dry-run "$RELEASE_SHA"
  printf '%s\n' 'DRY-RUN pull: docker compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml pull backend frontend'
  printf '%s\n' 'DRY-RUN validate: docker compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml run --rm --no-deps --entrypoint caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'
  printf '%s\n' 'DRY-RUN migrate: docker compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml run --rm --no-deps --entrypoint sh backend -c alembic upgrade head'
  printf '%s\n' 'DRY-RUN rollout: docker compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml up -d --no-build --force-recreate --scale backend=1 --scale frontend=1 --scale caddy=1 backend frontend caddy'
  sh "$REPO_ROOT/scripts/deployment/verify_production.sh" --dry-run
  printf '%s\n' "DRY-RUN finalize LKG: after independent verification, operator may run scripts/deployment/finalize_release.sh $RELEASE_SHA"
  printf '%s\n' 'DRY-RUN LKG: unchanged; finalization is a separate explicit operator action after verification.'
  exit 0
fi

stage=backup
if ! sh "$REPO_ROOT/scripts/deployment/backup_postgres.sh" "$RELEASE_SHA"; then
  fail 'backup failed; no image pull, migration, rollout, rollback, or restore was attempted'
fi

stage=pull
if ! compose pull backend frontend; then
  fail 'image pull failed; no migration, rollout, rollback, or restore was attempted'
fi

stage=caddy-validation
if ! compose run --rm --no-deps --entrypoint caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile; then
  fail 'Caddy validation failed; no migration, rollout, rollback, or restore was attempted'
fi

stage=migration
if ! compose run --rm --no-deps --entrypoint sh backend -c 'alembic upgrade head'; then
  fail 'migration failed; no rollout, rollback, or restore was attempted'
fi

stage=rollout
if ! compose up -d --no-build --force-recreate \
  --scale backend=1 --scale frontend=1 --scale caddy=1 \
  backend frontend caddy; then
  fail 'rollout failed; automatic rollback and database restore are intentionally disabled'
fi

stage=verification
if ! sh "$REPO_ROOT/scripts/deployment/verify_production.sh"; then
  fail 'public verification failed; automatic rollback, restore, and LKG update are intentionally disabled'
fi

printf '%s\n' "Release $RELEASE_SHA deployed and verified."
printf '%s\n' "LKG was not changed. After independent acceptance, run: scripts/deployment/finalize_release.sh $RELEASE_SHA"
