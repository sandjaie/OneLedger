#!/usr/bin/env bash
# A statement must never be tracked by git. This is an invariant, not a
# preference: see docs/operations/security-and-privacy.html §1.
set -euo pipefail

offenders="$(git ls-files | grep -iE '\.pdf$|^pdf-statements/' || true)"

if [ -n "$offenders" ]; then
  echo "FAIL: real financial data is tracked by git:"
  echo "$offenders" | sed 's/^/  /'
  echo
  echo "Remove it from the index and keep it out:  git rm --cached <file>"
  exit 1
fi

echo "OK"
