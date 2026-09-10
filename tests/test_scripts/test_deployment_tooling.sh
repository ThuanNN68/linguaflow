#!/usr/bin/env sh

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
SHELL_BIN=${SHELL_BIN:-sh}
SHA=0123456789abcdef0123456789abcdef01234567
UPPER_SHA=0123456789ABCDEF0123456789ABCDEF01234567
OTHER_SHA=1111111111111111111111111111111111111111
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/linguaflow-cd2.XXXXXX")

cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT HUP INT TERM

fail() {
  printf '%s\n' "test_deployment_tooling: $*" >&2
  exit 1
}

assert_contains() {
  needle=$1
  haystack_file=$2
  grep -Fq -- "$needle" "$haystack_file" || fail "missing expected text: $needle"
}

assert_not_contains() {
  needle=$1
  haystack_file=$2
  if grep -Fq -- "$needle" "$haystack_file"; then
    fail "unexpected text: $needle"
  fi
}

lkg_checksum() {
  sha256sum "$lkg_file" | awk '{print $1}'
}

reset_lkg() {
  printf '%s\n' 'previous-known-good' >"$lkg_file"
}

mkdir -p "$tmp_dir/bin" "$tmp_dir/backups"
cp "$ROOT/tests/test_scripts/fake_docker.sh" "$tmp_dir/bin/docker"
cp "$ROOT/tests/test_scripts/fake_curl.sh" "$tmp_dir/bin/curl"
chmod +x "$tmp_dir/bin/docker" "$tmp_dir/bin/curl"

for script in \
  backup_postgres.sh verify_production.sh finalize_release.sh deploy_release.sh \
  run_remote_release.sh production_release_wrapper.sh; do
  "$SHELL_BIN" -n "$ROOT/scripts/deployment/$script" || fail "shell syntax failed: $script"
done

ci_before=$(sha256sum "$ROOT/.github/workflows/ci.yml" | awk '{print $1}')

dry_log="$tmp_dir/dry-run.log"
lkg_file="$tmp_dir/last-known-good-sha"
reset_lkg
: >"$tmp_dir/docker.log"
PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_LOG="$tmp_dir/docker.log" \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/deploy_release.sh" --dry-run "$UPPER_SHA" >"$dry_log"

[ ! -s "$tmp_dir/docker.log" ] || fail 'dry-run invoked Docker'
assert_contains "ghcr.io/linguaflow/linguaflow-backend:$SHA" "$dry_log"
assert_contains "ghcr.io/linguaflow/linguaflow-frontend:$SHA" "$dry_log"
assert_contains 'DRY-RUN backup:' "$dry_log"
assert_contains 'DRY-RUN migrate:' "$dry_log"
assert_contains 'DRY-RUN rollout:' "$dry_log"
assert_contains 'DRY-RUN finalize LKG:' "$dry_log"
assert_contains 'DRY-RUN LKG: unchanged' "$dry_log"
lkg_value=$(sed -n '1p' "$lkg_file")
[ "$lkg_value" = previous-known-good ] || fail 'dry-run modified last-known-good state'

finalize_dry_docker_log="$tmp_dir/finalize-dry-run-docker.log"
: >"$finalize_dry_docker_log"
PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_LOG="$finalize_dry_docker_log" \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/finalize_release.sh" --dry-run "$UPPER_SHA" >"$tmp_dir/finalize-dry-run.log"
[ ! -s "$finalize_dry_docker_log" ] || fail 'finalize dry-run inspected Docker'
assert_contains "linguaflow-backend:$SHA" "$tmp_dir/finalize-dry-run.log"
assert_contains "linguaflow-frontend:$SHA" "$tmp_dir/finalize-dry-run.log"
lkg_value=$(sed -n '1p' "$lkg_file")
[ "$lkg_value" = previous-known-good ] || fail 'finalize dry-run modified last-known-good state'

for invalid in main develop_v2 latest 0123456789abcdef 0123456789abcdef0123456789abcdef0123456g; do
  if "$SHELL_BIN" "$ROOT/scripts/deployment/deploy_release.sh" --dry-run "$invalid" >/dev/null 2>&1; then
    fail "invalid release selector unexpectedly accepted: $invalid"
  fi
done

if "$SHELL_BIN" "$ROOT/scripts/deployment/deploy_release.sh" --dry-run >/dev/null 2>&1; then
  fail 'missing release SHA unexpectedly accepted'
fi

