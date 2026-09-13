#!/usr/bin/env bash
# One verification entry point for focused development checks and final gates.
#
#   scripts/verify.sh focused -- <command> [args...]
#   scripts/verify.sh focused --database -- <command> [args...]
#   scripts/verify.sh database
#   scripts/verify.sh full
#
# Focused checks leave db-test running so later checks reuse it. The final database and full
# gates tear that same server down exactly once, whether the gate passes or fails.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

usage() {
  cat >&2 <<'EOF'
usage:
  scripts/verify.sh focused [--database] -- <command> [args...]
  scripts/verify.sh database
  scripts/verify.sh full
EOF
  exit 2
}

mode="${1:-}"
[ -n "$mode" ] || usage
shift

start_head="$(git rev-parse HEAD)"
echo "VERIFY mode=$mode head=$start_head"

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
final_gate=

ensure_test_server() {
  local mapped
  if mapped="$(docker compose --profile test port db-test 5432 2>/dev/null)" &&
    [ -n "$mapped" ]; then
    eval "$(scripts/test-db.sh env)"
    echo "REUSE db-test at ${mapped##*:}"
  else
    eval "$(scripts/test-db.sh up)"
    echo "START db-test at ${POSTGRES_PORT}"
  fi
  test_server_ready=1
}

cleanup() {
  local status=$?
  trap - EXIT
  if [ -n "$final_gate" ] && [ -n "$test_server_ready" ]; then
    scripts/test-db.sh down
    echo "STOP db-test"
  fi
  exit "$status"
}
trap cleanup EXIT

assert_same_head() {
  local end_head
  end_head="$(git rev-parse HEAD)"
  if [ "$end_head" != "$start_head" ]; then
    echo "FAIL exact-head (started $start_head, ended $end_head)" >&2
    return 1
  fi
  echo "PASS exact-head $end_head"
}

run_commit_hooks() {
  SKIP=end-of-file-fixer,trailing-whitespace,ruff,ruff-format pre-commit run --all-files
}

run_api_gate() (
  cd api
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy .
  uv run pytest -q
)

run_web_gate() (
  cd web
  npm run api:types:check
  npm run typecheck
  npm run typecheck:compat
  npx vitest run
  npm run build
)

scan_web_bundle() {
  local scan_status
  grep -rE \
    'SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://' \
    web/.next/static/ && scan_status=0 || scan_status=$?
  if [ "$scan_status" -eq 0 ]; then
    echo "forbidden server-side value found in the web bundle" >&2
    return 1
  fi
  if [ "$scan_status" -ne 1 ]; then
    echo "web bundle scan could not complete" >&2
    return "$scan_status"
  fi
}

case "$mode" in
  focused)
    needs_database=
    if [ "${1:-}" = "--database" ]; then
      needs_database=1
      shift
    fi
    [ "${1:-}" = "--" ] || usage
    shift
    [ "$#" -gt 0 ] || usage
    if [ -n "$needs_database" ]; then
      ensure_test_server
    fi
    run_stage focused "$@"
    assert_same_head
    if [ -n "$needs_database" ]; then
      echo "KEEP db-test running for the next branch check"
    fi
    ;;
  database)
    [ "$#" -eq 0 ] || usage
    final_gate=1
    ensure_test_server
    run_stage database scripts/hooks/db-tests.sh
    assert_same_head
    ;;
  full)
    [ "$#" -eq 0 ] || usage
    final_gate=1
    run_stage docs python3 scripts/check-docs.py
    run_stage commit-hooks run_commit_hooks
    run_stage markdown pre-commit run markdownlint --all-files --hook-stage pre-push
    ensure_test_server
    run_stage api run_api_gate
    run_stage database scripts/hooks/db-tests.sh
    run_stage web run_web_gate
    run_stage bundle-scan scan_web_bundle
    assert_same_head
    ;;
  *)
    usage
    ;;
esac
