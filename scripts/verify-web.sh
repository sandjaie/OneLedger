#!/usr/bin/env bash
# Exact-head gate for web-only branches. No API, database or Compose test work runs here.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

scope_only=
if [ "${1:-}" = "--scope-only" ]; then
  [ "$#" -eq 1 ] || fail "usage: scripts/verify-web.sh [--scope-only]"
  scope_only=1
else
  [ "$#" -eq 0 ] || fail "usage: scripts/verify-web.sh [--scope-only]"
fi

[ -z "$(git status --porcelain --untracked-files=normal)" ] ||
  fail "working tree must be clean before web verification."

base_ref=origin/develop
if ! git rev-parse --verify --quiet "$base_ref" >/dev/null; then
  base_ref=develop
fi
git rev-parse --verify --quiet "$base_ref" >/dev/null ||
  fail "cannot find origin/develop or develop for verification scope."
base="$(git merge-base "$base_ref" HEAD)"

web_changed=
while IFS= read -r path; do
  [ -n "$path" ] || continue
  case "$path" in
    web/*) web_changed=1 ;;
    docs/*|CLAUDE.md|AGENTS.md|README.md|.claude/skills/*|.github/PULL_REQUEST_TEMPLATE.md|.pre-commit-config.yaml|scripts/verify.sh|scripts/verify-web.sh|scripts/tests/verify-web.sh) ;;
    *) fail "web-only verification refuses changed path: $path" ;;
  esac
done < <(git diff --name-only "$base" HEAD)
[ -n "$web_changed" ] || fail "web-only verification requires a web/ change."

head="$(git rev-parse HEAD)"
if [ -n "$scope_only" ]; then
  echo "PASS web-only scope $head"
  exit 0
fi

receipt="$(git rev-parse --git-path oneledger-verified-head)"
rm -f "$receipt"
echo "VERIFY scope=web-only head=$head"

run_stage() {
  local label="$1"
  shift
  echo "RUN  $label"
  if "$@"; then
    echo "PASS $label"
  else
    local status=$?
    echo "FAIL $label (exit $status)" >&2
    return "$status"
  fi
}

run_commit_hooks() {
  SKIP=end-of-file-fixer,trailing-whitespace,ruff,ruff-format,eslint pre-commit run --all-files
}

run_web_gate() (
  cd web &&
    npm run lint &&
    npm run api:types:check &&
    npm run typecheck &&
    npm run typecheck:compat &&
    npm run test &&
    npm run build
)

scan_web_bundle() {
  local status
  if grep -rEq \
    'SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://' \
    web/.next/static/; then
    fail "forbidden server-side value found in the web bundle"
  else
    status=$?
    [ "$status" -eq 1 ] || fail "web bundle scan could not complete"
  fi
}

run_stage docs python3 scripts/check-docs.py
run_stage commit-hooks run_commit_hooks
run_stage markdown pre-commit run markdownlint --all-files --hook-stage manual
run_stage web run_web_gate
run_stage bundle-scan scan_web_bundle
run_stage diff-size scripts/check-diff-size.sh

[ "$(git rev-parse HEAD)" = "$head" ] || fail "HEAD changed during web verification."
[ -z "$(git status --porcelain --untracked-files=normal)" ] ||
  fail "working tree must be clean after web verification."

printf '%s\n' "$head" > "$receipt"
echo "PASS exact-head $head"
