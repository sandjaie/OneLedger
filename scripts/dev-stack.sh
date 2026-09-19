#!/usr/bin/env bash
# Manage the development stack backed by the persistent working ledger.
# This script never removes the database or documents volumes.
set -euo pipefail

cd "$(dirname "$0")/.."

project="oneledger"
volumes=(oneledger_pgdata oneledger_documents)

usage() {
  echo "usage: scripts/dev-stack.sh up|down|status|logs [service]" >&2
  exit 2
}

compose() {
  docker compose --project-name "$project" "$@"
}

ensure_volumes() {
  local volume
  for volume in "${volumes[@]}"; do
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then
      docker volume create "$volume" >/dev/null
    fi
  done
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
    ensure_volumes
    compose up -d --build --wait
    compose --profile tools run --rm flyway migrate
    echo "development stack is up using the persistent working ledger"
    echo "  app  http://localhost:3000"
    echo "  api  http://localhost:8000"
    ;;
  down)
    [ "$#" -eq 1 ] || usage
    compose down
    ;;
  status)
    [ "$#" -eq 1 ] || usage
    compose ps
    ;;
  logs)
    [ "$#" -le 2 ] || usage
    if [ "$#" -eq 2 ]; then
      compose logs --tail 200 "$2"
    else
      compose logs --tail 200
    fi
    ;;
  *) usage ;;
esac
