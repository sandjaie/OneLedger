#!/usr/bin/env bash
# One stamped backup run (spec "One backup run, one stamp"): the database dump, the documents
# archive (the original statement PDFs — I6 provenance) and a structural evidence manifest, all
# three named `<prefix>-<stamp>.<ext>` with one shared UTC stamp, all three encrypted before they
# touch disk (security-and-privacy.html §6/§9).
#
# The manifest is computed inside a REPEATABLE READ psql session that exports its snapshot and
# stays open while `pg_dump --snapshot=<id>` runs against the very same snapshot, so the manifest
# and the dump can never describe different data. Restore with:
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE \
#     < backups/oneledger-<stamp>.dump.enc | pg_restore -d "$DATABASE_URL" --clean
# scripts/restore-drill.sh does exactly this, into isolation, and proves the result.
# On a Linux host the compose service writes these as root, mode 600: read them with sudo.
#
# Nobody has to remember to run it: the compose `backup` service calls it hourly with --if-due,
# which runs only when the newest dump is more than a day old. Run by hand it always runs.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh

# The directory Settings › Data Quality reads, so both must name the same one; compose sets it
# for both containers.
dir="${BACKUP_DIR:-backups}"
# Compose sets the prefix to the project's name, as it names the volumes: a scratch stack run
# from this checkout writes beside the ledger's backups, and must neither count as one of them
# nor be pruned as one. By hand it follows the project a shell exported, as the verify recipe
# does. The whole stamp is matched, as GET /doctor matches it: `oneledger-2-…` is not ours.
prefix="${BACKUP_PREFIX:-${COMPOSE_PROJECT_NAME:-oneledger}}"
pattern="$prefix-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump.enc"
mkdir -p "$dir"

# Measured against the newest dump's own clock rather than a timer, so a laptop asleep all night
# or a stack down for a week is caught up on the first tick after it comes back. The other two
# files of a run always share the dump's stamp, so checking the dump alone is enough.
if [ "${1:-}" = "--if-due" ] &&
   [ -n "$(find "$dir" -maxdepth 1 -name "$pattern" -mmin -$((24 * 60)) | head -n 1)" ]; then
  exit 0
fi

: "${DATABASE_URL:=$(scripts/dsn.sh)}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is unset — set it in .env; without it the backup cannot be encrypted, and an unencrypted backup is not written}"
: "${DOCUMENTS_DIR:?DOCUMENTS_DIR is unset — compose sets it for the backup service; a manual run needs it in .env too}"
export BACKUP_PASSPHRASE

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dump="$dir/$prefix-$stamp.dump.enc"
out_docs="$dir/$prefix-$stamp.documents.tar.enc"
out_manifest="$dir/$prefix-$stamp.manifest.json.enc"

for out in "$out_dump" "$out_docs" "$out_manifest"; do
  if [ -e "$out" ]; then
    echo "FAIL: $out already exists — refusing to overwrite a backup." >&2
    exit 1
  fi
done

# Written to uniquely named temporary files and published only after the whole run succeeds: a
# failure partway through must not leave a plausible-looking partial backup — or one of the
# three files without its siblings — at a final path for a retention or copy job to keep as if
# it were valid. mktemp keeps two same-second runs off each other's temp files; ln claims each
# final name atomically.
tmp_dump="$(mktemp "$dir/.$prefix-tmp.XXXXXXXX")"
tmp_docs="$(mktemp "$dir/.$prefix-tmp.XXXXXXXX")"
tmp_manifest="$(mktemp "$dir/.$prefix-tmp.XXXXXXXX")"
fifo_dir=
psql_pid=
psql_started=
plain_docs=
docs_extract=
cleanup() {
  # An EXIT trap's own exit status becomes the script's, so the real one is captured first and
  # returned last — otherwise a harmless false test below (nothing left to clean up) would
  # silently turn a successful run into a reported failure.
  local status=$?
  rm -f "$tmp_dump" "$tmp_docs" "$tmp_manifest"
  if [ -n "$psql_started" ]; then
    exec 3>&- 2>/dev/null || true
    wait "$psql_pid" 2>/dev/null || true
  fi
  [ -n "$fifo_dir" ] && rm -rf "$fifo_dir"
  # An unencrypted copy of the documents archive, or its extracted contents, must never survive
  # a failure — that would defeat "encrypted before it touches disk" as surely as publishing one.
  [ -n "$plain_docs" ] && rm -f "$plain_docs"
  [ -n "$docs_extract" ] && rm -rf "$docs_extract"
  return "$status"
}
trap cleanup EXIT