actual_log="$tmp_dir/docker-success.log"
curl_log="$tmp_dir/curl-success.log"
: >"$actual_log"
: >"$curl_log"
PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_LOG="$actual_log" \
  FAKE_CURL_LOG="$curl_log" \
  LINGUAFLOW_BACKUP_DIR="$tmp_dir/backups" \
  VERIFY_ATTEMPTS=1 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  "$SHELL_BIN" "$ROOT/scripts/deployment/deploy_release.sh" "$SHA" >"$tmp_dir/success.log"

expected_prefix='compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml '
while IFS= read -r docker_call; do
  case "$docker_call" in
    "$expected_prefix"*) ;;
    *) fail "compose call violates fixed contract: $docker_call" ;;
  esac
done <"$actual_log"
assert_contains 'pull backend frontend' "$actual_log"
assert_contains 'run --rm --no-deps --entrypoint caddy caddy validate' "$actual_log"
assert_contains '--entrypoint sh backend -c alembic upgrade head' "$actual_log"
assert_contains 'up -d --no-build --force-recreate --scale backend=1 --scale frontend=1 --scale caddy=1 backend frontend caddy' "$actual_log"
rollout_call=$(grep ' up -d ' "$actual_log")
case "$rollout_call" in
  *postgres*) fail 'rollout explicitly targets postgres' ;;
esac
backup_count=$(find "$tmp_dir/backups" -type f -name "postgres-$SHA-*.sql" | wc -l | tr -d ' ')
[ "$backup_count" = 1 ] || fail 'successful deployment did not create a SHA-tagged backup'

backup_failure_log="$tmp_dir/docker-backup-failure.log"
: >"$backup_failure_log"
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_LOG="$backup_failure_log" \
  FAKE_DOCKER_FAIL_ON='exec -T postgres' \
  LINGUAFLOW_BACKUP_DIR="$tmp_dir/backups" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/deploy_release.sh" "$SHA" >/dev/null 2>&1; then
  fail 'backup failure unexpectedly continued'
fi
assert_not_contains 'pull backend frontend' "$backup_failure_log"

migration_failure_log="$tmp_dir/docker-migration-failure.log"
: >"$migration_failure_log"
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_LOG="$migration_failure_log" \
  FAKE_DOCKER_FAIL_ON='--entrypoint sh backend' \
  LINGUAFLOW_BACKUP_DIR="$tmp_dir/backups" \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/deploy_release.sh" "$SHA" >/dev/null 2>&1; then
  fail 'migration failure unexpectedly continued'
fi
assert_contains 'pull backend frontend' "$migration_failure_log"
assert_not_contains 'up -d --no-build' "$migration_failure_log"
lkg_value=$(sed -n '1p' "$lkg_file")
[ "$lkg_value" = previous-known-good ] || fail 'failed deployment modified last-known-good state'

public_failure_docker_log="$tmp_dir/public-failure-docker.log"
: >"$public_failure_docker_log"
public_failure_lkg_checksum=$(lkg_checksum)
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_CURL_MODE=fail \
  FAKE_DOCKER_LOG="$public_failure_docker_log" \
  VERIFY_ATTEMPTS=1 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/finalize_release.sh" "$SHA" >/dev/null 2>&1; then
  fail 'finalization unexpectedly accepted failed public verification'
fi
after_public_failure_lkg_checksum=$(lkg_checksum)
[ "$public_failure_lkg_checksum" = "$after_public_failure_lkg_checksum" ] \
  || fail 'failed public verification modified last-known-good state'
[ ! -s "$public_failure_docker_log" ] \
  || fail 'failed public verification reached Docker image identity checks'

finalization_log="$tmp_dir/finalization-success.log"
: >"$finalization_log"
PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_LOG="$finalization_log" \
  VERIFY_ATTEMPTS=1 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/finalize_release.sh" "$UPPER_SHA" >/dev/null
lkg_value=$(sed -n '1p' "$lkg_file")
[ "$lkg_value" = "$SHA" ] || fail 'uppercase release SHA was not normalized before finalization'
assert_contains 'compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml ps --status running -q backend' "$finalization_log"
assert_contains 'compose --env-file /etc/linguaflow/production.env -p linguaflow -f docker-compose.production.yml ps --status running -q frontend' "$finalization_log"
assert_contains 'inspect --format {{.Config.Image}} backend-container' "$finalization_log"
assert_contains 'inspect --format {{.Config.Image}} frontend-container' "$finalization_log"

