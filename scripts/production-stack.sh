#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

project="oneledger-production"
compose_file="docker-compose.production.yml"
env_file=".env.production"
volumes="oneledger-production_pgdata oneledger-production_documents"

usage() {
  echo "usage: scripts/production-stack.sh init|up|down|status|reset" >&2
  exit 2
}

compose() {
  docker compose --project-name "$project" --env-file "$env_file" -f "$compose_file" "$@"
}

ensure_volumes() {
  for volume in $volumes; do
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then
      docker volume create "$volume" >/dev/null
    fi
  done
}

configured_email() {
  local source=".env"
  if [ ! -f "$source" ]; then
    source=".env.production.example"
  fi
  sed -n 's/^ALLOWED_USER_EMAIL=//p' "$source" | tail -n 1
}

initialize() {
  if [ ! -f "$env_file" ]; then
    local email password session_secret account_salt backup_passphrase
    email="$(configured_email)"
    case "$email" in
      *@*) ;;
      *) echo "ALLOWED_USER_EMAIL is not configured in .env or .env.production.example" >&2; exit 1 ;;
    esac
    password="$(openssl rand -hex 32)"
    session_secret="$(openssl rand -hex 32)"
    account_salt="$(openssl rand -hex 32)"
    backup_passphrase="$(openssl rand -hex 32)"
    umask 077
    {
      echo "PRODUCTION_ENV_FILE=.env.production"
      echo "POSTGRES_USER=oneledger"
      echo "POSTGRES_PASSWORD=$password"
      echo "POSTGRES_DB=oneledger"
      echo "POSTGRES_HOST=127.0.0.1"
      echo "POSTGRES_PORT=55432"
      echo "AUTH_MODE=local"
      echo "SESSION_SECRET=$session_secret"
      echo "WEB_ORIGIN=http://localhost:3100"
      echo "NEXT_PUBLIC_API_URL=http://localhost:8100"
      echo "COOKIE_SECURE=0"
      echo "ALLOWED_USER_EMAIL=$email"
      echo "GOOGLE_CLIENT_ID="
      echo "GOOGLE_CLIENT_SECRET="
      echo "GOOGLE_REDIRECT_URI=http://localhost:8100/auth/callback"
      echo "ACCOUNT_HASH_SALT=$account_salt"
      echo "BACKUP_PASSPHRASE=$backup_passphrase"
      echo "DOCUMENTS_DIR=/var/lib/oneledger/documents"
    } >"$env_file"
    chmod 600 "$env_file"
  fi
  ensure_volumes
}

command="${1:-}"
case "$command" in
  init)
    [ "$#" -eq 1 ] || usage
    initialize
    ;;
  up)
    [ "$#" -eq 1 ] || usage
    initialize
    compose up -d --build --wait
    ;;
  down)
    [ "$#" -eq 1 ] || usage
    [ -f "$env_file" ] || { echo "run scripts/production-stack.sh init first" >&2; exit 1; }
    compose down
    ;;
  status)
    [ "$#" -eq 1 ] || usage
    [ -f "$env_file" ] || { echo "run scripts/production-stack.sh init first" >&2; exit 1; }
    compose ps
    ;;
  reset)
    if [ "$#" -ne 3 ] || [ "$2" != "--confirm" ] || [ "$3" != "$project" ]; then
      echo "reset requires: --confirm oneledger-production" >&2
      exit 2
    fi
    [ -f "$env_file" ] || { echo "run scripts/production-stack.sh init first" >&2; exit 1; }
    compose down
    docker volume rm oneledger-production_pgdata
    docker volume rm oneledger-production_documents
    ;;
  *) usage ;;
esac
