#!/usr/bin/env bash
# Run a check inside the web app. Exits cleanly while web/ does not exist,
# so this hook is installable before Phase 0 PR 6 creates it.
set -euo pipefail
[ -d web ] || { echo "web/ not created yet — skipping $1"; exit 0; }
[ -d web/node_modules ] || { echo "web/node_modules missing — run 'npm ci' in web/"; exit 1; }

action="$1"
shift
args=()
for arg in "$@"; do
  case "$arg" in
    web/*) args+=("${arg#web/}") ;;
    *) args+=("$arg") ;;
  esac
done

cd web
case "$action" in
  lint)
    if [ ${#args[@]} -eq 0 ]; then args=(.); fi
    exec npx --no-install eslint "${args[@]}"
    ;;
  typecheck)
    npm run typecheck
    exec npm run typecheck:compat
    ;;
  test)      exec npx --no-install vitest run --passWithNoTests ;;
  api-types-check) exec npm run api:types:check ;;
  *)         echo "unknown web check: $action"; exit 1 ;;
esac
