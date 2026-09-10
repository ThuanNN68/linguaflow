#!/usr/bin/env sh

# Root-owned VPS entry point for automatic releases. Install this file at
# /usr/local/sbin/linguaflow-release and grant the deploy account sudo access
# to this command only. The GHCR token is consumed from stdin and retained only
# in a temporary Docker configuration.

set -eu

RELEASE_ROOT=/opt/linguaflow/releases
INCOMING_ROOT=/home/deploy/linguaflow-incoming
RUNTIME_ENV_FILE=/etc/linguaflow/production.env
LKG_FILE=/opt/linguaflow/state/last-known-good-sha
COMPOSE_PROJECT=linguaflow
BACKEND_REPOSITORY=${LINGUAFLOW_BACKEND_IMAGE:-ghcr.io/linguaflow/linguaflow-backend}
FRONTEND_REPOSITORY=${LINGUAFLOW_FRONTEND_IMAGE:-ghcr.io/linguaflow/linguaflow-frontend}
MINIMUM_AVAILABLE_KB=31457280

fail() {
  printf '%s\n' "linguaflow-release: $*" >&2
  exit 1
}

usage() {
  printf '%s\n' 'Usage: linguaflow-release deploy|rehearse RELEASE_SHA BACKEND_DIGEST FRONTEND_DIGEST INCOMING_PATH GHCR_ACTOR' >&2
  exit 2
}

normalize_sha() {
  value=$1
  case "$value" in
    '' | *[!0123456789abcdefABCDEF]*) return 1 ;;
  esac
  [ "${#value}" -eq 40 ] || return 1
  printf '%s\n' "$value" | tr '[:upper:]' '[:lower:]'
}

validate_digest() {
  value=$1
  case "$value" in
    sha256:*) hex=${value#sha256:} ;;
    *) return 1 ;;
  esac
  [ "${#hex}" -eq 64 ] || return 1
  case "$hex" in
    '' | *[!0123456789abcdef]*) return 1 ;;
  esac
}

[ "$(id -u)" -eq 0 ] || fail 'must run as root'
[ "$#" -eq 6 ] || usage
mode=$1
case "$mode" in
  deploy | rehearse) ;;
  *) usage ;;
esac
release_sha=$(normalize_sha "$2") || fail 'RELEASE_SHA is invalid'
backend_digest=$3
frontend_digest=$4
incoming=$5
ghcr_actor=$6
validate_digest "$backend_digest" || fail 'backend digest is invalid'
validate_digest "$frontend_digest" || fail 'frontend digest is invalid'
case "$ghcr_actor" in
  '' | *[!A-Za-z0-9-]*) fail 'GHCR actor is invalid' ;;
esac

expected_incoming_prefix="$INCOMING_ROOT/$release_sha/"
case "$incoming" in
  "$expected_incoming_prefix"*) ;;
  *) fail 'incoming release path is outside the authorized root' ;;
