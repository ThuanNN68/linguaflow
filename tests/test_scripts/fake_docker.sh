#!/usr/bin/env sh

set -eu

if [ -n "${FAKE_DOCKER_LOG:-}" ]; then
  printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"
fi

default_sha=${RELEASE_SHA:-0123456789abcdef0123456789abcdef01234567}
backend_container_id=${FAKE_DOCKER_BACKEND_CONTAINER_ID-backend-container}
frontend_container_id=${FAKE_DOCKER_FRONTEND_CONTAINER_ID-frontend-container}
backend_image=${FAKE_DOCKER_BACKEND_IMAGE-ghcr.io/linguaflow/linguaflow-backend:$default_sha}
frontend_image=${FAKE_DOCKER_FRONTEND_IMAGE-ghcr.io/linguaflow/linguaflow-frontend:$default_sha}
last_arg=
for arg in "$@"; do
  last_arg=$arg
done

if [ -n "${FAKE_DOCKER_FAIL_ON:-}" ]; then
  case "$*" in
    *"$FAKE_DOCKER_FAIL_ON"*)
      printf '%s\n' "fake docker failure: $FAKE_DOCKER_FAIL_ON" >&2
      exit 1
      ;;
  esac
fi

case "$*" in
  *' ps --status running -q backend')
    [ -n "$backend_container_id" ] && printf '%s\n' "$backend_container_id"
    exit 0
    ;;
  *' ps --status running -q frontend')
    [ -n "$frontend_container_id" ] && printf '%s\n' "$frontend_container_id"
    exit 0
    ;;
  *'inspect --format {{.Config.Image}} '*)
    case "$last_arg" in
      "$backend_container_id") printf '%s\n' "$backend_image" ;;
      "$frontend_container_id") printf '%s\n' "$frontend_image" ;;
      *)
        printf '%s\n' 'fake docker: unknown container ID' >&2
        exit 1
        ;;
    esac
    exit 0
    ;;
esac

exit 0
