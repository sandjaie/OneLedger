#!/usr/bin/env bash
# Markdown lint. Rules live in `.markdownlint-cli2.jsonc`; this only finds a runner.
#
# `markdownlint-cli2` is not a repository dependency: nothing this project ships imports it,
# and adding it to `web/package.json` would make a documentation check a web build concern.
# It is resolved from `web/node_modules` when a developer already installed it there, and
# otherwise from npm's cache via `npx`. Where neither can run — no Node, no network on a
# fresh clone — the hook says so and passes, because a commit must not be blocked by a
# checker that could not be fetched. CI installs Node, so the gate still holds there.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ "$#" -eq 0 ]; then exit 0; fi

runner=""
if [ -x web/node_modules/.bin/markdownlint-cli2 ]; then
  runner="web/node_modules/.bin/markdownlint-cli2"
elif command -v markdownlint-cli2 >/dev/null 2>&1; then
  runner="markdownlint-cli2"
elif command -v npx >/dev/null 2>&1; then
  runner="npx --yes markdownlint-cli2@0.23.2"
else
  echo "markdownlint-cli2 unavailable (no Node) — markdown not checked" >&2
  exit 0
fi

if ! $runner "$@"; then
  echo "" >&2
  echo "Rules are in .markdownlint-cli2.jsonc. Fix the markdown rather than widening a rule." >&2
  exit 1
fi
