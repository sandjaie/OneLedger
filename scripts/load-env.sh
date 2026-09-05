#!/usr/bin/env bash
# Sourced, not executed. Loads .env WITHOUT overriding anything already exported — so a
# scratch run that sets POSTGRES_PORT=55432 is not dragged back to the working database by
# a file it never asked to read. `set -a && . ./.env` does exactly that, silently.
if [ -f .env ]; then
  while IFS='=' read -r _key _value; do
    [[ "$_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [ -n "${!_key+set}" ] && continue
    export "$_key=${_value%%#*}"
  done < <(grep -vE '^\s*(#|$)' .env)
  unset _key _value
fi
