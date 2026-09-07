#!/usr/bin/env bash
# Backs up the database only — a restorable snapshot of oneledger's Postgres data. It does NOT
# back up the documents volume (the original statement PDFs); that is a separate concern with
# its own lifetime and is not covered here.
#
# The dump is encrypted before it touches disk (security-and-privacy.html §6/§9: backups are
# encrypted at rest wherever they are stored). Restore with:
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE \
#     < backups/oneledger-<timestamp>.dump.enc | pg_restore -d "$DATABASE_URL" --clean
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh
: "${DATABASE_URL:=$(scripts/dsn.sh)}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is unset — set it in .env; without it the backup cannot be encrypted, and an unencrypted dump is not written}"
export BACKUP_PASSPHRASE

mkdir -p backups
out="backups/oneledger-$(date -u +%Y%m%dT%H%M%SZ).dump.enc"

if [ -e "$out" ]; then
  echo "FAIL: $out already exists — refusing to overwrite a backup." >&2
  exit 1
fi

pg_dump -Fc "$DATABASE_URL" \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -out "$out"

bytes="$(wc -c < "$out" | tr -d ' ')"
echo "$out ($bytes bytes)"
