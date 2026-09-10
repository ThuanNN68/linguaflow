#!/usr/bin/env sh
# INITIAL VPS PROVISIONING ONLY. This script creates the first database
# password; it is never a normal release command. Re-running it after
# PostgreSQL initializes can desynchronize the database and backend password.
set -eu

if [ "${1:-}" != "--initial-provision" ]; then
  echo "Refusing to run: this is INITIAL VPS PROVISIONING ONLY." >&2
  echo "Usage: configure_vps_env.sh --initial-provision APP_DOMAIN API_DOMAIN [ENV_FILE]" >&2
  exit 2
fi

app_domain=${2:?usage: configure_vps_env.sh --initial-provision APP_DOMAIN API_DOMAIN [ENV_FILE]}
api_domain=${3:?usage: configure_vps_env.sh --initial-provision APP_DOMAIN API_DOMAIN [ENV_FILE]}
env_file=${4:-/etc/linguaflow/production.env}

if [ ! -f "$env_file" ]; then
  echo "Missing $env_file" >&2
  exit 1
fi

existing_postgres_password=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$env_file" | head -n 1)
if [ -n "$existing_postgres_password" ]; then
  echo "Refusing to replace an existing POSTGRES_PASSWORD." >&2
  echo "This prevents breaking an already initialized PostgreSQL cluster." >&2
  exit 1
fi

upsert_env() {
  key=$1
  value=$2
  if grep -q "^${key}=" "$env_file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

acme_email=$(sed -n 's/^SMTP_FROM_EMAIL=//p' "$env_file" | head -n 1)
if [ -z "$acme_email" ]; then
  echo "SMTP_FROM_EMAIL must be set before deployment" >&2
  exit 1
fi

upsert_env POSTGRES_DB linguaflow
upsert_env POSTGRES_USER linguaflow
upsert_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
upsert_env APP_ENV production
upsert_env ENVIRONMENT production
upsert_env CORS_ORIGINS "https://${app_domain}"
upsert_env APP_DOMAIN "$app_domain"
upsert_env API_DOMAIN "$api_domain"
upsert_env ACME_EMAIL "$acme_email"
upsert_env NEXT_PUBLIC_API_URL "https://${api_domain}"

chmod 600 "$env_file"
