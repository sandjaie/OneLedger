#!/usr/bin/env bash
# Sourced, not executed. Loads .env WITHOUT overriding anything already exported — so a
# scratch run that sets POSTGRES_PORT=55432 is not dragged back to the working database by
# a file it never asked to read. A bare `set -a && . ./.env` does exactly that, silently.
#
# The shell parses .env, rather than a hand-rolled reader: quoting and `#` inside a value
# are dotenv syntax that compose honours, and a parser that mangles them would hand Postgres
# one password and dsn.sh another. Snapshot what the caller set, source, put the snapshot
# back — anything .env introduced survives, anything the caller chose wins.
if [ -f .env ]; then
  _preset="$(export -p)"
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  eval "$_preset" 2>/dev/null || true   # read-only vars refuse assignment; harmless
  unset _preset
fi
