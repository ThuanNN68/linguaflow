#!/usr/bin/env sh

# Creates a logical PostgreSQL backup before a production schema migration.
# This script intentionally never reads, prints, or sources the runtime env file.

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
RUNTIME_ENV_FILE=/etc/linguaflow/production.env
COMPOSE_PROJECT=linguaflow
COMPOSE_FILE=docker-compose.production.yml
BACKUP_DIR=${LINGUAFLOW_BACKUP_DIR:-/var/backups/linguaflow}

cd "$REPO_ROOT"

fail() {
  printf '%s\n' "backup_postgres: $*" >&2
  exit 1
}

usage() {
  printf '%s\n' 'Usage: scripts/deployment/backup_postgres.sh [--dry-run] RELEASE_SHA' >&2
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

compose() {
  RELEASE_SHA=$RELEASE_SHA docker compose \
    --env-file "$RUNTIME_ENV_FILE" \
    -p "$COMPOSE_PROJECT" \
    -f "$COMPOSE_FILE" \
    "$@"
}

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_file="$BACKUP_DIR/postgres-${RELEASE_SHA}-${timestamp}.sql"

if [ "$dry_run" = true ]; then
  printf '%s\n' "DRY-RUN backup: create $backup_file"
  printf '%s\n' 'DRY-RUN backup: docker compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml exec -T postgres pg_dump'
  exit 0
fi

umask 077
mkdir -p "$BACKUP_DIR" || fail "cannot create backup directory: $BACKUP_DIR"
tmp_file=$(mktemp "$BACKUP_DIR/.postgres-${RELEASE_SHA}-${timestamp}.XXXXXX") || fail 'cannot allocate backup file'

cleanup() {
  if [ -n "${tmp_file:-}" ] && [ -f "$tmp_file" ]; then
    rm -f "$tmp_file"
  fi
}
trap cleanup EXIT HUP INT TERM

if ! compose exec -T postgres sh -ceu 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >"$tmp_file"; then
  fail 'pg_dump failed; migration and rollout must not continue'
fi

mv "$tmp_file" "$backup_file" || fail 'cannot finalize backup file'
tmp_file=
printf '%s\n' "Backup completed: $backup_file"
