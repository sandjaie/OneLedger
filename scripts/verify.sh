#!/usr/bin/env bash
# Final local gate. Run once on a clean, reviewed commit before its first push.
# Gates follow the changed surfaces; focused API and database tests may be named explicitly.
# A successful run records the exact HEAD for optional manual inspection.
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
    fail "HEAD has not passed a final verifier; run scripts/verify.sh or scripts/verify-web.sh on the clean reviewed commit."
  echo "PASS verified-head $pushed"
}

if [ "${1:-}" = "--check-receipt" ]; then
  [ "$#" -eq 1 ] || fail "usage: scripts/verify.sh [--check-receipt]"
  check_receipt
  exit 0
fi

api_tests=()
db_tests=()
api_test_count=0
db_test_count=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --api-test)
      [ "$#" -ge 2 ] || fail "--api-test requires an api-relative test path"
      api_tests+=("$2")
      api_test_count=$((api_test_count + 1))
      shift 2
      ;;
    --db-test)
      [ "$#" -ge 2 ] || fail "--db-test requires a repository-relative db/tests path"
      db_tests+=("$2")
      db_test_count=$((db_test_count + 1))
      shift 2
      ;;
    *)
      fail "usage: scripts/verify.sh [--api-test tests/test_file.py] [--db-test db/tests/file.sql]"
      ;;
  esac
done

if [ "$api_test_count" -gt 0 ]; then
  for test in "${api_tests[@]}"; do
    test_file="${test%%::*}"
    case "$test_file" in
      tests/*.py) ;;
      *) fail "API tests must be under api/tests: $test" ;;
    esac
    [ -f "api/$test_file" ] || fail "API test does not exist: api/$test_file"
  done
fi
if [ "$db_test_count" -gt 0 ]; then
  for test in "${db_tests[@]}"; do
    case "$test" in
      db/tests/*.sql|db/tests/*_upgrade.sh) ;;
      *) fail "database tests must be under db/tests: $test" ;;
    esac
    [ -f "$test" ] || fail "database test does not exist: $test"
  done
fi

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

api_changed=
db_changed=
web_changed=
changed=
while IFS= read -r path; do
  [ -n "$path" ] || continue
  changed=1
  case "$path" in
    api/*) api_changed=1 ;;
    db/*) db_changed=1 ;;
    web/*) web_changed=1 ;;
  esac
done < <(git diff --name-only "$base" HEAD)
[ -n "$changed" ] || {
  api_changed=1
  db_changed=1
  web_changed=1
}

scope_parts=()
[ -z "$api_changed" ] || scope_parts+=(api)
[ -z "$db_changed" ] || scope_parts+=(database)
[ -z "$web_changed" ] || scope_parts+=(web)
if [ "${#scope_parts[@]}" -eq 0 ]; then
  scope=docs-only
else
  scope="$(IFS=+; echo "${scope_parts[*]}")"
fi

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
  SKIP=end-of-file-fixer,trailing-whitespace,docs,no-real-data,ruff,ruff-format,eslint \
    pre-commit run --all-files
}

run_api_tests() {
  if [ "$api_test_count" -gt 0 ]; then
    uv run pytest -q "${api_tests[@]}"
  else
    uv run pytest -q
  fi
}

run_database_gate() {
  if [ "$db_test_count" -gt 0 ]; then
    scripts/hooks/db-tests.sh "${db_tests[@]}"
  else
    scripts/hooks/db-tests.sh
  fi
}

# Every gate chains its commands with &&: run_stage calls a gate inside `if`, where bash ignores
# errexit, so a failed command in the middle of an unchained gate would pass silently.
run_api_gate() (
  cd api &&
    uv run ruff check . &&
    uv run ruff format --check . &&
    uv run mypy . &&
    run_api_tests
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

if [ -n "$api_changed" ] || [ "$api_test_count" -gt 0 ]; then
  ensure_test_server
  run_stage api run_api_gate
fi
if [ -n "$db_changed" ] || [ "$db_test_count" -gt 0 ]; then
  [ -n "$test_server_ready" ] || ensure_test_server
  run_stage database run_database_gate
fi
if [ -n "$web_changed" ]; then
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