esac
incoming_suffix=${incoming#"$expected_incoming_prefix"}
case "$incoming_suffix" in
  '' | */* | *[!A-Za-z0-9._-]*) fail 'incoming release identifier is invalid' ;;
esac
[ -d "$incoming" ] || fail 'incoming release directory does not exist'
resolved_incoming=$(realpath -e -- "$incoming") || fail 'cannot resolve incoming release directory'
[ "$resolved_incoming" = "$incoming" ] || fail 'incoming release directory is not canonical'
[ -z "$(find "$incoming" -type l -print -quit)" ] || fail 'incoming release contains a symbolic link'

incoming_validated=false
docker_config=
trusted_dir=
cleanup() {
  if [ -n "$docker_config" ] && [ -d "$docker_config" ]; then
    DOCKER_CONFIG=$docker_config docker logout ghcr.io >/dev/null 2>&1 || true
    rm -rf -- "$docker_config"
  fi
  if [ "$incoming_validated" = true ] && [ -d "$incoming" ]; then
    rm -rf -- "$incoming"
  fi
  if [ -n "$trusted_dir" ] && [ -d "$trusted_dir" ]; then
    rm -rf -- "$trusted_dir"
  fi
}
trap cleanup EXIT HUP INT TERM

expected_files='Caddyfile
SHA256SUMS
docker-compose.production.yml
scripts/deployment/backup_postgres.sh
scripts/deployment/deploy_release.sh
scripts/deployment/finalize_release.sh
scripts/deployment/verify_production.sh'
actual_files=$(cd "$incoming" && find . -type f -printf '%P\n' | LC_ALL=C sort)
[ "$actual_files" = "$expected_files" ] || fail 'incoming release does not contain the exact authorized file set'
[ "$(cd "$incoming" && find . -type d | wc -l | tr -d '[:space:]')" = 3 ] \
  || fail 'incoming release contains an unexpected directory'

verify_checksum() {
  relative=$1
  checksum_count=$(awk -v file="$relative" '$2 == file { count++ } END { print count + 0 }' "$incoming/SHA256SUMS")
  [ "$checksum_count" = 1 ] || fail "checksum manifest entry is missing or duplicated: $relative"
  expected=$(awk -v file="$relative" '$2 == file { print $1 }' "$incoming/SHA256SUMS")
  [ "${#expected}" -eq 64 ] || fail "checksum is malformed: $relative"
  case "$expected" in
    *[!0123456789abcdef]*) fail "checksum is malformed: $relative" ;;
  esac
  actual=$(sha256sum "$incoming/$relative" | awk '{print $1}')
  [ "$actual" = "$expected" ] || fail "checksum mismatch: $relative"
}

for relative in \
  Caddyfile docker-compose.production.yml scripts/deployment/backup_postgres.sh \
  scripts/deployment/deploy_release.sh scripts/deployment/finalize_release.sh \
  scripts/deployment/verify_production.sh; do
  verify_checksum "$relative"
done
manifest_lines=$(wc -l <"$incoming/SHA256SUMS" | tr -d '[:space:]')
[ "$manifest_lines" = 6 ] || fail 'checksum manifest contains an unexpected entry'
incoming_validated=true

for script in \
  deployment/backup_postgres.sh deployment/deploy_release.sh \
  deployment/finalize_release.sh deployment/verify_production.sh; do
  sh -n "$incoming/scripts/$script" || fail "release script syntax is invalid: $script"
done

release_dir="$RELEASE_ROOT/$release_sha"
mkdir -p "$RELEASE_ROOT"
chmod 700 "$RELEASE_ROOT"
trusted_dir=$(mktemp -d "$RELEASE_ROOT/.incoming-${release_sha}.XXXXXX") \
  || fail 'cannot allocate root-owned bundle directory'
cp -R "$incoming/." "$trusted_dir/"
chown -R root:root "$trusted_dir"
chmod 700 "$trusted_dir" "$trusted_dir/scripts" "$trusted_dir/scripts/deployment"
chmod 640 "$trusted_dir/Caddyfile" "$trusted_dir/docker-compose.production.yml" "$trusted_dir/SHA256SUMS"
chmod 750 "$trusted_dir"/scripts/deployment/*.sh
for relative in \
  Caddyfile docker-compose.production.yml scripts/deployment/backup_postgres.sh \
  scripts/deployment/deploy_release.sh scripts/deployment/finalize_release.sh \
  scripts/deployment/verify_production.sh; do
  trusted_hash=$(sha256sum "$trusted_dir/$relative" | awk '{print $1}')
  incoming_hash=$(sha256sum "$incoming/$relative" | awk '{print $1}')
  [ "$trusted_hash" = "$incoming_hash" ] || fail "root-owned bundle copy mismatch: $relative"
done

stage_immutable_release() {
  if [ -e "$release_dir" ]; then
    [ -d "$release_dir" ] || fail 'existing release path is not a directory'
    [ -z "$(find "$release_dir" -type l -print -quit)" ] || fail 'existing release contains a symbolic link'
    for relative in \
      Caddyfile docker-compose.production.yml scripts/deployment/backup_postgres.sh \
      scripts/deployment/deploy_release.sh scripts/deployment/finalize_release.sh \
      scripts/deployment/verify_production.sh; do
      [ -f "$release_dir/$relative" ] || fail "existing release is incomplete: $relative"
      trusted_hash=$(sha256sum "$trusted_dir/$relative" | awk '{print $1}')
      existing_hash=$(sha256sum "$release_dir/$relative" | awk '{print $1}')
      [ "$trusted_hash" = "$existing_hash" ] || fail "existing immutable release differs: $relative"
    done
    return
  fi

  mv "$trusted_dir" "$release_dir" || fail 'cannot finalize immutable release directory'
  trusted_dir=
}

[ -r "$RUNTIME_ENV_FILE" ] || fail 'production runtime environment is unavailable'
[ -f "$LKG_FILE" ] || fail 'last-known-good state is unavailable'
current_lkg=$(sed -n '1p' "$LKG_FILE")
current_lkg=$(normalize_sha "$current_lkg") || fail 'last-known-good state is invalid'

available_kb=$(df -Pk "$RELEASE_ROOT" | awk 'NR == 2 { print $4 }')
case "$available_kb" in
  '' | *[!0123456789]*) fail 'cannot determine available disk space' ;;
esac
[ "$available_kb" -ge "$MINIMUM_AVAILABLE_KB" ] || fail 'less than 30 GiB is available for a production release'

work_dir=$trusted_dir
cd "$work_dir" || fail 'cannot enter root-owned release bundle'
compose() {
  RELEASE_SHA=$release_sha docker compose \
    --env-file "$RUNTIME_ENV_FILE" \
    -p "$COMPOSE_PROJECT" \
    -f docker-compose.production.yml \
    "$@"
}

service_id() {
  service=$1
  ids=$(compose ps --status running -q "$service") || fail "cannot resolve $service container"
  count=$(printf '%s\n' "$ids" | awk 'NF { count++ } END { print count + 0 }')
  [ "$count" = 1 ] || fail "expected exactly one running $service container"
  printf '%s\n' "$ids"
}

volume_identity() {
  for volume in \
    linguaflow_postgres-data linguaflow_backend-uploads \
    linguaflow_caddy-data linguaflow_caddy-config; do
    docker volume inspect --format '{{.Name}}|{{.Mountpoint}}|{{.Driver}}' "$volume" \
      || fail "cannot inspect durable volume: $volume"
  done
}

verify_image() {
  image=$1
  expected_digest=$2
  expected_ref="$image:$release_sha"
  docker pull "$expected_ref"
  repo_digest="$image@$expected_digest"
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$expected_ref" \
    | grep -Fqx "$repo_digest" \
    || fail "pulled image digest does not match: $image"
  revision=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$expected_ref")
  [ "$revision" = "$release_sha" ] || fail "OCI revision does not match: $image"
}

verify_running_artifact() {
  service=$1
  expected_image=$2
  expected_digest=$3
  container=$(service_id "$service")
  actual_image=$(docker inspect --format '{{.Config.Image}}' "$container")
  [ "$actual_image" = "$expected_image:$release_sha" ] \
    || fail "running $service does not use the requested exact-SHA image"
  actual_image_id=$(docker inspect --format '{{.Image}}' "$container")
  expected_image_id=$(docker image inspect --format '{{.Id}}' "$expected_image@$expected_digest") \
    || fail "cannot resolve expected $service digest locally"
  [ "$actual_image_id" = "$expected_image_id" ] \
    || fail "running $service image ID does not match the expected digest"
  revision=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$actual_image_id")
  [ "$revision" = "$release_sha" ] || fail "running $service OCI revision does not match"
}

sh scripts/deployment/verify_production.sh || fail 'production was unhealthy before release processing'
before_backend=$(service_id backend)
before_frontend=$(service_id frontend)
before_postgres=$(service_id postgres)
before_caddy=$(service_id caddy)
before_volumes=$(volume_identity)
printf '%s\n' "BEFORE|backend=$before_backend|frontend=$before_frontend|postgres=$before_postgres|caddy=$before_caddy"

docker_config=$(mktemp -d /tmp/linguaflow-docker.XXXXXX) || fail 'cannot allocate transient Docker configuration'
chmod 700 "$docker_config"
DOCKER_CONFIG=$docker_config docker login ghcr.io --username "$ghcr_actor" --password-stdin >/dev/null \
  || fail 'transient GHCR authentication failed'
export DOCKER_CONFIG="$docker_config"

verify_image "$BACKEND_REPOSITORY" "$backend_digest"
verify_image "$FRONTEND_REPOSITORY" "$frontend_digest"

if [ "$mode" = rehearse ]; then
  [ "$release_sha" = "$current_lkg" ] || fail 'rehearsal SHA must equal the current LKG'
  verify_running_artifact backend "$BACKEND_REPOSITORY" "$backend_digest"
  verify_running_artifact frontend "$FRONTEND_REPOSITORY" "$frontend_digest"
  [ "$(service_id backend)" = "$before_backend" ] || fail 'rehearsal changed backend container identity'
  [ "$(service_id frontend)" = "$before_frontend" ] || fail 'rehearsal changed frontend container identity'
  [ "$(service_id postgres)" = "$before_postgres" ] || fail 'rehearsal changed PostgreSQL container identity'
  [ "$(service_id caddy)" = "$before_caddy" ] || fail 'rehearsal changed Caddy container identity'
  [ "$(volume_identity)" = "$before_volumes" ] || fail 'rehearsal changed durable volume identity'
  sh scripts/deployment/verify_production.sh || fail 'production health failed during rehearsal'
  printf '%s\n' "ALREADY_DEPLOYED|mode=rehearse|release_sha=$release_sha"
  exit 0
fi

if [ "$release_sha" = "$current_lkg" ]; then
  verify_running_artifact backend "$BACKEND_REPOSITORY" "$backend_digest"
  verify_running_artifact frontend "$FRONTEND_REPOSITORY" "$frontend_digest"
  [ "$(service_id postgres)" = "$before_postgres" ] || fail 'PostgreSQL identity drifted during idempotency check'
  [ "$(volume_identity)" = "$before_volumes" ] || fail 'durable volume identity drifted during idempotency check'
  sh scripts/deployment/verify_production.sh || fail 'production health failed during idempotency check'
  printf '%s\n' "ALREADY_DEPLOYED|mode=deploy|release_sha=$release_sha"
  exit 0
fi

stage_immutable_release
work_dir=$release_dir
cd "$work_dir" || fail 'cannot enter immutable release directory'
sh scripts/deployment/deploy_release.sh "$release_sha" || fail 'release coordinator failed; LKG was not changed'
verify_running_artifact backend "$BACKEND_REPOSITORY" "$backend_digest"
verify_running_artifact frontend "$FRONTEND_REPOSITORY" "$frontend_digest"
[ "$(service_id postgres)" = "$before_postgres" ] || fail 'PostgreSQL container identity changed during rollout'
[ "$(volume_identity)" = "$before_volumes" ] || fail 'durable volume identity changed during rollout'
sh scripts/deployment/verify_production.sh || fail 'independent public verification failed after rollout'
LINGUAFLOW_LKG_FILE=$LKG_FILE sh scripts/deployment/finalize_release.sh "$release_sha" \
  || fail 'release passed rollout but LKG finalization failed'
new_lkg=$(sed -n '1p' "$LKG_FILE")
[ "$new_lkg" = "$release_sha" ] || fail 'LKG does not match the deployed release SHA'

after_backend=$(service_id backend)
after_frontend=$(service_id frontend)
after_postgres=$(service_id postgres)
after_caddy=$(service_id caddy)
printf '%s\n' "AFTER|backend=$after_backend|frontend=$after_frontend|postgres=$after_postgres|caddy=$after_caddy"
printf '%s\n' "DEPLOYED|release_sha=$release_sha|backend_digest=$backend_digest|frontend_digest=$frontend_digest"
