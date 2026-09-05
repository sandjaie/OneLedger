#!/usr/bin/env bash
# Prints the Postgres DSN, assembled from the parts in .env. One source of truth for the
# password: nothing anywhere holds a second copy that can drift out of step.
#
#   DATABASE_URL="$(scripts/dsn.sh)" psql "$DATABASE_URL" -c '\dt'
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh

: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is unset — copy .env.example to .env}"

# Percent-encode the credentials. The file invites a real password, and `#`, `@`, `/` and `:`
# are all legal in one and all structural in a URI — an unencoded `#` silently truncates the
# DSN at the fragment, leaving a connection attempt with the wrong password and no clue why.
_encode() { python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }

printf 'postgresql://%s:%s@%s:%s/%s\n' \
  "$(_encode "${POSTGRES_USER:-oneledger}")" \
  "$(_encode "$POSTGRES_PASSWORD")" \
  "${POSTGRES_HOST:-127.0.0.1}" \
  "${POSTGRES_PORT:-5432}" \
  "${POSTGRES_DB:-oneledger}"
