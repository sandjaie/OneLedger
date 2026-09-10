#!/usr/bin/env bash
# Run a tool inside the Python service. Exits cleanly while api/ does not
# exist, so this hook is installable before Phase 0 PR 4 creates it.
set -euo pipefail
[ -d api ] || { echo "api/ not created yet — skipping $*"; exit 0; }

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed, and the api hooks need it: https://docs.astral.sh/uv/"
  exit 1
fi

args=()
for arg in "$@"; do
  case "$arg" in
    api/*) args+=("${arg#api/}") ;;
    *) args+=("$arg") ;;
  esac
done

cd api && exec uv run "${args[@]}"
