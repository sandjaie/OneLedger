#!/usr/bin/env bash
# Final local gate. Run once on a clean, reviewed commit before its first push.
# A successful run records the exact HEAD; pre-push only checks that receipt and diff size.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

receipt="$(git rev-parse --git-path oneledger-verified-head)"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

current_head() {
  git rev-parse HEAD
}

check_receipt() {
  local head pushed verified=
  head="$(current_head)"
  pushed="${PRE_COMMIT_TO_REF:-$head}"
  [ -f "$receipt" ] && verified="$(sed -n '1p' "$receipt")"
  [ "$verified" = "$pushed" ] ||
    fail "HEAD has not passed scripts/verify.sh; run it on the clean reviewed commit before pushing."
  echo "PASS verified-head $pushed"
}

if [ "${1:-}" = "--check-receipt" ]; then
  [ "$#" -eq 1 ] || fail "usage: scripts/verify.sh [--check-receipt]"
  check_receipt
  exit 0
fi
[ "$#" -eq 0 ] || fail "usage: scripts/verify.sh [--check-receipt]"

[ -z "$(git status --porcelain --untracked-files=normal)" ] ||
  fail "working tree must be clean before final verification."

start_head="$(current_head)"
rm -f "$receipt"

base_ref=origin/develop
if ! git rev-parse --verify --quiet "$base_ref" >/dev/null; then
  base_ref=develop
fi
git rev-parse --verify --quiet "$base_ref" >/dev/null ||
  fail "cannot find origin/develop or develop for verification scope."
base="$(git merge-base "$base_ref" HEAD)"

scope=docs-only
changed=
while IFS= read -r path; do
  [ -n "$path" ] || continue
  changed=1
  case "$path" in
    docs/*|*.md|CLAUDE.md|AGENTS.md|README.md|.claude/skills/*|.github/PULL_REQUEST_TEMPLATE.md) ;;
    *) scope=full; break ;;
  esac
done < <(git diff --name-only "$base" HEAD)
[ -n "$changed" ] || scope=full

echo "VERIFY scope=$scope head=$start_head"

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

test_server_ready=
cleanup() {
  local status=$?
  trap - EXIT
  if [ -n "$test_server_ready" ]; then
    scripts/test-db.sh down || status=$?
    echo "STOP db-test"
  fi
  exit "$status"
}
trap cleanup EXIT

ensure_test_server() {
  local mapped
  if mapped="$(docker compose --profile test port db-test 5432 2>/dev/null)" &&
    [ -n "$mapped" ]; then
    test_server_ready=1
    eval "$(scripts/test-db.sh env)"
    echo "REUSE db-test at ${mapped##*:}"
  else
    test_server_ready=1
    eval "$(scripts/test-db.sh up)"
    echo "START db-test at ${POSTGRES_PORT}"
  fi
}

run_commit_hooks() {
  SKIP=end-of-file-fixer,trailing-whitespace,ruff,ruff-format pre-commit run --all-files
}

# Every gate chains its commands with &&: run_stage calls a gate inside `if`, where bash ignores
# errexit, so a failed command in the middle of an unchained gate would pass silently.
run_api_gate() (
  cd api &&
    uv run ruff check . &&
    uv run ruff format --check . &&
    uv run mypy . &&
    uv run pytest -q
)

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
  grep -rE \
    'SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://' \
    web/.next/static/ && status=0 || status=$?
  [ "$status" -eq 1 ] || {
    [ "$status" -eq 0 ] && echo "forbidden server-side value found in the web bundle" >&2
    [ "$status" -ne 0 ] && echo "web bundle scan could not complete" >&2
    return 1
  }
}

run_stage docs python3 scripts/check-docs.py
run_stage commit-hooks run_commit_hooks
run_stage markdown pre-commit run markdownlint --all-files --hook-stage manual

if [ "$scope" = full ]; then
  ensure_test_server
  run_stage api run_api_gate
  run_stage database scripts/hooks/db-tests.sh
  run_stage web run_web_gate
  run_stage bundle-scan scan_web_bundle
fi

run_stage diff-size scripts/check-diff-size.sh

if [ -n "$test_server_ready" ]; then
  run_stage database-cleanup scripts/test-db.sh down
  test_server_ready=
  echo "STOP db-test"
fi

end_head="$(current_head)"
[ "$end_head" = "$start_head" ] ||
  fail "exact-head changed (started $start_head, ended $end_head)."
[ -z "$(git status --porcelain --untracked-files=normal)" ] ||
  fail "working tree must be clean after final verification."

printf '%s\n' "$end_head" >"$receipt"
echo "PASS exact-head $end_head"
