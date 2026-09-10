#!/usr/bin/env sh

# Runner-side transport for the production release contract. The GHCR token is
# sent only over the pinned SSH channel on stdin; it is never an argument or a
# staged file.

set -eu

fail() {
  printf '%s\n' "run_remote_release: $*" >&2
  exit 1
}

usage() {
  printf '%s\n' 'Usage: scripts/deployment/run_remote_release.sh deploy|rehearse RELEASE_SHA BACKEND_DIGEST FRONTEND_DIGEST' >&2
  exit 2
}

require_env() {
  name=$1
  eval "value=\${$name-}"
  [ -n "$value" ] || fail "$name is required"
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

[ "$#" -eq 4 ] || usage
mode=$1
case "$mode" in
  deploy | rehearse) ;;
  *) usage ;;
esac

release_sha=$(normalize_sha "$2") || fail 'RELEASE_SHA must be a full hexadecimal commit SHA'
backend_digest=$3
frontend_digest=$4
validate_digest "$backend_digest" || fail 'backend digest must be a lowercase sha256 digest'
validate_digest "$frontend_digest" || fail 'frontend digest must be a lowercase sha256 digest'

for name in \
  PROD_SSH_PRIVATE_KEY PROD_SSH_HOST PROD_SSH_PORT PROD_SSH_USER \
  PROD_SSH_KNOWN_HOSTS GHCR_TOKEN GITHUB_ACTOR GITHUB_RUN_ID \
  GITHUB_RUN_ATTEMPT; do
  require_env "$name"
done

case "$PROD_SSH_HOST" in
  *[!A-Za-z0-9.-]*) fail 'PROD_SSH_HOST contains unsafe characters' ;;
esac
case "$PROD_SSH_PORT" in
  '' | *[!0123456789]*) fail 'PROD_SSH_PORT must be numeric' ;;
esac
case "$PROD_SSH_USER" in
  '' | *[!A-Za-z0-9_-]*) fail 'PROD_SSH_USER contains unsafe characters' ;;
esac
case "$GITHUB_ACTOR" in
  '' | *[!A-Za-z0-9-]*) fail 'GITHUB_ACTOR contains unsafe characters' ;;
esac
case "$GITHUB_RUN_ID:$GITHUB_RUN_ATTEMPT" in
  *[!0123456789:]*) fail 'GitHub run identity is invalid' ;;
esac

if [ -n "${RELEASE_BUNDLE_ROOT-}" ]; then
  repo_root=$(CDPATH='' cd -- "$RELEASE_BUNDLE_ROOT" && pwd) \
    || fail 'cannot resolve RELEASE_BUNDLE_ROOT'
else
  repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
fi
git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail 'release bundle root is not a Git checkout'
tmp_root=$(mktemp -d "${TMPDIR:-/tmp}/linguaflow-release.XXXXXX") \
  || fail 'cannot allocate temporary release directory'
bundle=$tmp_root/bundle
key_file=$tmp_root/deploy-key
known_hosts=$tmp_root/known_hosts
remote_id="$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$mode"
remote_base="/home/$PROD_SSH_USER/linguaflow-incoming/$release_sha"
remote_incoming="$remote_base/$remote_id"
remote_ready=false

cleanup() {
  if [ "$remote_ready" = true ]; then
    ssh -i "$key_file" -p "$PROD_SSH_PORT" \
      -o BatchMode=yes \
      -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=yes \
      -o UserKnownHostsFile="$known_hosts" \
      "$PROD_SSH_USER@$PROD_SSH_HOST" \
      "rm -rf -- '$remote_incoming'" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$tmp_root"
}
trap cleanup EXIT HUP INT TERM

umask 077
printf '%s\n' "$PROD_SSH_PRIVATE_KEY" >"$key_file"
printf '%s\n' "$PROD_SSH_KNOWN_HOSTS" >"$known_hosts"
chmod 600 "$key_file" "$known_hosts"

mkdir "$bundle"
for source in \
  docker-compose.production.yml \
  Caddyfile \
  scripts/deployment/deploy_release.sh \
  scripts/deployment/backup_postgres.sh \
  scripts/deployment/verify_production.sh \
  scripts/deployment/finalize_release.sh; do
  git -C "$repo_root" cat-file -e "HEAD:$source" 2>/dev/null \
    || fail "release input is missing from the checked-out Git revision: $source"
  destination=$bundle/$source
  mkdir -p "$(dirname "$destination")"
  git -C "$repo_root" show "HEAD:$source" >"$destination" \
    || fail "cannot export release input from the checked-out Git revision: $source"
done

(
  cd "$bundle"
  : >SHA256SUMS
  for relative in \
    Caddyfile \
    docker-compose.production.yml \
    scripts/deployment/backup_postgres.sh \
    scripts/deployment/deploy_release.sh \
    scripts/deployment/finalize_release.sh \
    scripts/deployment/verify_production.sh; do
    checksum=$(sha256sum "$relative" | awk '{print $1}')
    printf '%s  %s\n' "$checksum" "$relative" >>SHA256SUMS
  done
)

ssh -i "$key_file" -p "$PROD_SSH_PORT" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$known_hosts" \
  "$PROD_SSH_USER@$PROD_SSH_HOST" \
  "mkdir -p -- '$remote_base' && mkdir -- '$remote_incoming'"
remote_ready=true

scp -i "$key_file" -P "$PROD_SSH_PORT" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$known_hosts" \
  -r "$bundle/." "$PROD_SSH_USER@$PROD_SSH_HOST:$remote_incoming/"

printf '%s\n' "$GHCR_TOKEN" | ssh -i "$key_file" -p "$PROD_SSH_PORT" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$known_hosts" \
  "$PROD_SSH_USER@$PROD_SSH_HOST" \
  "sudo -n /usr/local/sbin/linguaflow-release '$mode' '$release_sha' '$backend_digest' '$frontend_digest' '$remote_incoming' '$GITHUB_ACTOR'"

printf '%s\n' "Production channel completed in $mode mode for $release_sha."