# The documents archive, and the two structural facts the manifest records about it, computed
# from that same archive's own contents rather than a second, separate directory listing — a
# plain file count and the SHA-256 of the sorted list of each file's own SHA-256 (order-
# independent, so it does not depend on filesystem traversal order, and a fixed value — the
# empty-input hash — when there are no documents yet). The tar is built once, unencrypted, into a
# temp file; the count and hash are read back from exactly what that tar holds; only then is it
# encrypted and the plaintext copy removed — so the manifest and the archive can never disagree
# about what is in it. They can still disagree about what is in DOCUMENTS_DIR at the moment the
# database snapshot below is taken a few lines later: reading the documents and taking the
# snapshot are not one atomic action, so a document added or removed in that narrow window is
# reflected in at most one of the two.
plain_docs="$(mktemp "$dir/.$prefix-tmp.XXXXXXXX")"
tar -C "$DOCUMENTS_DIR" -cf "$plain_docs" .
docs_extract="$(mktemp -d "${TMPDIR:-/tmp}/oneledger-backup-docs.XXXXXXXX")"
tar -xf "$plain_docs" -C "$docs_extract"
docs_count="$(find "$docs_extract" -type f | wc -l | tr -d ' ')"
docs_hash="$(find "$docs_extract" -type f -exec sha256sum {} + 2>/dev/null |
  awk '{print $1}' | sort | sha256sum | awk '{print $1}')"
rm -rf "$docs_extract"
docs_extract=
openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -in "$plain_docs" -out "$tmp_docs"
rm -f "$plain_docs"
plain_docs=

# The REPEATABLE READ session: a long-lived psql process so its transaction — and the snapshot
# it exports — stays open across the `pg_dump` call below, which is a separate process this
# script runs directly rather than through the session. `\echo` markers bound each answer, so a
# stray command tag (BEGIN, COMMIT) can never be mistaken for the row it precedes.
#
# ponytail: a bash `coproc` would be the one-liner for this, but this checkout's own bash
# (/bin/bash on macOS is 3.2; the rest of this codebase avoids bash-4-only syntax, e.g.
# test-db.sh uses `tr` rather than `${var,,}`) has no `coproc` builtin. Two named pipes and fixed
# file descriptors 3 (to psql) and 4 (from psql) give the identical held-open session and work
# back to bash 3.2 — the same technique bash's own `coproc` is sugar for.
fifo_dir="$(mktemp -d "${TMPDIR:-/tmp}/oneledger-backup.XXXXXXXX")"
mkfifo "$fifo_dir/in" "$fifo_dir/out"
psql "$DATABASE_URL" -X -q -t -A -v ON_ERROR_STOP=1 \
  -v docs_count="$docs_count" -v docs_hash="$docs_hash" \
  < "$fifo_dir/in" > "$fifo_dir/out" &
psql_pid=$!
psql_started=1
exec 3> "$fifo_dir/in"
exec 4< "$fifo_dir/out"

psql_ask() {
  printf '%s\n' "$1" >&3
  printf '\\echo %s\n' "$2" >&3
  local line answer=""
  while IFS= read -r line <&4; do
    [ "$line" = "$2" ] && break
    [ -n "$line" ] && answer="$line"
  done
  printf '%s' "$answer"
}

psql_ask "BEGIN ISOLATION LEVEL REPEATABLE READ;" MARK_BEGIN >/dev/null
snapshot_id="$(psql_ask "SELECT pg_export_snapshot();" MARK_SNAPSHOT)"
[ -n "$snapshot_id" ] || { echo "FAIL: could not export a database snapshot." >&2; exit 1; }
manifest_json="$(psql_ask '\i scripts/backup-manifest.sql' MARK_MANIFEST)"
[ -n "$manifest_json" ] || { echo "FAIL: the manifest query returned nothing." >&2; exit 1; }

# Runs against the exported snapshot while the psql session's transaction is still open, so it
# sees exactly the data the manifest just described — never a row committed after either point.
pg_dump -Fc --snapshot="$snapshot_id" "$DATABASE_URL" |
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -out "$tmp_dump"

# Closing stdin ends the session; its read-only transaction rolls back on its own.
exec 3>&-
wait "$psql_pid"
psql_started=
rm -rf "$fifo_dir"
fifo_dir=

printf '%s' "$manifest_json" |
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -out "$tmp_manifest"

ln "$tmp_dump" "$out_dump"
ln "$tmp_docs" "$out_docs"
ln "$tmp_manifest" "$out_manifest"

# Retention (security-and-privacy.html §9): nothing younger than thirty days is pruned, and the
# thirty newest stamps are kept however old they are. Pruned only here, after a new run has
# published, so a job that keeps failing prunes nothing. Every file of a pruned stamp goes
# together — the three backup files and, if scripts/restore-drill.sh ever wrote one for it, its
# drill.json too — none is kept without its siblings, and none is left pointing at a stamp that
# no longer exists.
find "$dir" -maxdepth 1 -name "$pattern" | sort -r | tail -n +31 |
  while IFS= read -r old_dump; do
    if find "$old_dump" -maxdepth 0 -mmin +$((30 * 24 * 60)) -print -quit | grep -q .; then
      stem="${old_dump%.dump.enc}"
      rm -f "$old_dump" "$stem.documents.tar.enc" "$stem.manifest.json.enc" "$stem.drill.json"
    fi
  done

for out in "$out_dump" "$out_docs" "$out_manifest"; do
  bytes="$(wc -c < "$out" | tr -d ' ')"
  echo "$out ($bytes bytes)"
done
