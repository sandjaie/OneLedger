#!/usr/bin/env bash
# Manage the development stack backed by the persistent working ledger.
# This script never removes the database or documents volumes.
set -euo pipefail

cd "$(dirname "$0")/.."

project="oneledger"
compose_file="docker-compose.yml"
env_file=".env"
volumes=(oneledger_pgdata oneledger_documents)

# Pin both Compose self-configuration and interpolation. Shell variables outrank .env, while the
# explicit CLI flags keep an inherited COMPOSE_FILE from redirecting lifecycle commands.
export COMPOSE_PROJECT_NAME="$project"
export COMPOSE_FILE="$compose_file"

usage() {
  echo "usage: scripts/dev-stack.sh up|down|status|logs [service]" >&2
  exit 2
}

compose() {
  docker compose \
    --project-name "$project" \
    --env-file "$env_file" \
    -f "$compose_file" \
    "$@"
}

require_volumes() {
  local volume missing=()
  if ! docker info >/dev/null; then
    echo "cannot inspect the Docker daemon; no development stack changes were made" >&2
    exit 1
  fi
  for volume in "${volumes[@]}"; do
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then
      missing+=("$volume")
    fi
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "missing required development volumes: ${missing[*]}" >&2
    echo "refusing to create a fresh working ledger; restore the expected volumes first" >&2
    exit 1
  fi
}

require_env() {
  if [ ! -f .env ]; then
    echo "copy .env.example to .env and configure the development owner before starting" >&2
    exit 1
  fi
}

command="${1:-}"
case "$command" in
  up)
    [ "$#" -eq 1 ] || usage
    require_env
    require_volumes
    compose up -d --build --wait
    compose --profile tools run --rm flyway migrate
    echo "development stack is up using the persistent working ledger"
    echo "  app  http://localhost:3000"
    echo "  api  http://localhost:8000"
    ;;
  down)
    [ "$#" -eq 1 ] || usage
    require_env
    compose down
    ;;
  status)
    [ "$#" -eq 1 ] || usage
    require_env
    compose ps
    ;;
  logs)
    [ "$#" -le 2 ] || usage
    require_env
    if [ "$#" -eq 2 ]; then
      compose logs --tail 200 "$2"
    else
      compose logs --tail 200
    fi
    ;;
  *) usage ;;
esac
