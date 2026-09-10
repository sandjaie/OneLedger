#!/usr/bin/env bash
# Backs up the database only — a restorable snapshot of oneledger's Postgres data. It does NOT
# back up the documents volume (the original statement PDFs); that is a separate concern with
# its own lifetime and is not covered here.
#
# The dump is encrypted before it touches disk (security-and-privacy.html §6/§9: backups are
# encrypted at rest wherever they are stored). Restore with:
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE \
#     < backups/oneledger-<timestamp>.dump.enc | pg_restore -d "$DATABASE_URL" --clean
# On a Linux host the compose service writes its dumps as root, mode 600: read them with sudo.
#
# Nobody has to remember to run it: the compose `backup` service calls it hourly with
# --if-due, which dumps only when the newest backup is more than a day old. Run by hand it
# always dumps.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh

# The directory the API reads for Settings › Data Quality, so both must name the same one;
# compose sets it for both containers.
dir="${BACKUP_DIR:-backups}"
# Compose sets the prefix to the project's name, as it names the volumes: a scratch stack run
# from this checkout writes beside the ledger's backups, and must neither count as one of them
# nor be pruned as one. By hand it follows the project a shell exported, as the verify recipe
# does. The whole stamp is matched, as GET /doctor matches it: `oneledger-2-…` is not ours.
prefix="${BACKUP_PREFIX:-${COMPOSE_PROJECT_NAME:-oneledger}}"
pattern="$prefix-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump.enc"
mkdir -p "$dir"

# Measured against the newest file's own clock rather than a timer, so a laptop asleep all
# night or a stack down for a week is caught up on the first tick after it comes back.
if [ "${1:-}" = "--if-due" ] &&
   [ -n "$(find "$dir" -maxdepth 1 -name "$pattern" -mmin -$((24 * 60)) | head -n 1)" ]; then
  exit 0
fi

: "${DATABASE_URL:=$(scripts/dsn.sh)}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is unset — set it in .env; without it the backup cannot be encrypted, and an unencrypted dump is not written}"
export BACKUP_PASSPHRASE

out="$dir/$prefix-$(date -u +%Y%m%dT%H%M%SZ).dump.enc"

if [ -e "$out" ]; then
  echo "FAIL: $out already exists — refusing to overwrite a backup." >&2
  exit 1
fi

# Write to a uniquely named temporary file and publish only after the whole pipeline
# succeeds: a pg_dump that fails mid-stream must not leave a plausible-looking partial
# backup at the final path for a retention or copy job to preserve as if it were valid.
# mktemp keeps two same-second invocations off each other's temp file, and ln claims the
# final name atomically — it fails outright if a concurrent run published first.
tmp="$(mktemp "$dir/.$prefix-tmp.XXXXXXXX")"
trap 'rm -f "$tmp"' EXIT
pg_dump -Fc "$DATABASE_URL" \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -out "$tmp"
ln "$tmp" "$out"

# Retention (security-and-privacy.html §9): nothing younger than thirty days is pruned, and the
# thirty newest are kept however old they are. Pruned only here, after a new backup is
# published, so a job that keeps failing prunes nothing. The count is for the job that
# succeeds on the wrong database: after a reset, the real copies survive thirty further dumps
# of an empty ledger, a month of daily ones, rather than the first. The stamp sorts in time order.
find "$dir" -maxdepth 1 -name "$pattern" | sort -r | tail -n +31 |
  while IFS= read -r old; do
    find "$old" -maxdepth 0 -mmin +$((30 * 24 * 60)) -delete
  done

bytes="$(wc -c < "$out" | tr -d ' ')"
echo "$out ($bytes bytes)"