reset_lkg
backend_mismatch_lkg_checksum=$(lkg_checksum)
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_BACKEND_IMAGE="ghcr.io/linguaflow/linguaflow-backend:$OTHER_SHA" \
  VERIFY_ATTEMPTS=1 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/finalize_release.sh" "$SHA" >/dev/null 2>&1; then
  fail 'finalization unexpectedly accepted a backend SHA mismatch'
fi
[ "$backend_mismatch_lkg_checksum" = "$(lkg_checksum)" ] \
  || fail 'backend SHA mismatch modified last-known-good state'

reset_lkg
frontend_mismatch_lkg_checksum=$(lkg_checksum)
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_FRONTEND_IMAGE="ghcr.io/linguaflow/linguaflow-frontend:$OTHER_SHA" \
  VERIFY_ATTEMPTS=1 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/finalize_release.sh" "$SHA" >/dev/null 2>&1; then
  fail 'finalization unexpectedly accepted a frontend SHA mismatch'
fi
[ "$frontend_mismatch_lkg_checksum" = "$(lkg_checksum)" ] \
  || fail 'frontend SHA mismatch modified last-known-good state'

reset_lkg
missing_backend_lkg_checksum=$(lkg_checksum)
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_BACKEND_CONTAINER_ID= \
  VERIFY_ATTEMPTS=1 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/finalize_release.sh" "$SHA" >/dev/null 2>&1; then
  fail 'finalization unexpectedly accepted a missing backend container'
fi
[ "$missing_backend_lkg_checksum" = "$(lkg_checksum)" ] \
  || fail 'missing backend container modified last-known-good state'

reset_lkg
missing_frontend_lkg_checksum=$(lkg_checksum)
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_DOCKER_FRONTEND_CONTAINER_ID= \
  VERIFY_ATTEMPTS=1 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  LINGUAFLOW_LKG_FILE="$lkg_file" \
  "$SHELL_BIN" "$ROOT/scripts/deployment/finalize_release.sh" "$SHA" >/dev/null 2>&1; then
  fail 'finalization unexpectedly accepted a missing frontend container'
fi
[ "$missing_frontend_lkg_checksum" = "$(lkg_checksum)" ] \
  || fail 'missing frontend container modified last-known-good state'

curl_failure_log="$tmp_dir/curl-failure.log"
: >"$curl_failure_log"
if PATH="$tmp_dir/bin:$PATH" \
  FAKE_CURL_LOG="$curl_failure_log" \
  FAKE_CURL_MODE=fail \
  VERIFY_ATTEMPTS=3 \
  VERIFY_DELAY_SECONDS=1 \
  VERIFY_TIMEOUT_SECONDS=1 \
  "$SHELL_BIN" "$ROOT/scripts/deployment/verify_production.sh" >/dev/null 2>&1; then
  fail 'verification failure unexpectedly passed'
fi
curl_calls=$(wc -l <"$curl_failure_log" | tr -d ' ')
[ "$curl_calls" = 6 ] || fail "verification was not bounded as expected: $curl_calls curl calls"

for prohibited in 'down -v' 'volume rm' 'system prune' 'image prune' 'pg_restore' 'alembic downgrade' 'reset' 'seed'; do
  if grep -Fq -- "$prohibited" \
    "$ROOT/scripts/deployment/backup_postgres.sh" \
    "$ROOT/scripts/deployment/deploy_release.sh" \
    "$ROOT/scripts/deployment/finalize_release.sh" \
    "$ROOT/scripts/deployment/run_remote_release.sh" \
    "$ROOT/scripts/deployment/production_release_wrapper.sh"; then
    fail "prohibited destructive operation found: $prohibited"
  fi
done

if grep -Eq '(^|[[:space:];])(source|\.)([[:space:]]+).*production\.env|cat[[:space:]]+.*production\.env' \
  "$ROOT/scripts/deployment/backup_postgres.sh" \
  "$ROOT/scripts/deployment/deploy_release.sh" \
  "$ROOT/scripts/deployment/finalize_release.sh" \
  "$ROOT/scripts/deployment/production_release_wrapper.sh"; then
  fail 'a deployment helper reads the runtime environment file directly'
fi

if grep -Fq 'configure_vps_env.sh' "$ROOT/scripts/deployment/deploy_release.sh"; then
  fail 'normal deployment invokes the provisioning-only configuration helper'
fi

ci_after=$(sha256sum "$ROOT/.github/workflows/ci.yml" | awk '{print $1}')
[ "$ci_before" = "$ci_after" ] || fail 'ci.yml changed while exercising deployment tooling'

printf '%s\n' 'Deployment tooling tests passed.'
