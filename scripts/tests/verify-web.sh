#!/usr/bin/env bash
# Exercise the web-only scope guard without running the expensive verification stages.
set -euo pipefail

source_root="$(git rev-parse --show-toplevel)"
verifier="$source_root/scripts/verify-web.sh"
fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT

git init -q -b develop "$fixture"
cd "$fixture"
printf 'fixture\n' > README.md
git add README.md
git -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm base

commit_fixture() {
  git add .
  git -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm "$1"
}

expect_allowed() {
  if ! "$verifier" --scope-only; then
    echo "expected web-only scope to be accepted: $1" >&2
    exit 1
  fi
}

expect_refused() {
  if "$verifier" --scope-only; then
    echo "expected web-only scope to be refused: $1" >&2
    exit 1
  fi
}

git checkout -q -b feature/web
mkdir -p web/app docs
printf 'export default function Page() { return null }\n' > web/app/page.tsx
printf '# Web note\n' > docs/web.md
commit_fixture 'web and docs'
expect_allowed 'web and docs'

mkdir -p api
printf 'pass\n' > api/unsafe.py
commit_fixture 'api change'
expect_refused 'api change'

git checkout -q develop
git checkout -q -b feature/database
mkdir -p web/app db
printf 'export default function Page() { return null }\n' > web/app/page.tsx
printf 'SELECT 1;\n' > db/unsafe.sql
commit_fixture 'web and database'
expect_refused 'database change'

git checkout -q develop
git checkout -q -b feature/docs
mkdir -p docs
printf '# Documentation only\n' > docs/only.md
commit_fixture 'docs only'
expect_refused 'docs-only change'

git checkout -q develop
git checkout -q -b feature/other-script
mkdir -p web/app scripts
printf 'export default function Page() { return null }\n' > web/app/page.tsx
printf '#!/usr/bin/env bash\n' > scripts/other.sh
commit_fixture 'web and unrelated script'
expect_refused 'unrelated script change'

echo 'PASS web-only scope guard'
