#!/usr/bin/env bash
# Run a check inside the web app. Exits cleanly while web/ does not exist,
# so this hook is installable before Phase 0 PR 6 creates it.
set -euo pipefail
[ -d web ] || { echo "web/ not created yet — skipping $1"; exit 0; }
[ -d web/node_modules ] || { echo "web/node_modules missing — run 'npm ci' in web/"; exit 1; }

cd web
case "$1" in
  lint)      exec npx --no-install eslint . ;;
  typecheck) exec npx --no-install tsc --noEmit ;;
  test)      exec npx --no-install vitest run --passWithNoTests ;;
  api-types-check) exec npm run api:types:check ;;
  *)         echo "unknown web check: $1"; exit 1 ;;
esac
