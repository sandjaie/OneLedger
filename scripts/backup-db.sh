#!/usr/bin/env bash
# Backs up the database only — a restorable snapshot of oneledger's Postgres data. It does NOT
# back up the documents volume (the original statement PDFs); that is a separate concern with
# its own lifetime and is not covered here.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh
: "${DATABASE_URL:=$(scripts/dsn.sh)}"

mkdir -p backups
out="backups/oneledger-$(date -u +%Y%m%dT%H%M%SZ).dump"

if [ -e "$out" ]; then
  echo "FAIL: $out already exists — refusing to overwrite a backup." >&2
  exit 1
fi

pg_dump -Fc "$DATABASE_URL" -f "$out"

bytes="$(wc -c < "$out" | tr -d ' ')"
echo "$out ($bytes bytes)"
