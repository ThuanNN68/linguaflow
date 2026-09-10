#!/usr/bin/env sh

set -eu

if [ -n "${FAKE_CURL_LOG:-}" ]; then
  printf '%s\n' "$*" >>"$FAKE_CURL_LOG"
fi

if [ "${FAKE_CURL_MODE:-pass}" = fail ]; then
  exit 1
fi

output_file=
url=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output)
      output_file=$2
      shift 2
      ;;
    http*)
      url=$1
      shift
      ;;
    *) shift ;;
  esac
done

if [ -n "$output_file" ]; then
  case "$url" in
    *api-c3-lingua-flow-217*)
      printf '%s\n' '{"service":"LinguaFlow API","database":"ok"}' >"$output_file"
      ;;
    *)
      printf '%s\n' '<html><title>LinguaFlow</title></html>' >"$output_file"
      ;;
  esac
fi

printf '%s' '200'
