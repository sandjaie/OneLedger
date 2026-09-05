#!/usr/bin/env bash
# Prints the Postgres DSN, assembled from the parts in .env. One source of truth for the
# password: nothing anywhere holds a second copy that can drift out of step.
#
#   DATABASE_URL="$(scripts/dsn.sh)" psql "$DATABASE_URL" -c '\dt'
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/load-env.sh

: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is unset — copy .env.example to .env}"
printf 'postgresql://%s:%s@%s:%s/%s\n' \
  "${POSTGRES_USER:-oneledger}" "$POSTGRES_PASSWORD" \
  "${POSTGRES_HOST:-127.0.0.1}" "${POSTGRES_PORT:-5432}" "${POSTGRES_DB:-oneledger}"
