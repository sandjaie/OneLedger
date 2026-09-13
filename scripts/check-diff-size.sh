#!/usr/bin/env bash
# A pull request converges in the number of rounds its size deserves. Task 3 of M2 reached
# +3,380 lines and took eight review rounds; every push handed the reviewer a fresh 3,000-line
# diff to re-read. This gate refuses that shape at push time, while splitting is still cheap.
#
# The budget counts implementation and test code, and only that. Documentation, configuration
# and generated files are excluded — the same exclusions Phase 0's cap used
# (docs/superpowers/phase-0-pull-requests.md), so a milestone plan or a regenerated API type
# file never pushes a branch over.
#
#   soft budget   1500 production lines added, 3000 production + test lines added
#   hard stop     2000 / 4000 — refuses the push
#
# Loosened 2026-09-14 for single-owner speed: related plan tasks are grouped into one branch and
# reviewed once. The hard stop remains because one reviewer still reads the whole diff.
#
# Over the hard stop, an owner override takes a reason and records it:
#
#   ONELEDGER_SIZE_OVERRIDE="v13 is one migration and cannot be split" git push
#
# The reason belongs in the PR's Notes section, not only in a shell.
set -euo pipefail

SOFT_PROD=${ONELEDGER_SOFT_PROD:-1500}
SOFT_TOTAL=${ONELEDGER_SOFT_TOTAL:-3000}
HARD_PROD=${ONELEDGER_HARD_PROD:-2000}
HARD_TOTAL=${ONELEDGER_HARD_TOTAL:-4000}

# Git's pre-push contract puts each pushed ref on stdin; pre-commit consumes that and
# re-exposes it. Measure what is actually being pushed, not what happens to be checked out —
# `git push origin feature/large` from a small branch would otherwise measure the small one.
branch=${PRE_COMMIT_LOCAL_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}
branch=${branch#refs/heads/}
tip=${PRE_COMMIT_TO_REF:-HEAD}

case "$tip" in
  # A deletion pushes the null object id; there is nothing to measure.
  *[!0]*) ;;
  ????????*) echo "branch deletion — nothing to measure; skipping"; exit 0 ;;
esac

case "$branch" in
  develop|main|release/*|HEAD|"")
    echo "on ${branch:-a detached head} — the budget applies to task branches; skipping"
    exit 0
    ;;
esac

# The budget measures this branch against what it will merge into. Without a reachable
# integration branch there is nothing to measure, and a push is not the place to fail over it.
base_ref=${ONELEDGER_DIFF_BASE:-origin/develop}
if ! git rev-parse --verify --quiet "$base_ref" >/dev/null; then
  base_ref=origin/main
  git rev-parse --verify --quiet "$base_ref" >/dev/null || {
    echo "no origin/develop or origin/main to measure against — skipping"
    exit 0
  }
fi
base=$(git merge-base "$base_ref" "$tip") || {
  echo "no merge base with $base_ref (shallow clone?) — skipping"
  exit 0
}

report=$(git diff --numstat --no-renames "$base" "$tip" | awk -F'\t' '
  # $1 added, $2 deleted, $3 path, tab-separated. Binary files report "-" and are not code.
  $1 == "-" { next }
  {
    path = $3
    kind = "prod"

    # Documentation and anything generated: outside the budget, wherever it sits.
    if (path ~ /(^|\/)docs\// ||
        path ~ /\.(md|html)$/ ||
        path ~ /(^|\/)\.claude\// ||
        path ~ /(^|\/)\.github\// ||
        path ~ /^web\/types\/api\// ||
        path ~ /\.gen\.(ts|tsx)$/ ||
        path ~ /(package-lock\.json|uv\.lock|\.lock)$/)
      kind = "skip"

    # Tests and their fixtures count, but against the wider budget only: TDD earns its lines.
    # Checked before configuration, because an expected-output fixture is JSON or YAML far more
    # often than not, and a 1,500-line one is a review to read however it is spelled.
    else if (path ~ /(^|\/)tests?\// ||
             path ~ /(^|\/)__tests__\// ||
             path ~ /\.(test|spec)\.(ts|tsx|js|jsx|py)$/ ||
             path ~ /(^|\/)conftest\.py$/ ||
             path ~ /(^|\/)fixtures\//)
      kind = "test"

    # Configuration: outside the budget, once it is known not to be a fixture.
    else if (path ~ /\.(ya?ml|json|toml|cfg|ini)$/ ||
             path ~ /(^|\/)(Dockerfile|\.env\.example|\.gitignore|\.dockerignore)$/)
      kind = "skip"

    added[kind] += $1
    deleted[kind] += $2
    if (kind != "skip") { files[path] = $1 }
  }
  END {
    printf "%d %d %d %d\n", added["prod"], added["test"], deleted["prod"], deleted["test"]
    for (p in files) printf "FILE\t%d\t%s\n", files[p], p
  }
')

read -r prod tests del_prod del_tests <<<"$(echo "$report" | head -1)"
prod=${prod:-0}; tests=${tests:-0}; del_prod=${del_prod:-0}; del_tests=${del_tests:-0}
total=$((prod + tests))

echo "diff size vs $base_ref ($(git rev-parse --short "$base"))"
echo "  production  +${prod} -${del_prod}   (budget ${SOFT_PROD}, hard stop ${HARD_PROD})"
echo "  tests       +${tests} -${del_tests}"
echo "  counted     +${total}            (budget ${SOFT_TOTAL}, hard stop ${HARD_TOTAL})"

over_hard=0
[ "$prod" -gt "$HARD_PROD" ] && over_hard=1
[ "$total" -gt "$HARD_TOTAL" ] && over_hard=1
over_soft=0
[ "$prod" -gt "$SOFT_PROD" ] && over_soft=1
[ "$total" -gt "$SOFT_TOTAL" ] && over_soft=1

if [ "$over_soft" = 1 ]; then
  echo ""
  echo "  largest counted files:"
  echo "$report" | awk -F'\t' '$1 == "FILE" { printf "%s\t%s\n", $2, $3 }' |
    sort -rn | head -8 | awk -F'\t' '{ printf "    +%-6s %s\n", $1, $2 }'
fi

if [ "$over_hard" = 1 ]; then
  if [ -n "${ONELEDGER_SIZE_OVERRIDE:-}" ]; then
    echo ""
    echo "OVER the hard stop, allowed by owner override:"
    echo "  ${ONELEDGER_SIZE_OVERRIDE}"
    echo "Record that reason in the PR's Notes section."
    exit 0
  fi
  echo ""
  echo "FAIL: this branch is past the hard stop. Split it before pushing."
  echo "      One reviewer reads the whole diff; past this size findings get missed. Move the"
  echo "      unfinished remainder onto the next branch off develop and ship them in order."
  echo ""
  echo "      If it genuinely cannot be split — one migration, one generated surface — push with"
  echo "      the reason, and put the same reason in the PR:"
  echo "        ONELEDGER_SIZE_OVERRIDE=\"why\" git push -u origin $branch"
  exit 1
fi

if [ "$over_soft" = 1 ]; then
  echo ""
  echo "WARNING: over budget, under the hard stop. Expect a slower review."
  echo "         Split it if the remaining work has not landed yet."
fi

echo "OK: within budget"
