#!/usr/bin/env bash
# Proves a backup restores rather than merely existing (security-and-privacy.html §9: "A backup
# that has never been restored is a hypothesis"; spec "Restore drill"). Restores the newest stamp
# that has all three files into the isolated test server — a temporary database and a temporary
# document directory — and compares a freshly computed manifest against the one recorded when the
# backup was taken. Run by hand; never part of scripts/verify.sh.
#
# Every command that touches a database runs through `docker compose --profile test exec db-test`
# — never against $DATABASE_URL or the working ledger's own connection — so a target that is not
# the isolated test server is not merely refused, it is structurally unreachable from here: this
# script has no code path that can open a connection anywhere else. `scripts/test-db.sh` supplies
# the one database it will ever touch.
#
# `<prefix>-<stamp>.drill.json` is always written once a stamp has been picked (spec step 5),
# never only on the happy path: `cleanup` writes a plain-shell fallback — no python, no database,
# nothing that can itself fail in a new way — naming the stage reached, whenever the real one
# (written by the Python step near the end) never got the chance to.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh

dir="${BACKUP_DIR:-backups}"
prefix="${BACKUP_PREFIX:-${COMPOSE_PROJECT_NAME:-oneledger}}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is unset — set it in .env}"
export BACKUP_PASSPHRASE

# The strict stamp pattern scripts/backup-db.sh itself writes: a whole stamp, not merely a name
# that starts with this prefix. A loose `$prefix-*` glob would let "oneledger-verify-…" or
# "oneledger-2-…" — different stacks whose names happen to extend this one — pass as "oneledger"'s
# own newest backup, which is exactly how another project's dump would end up drilled and named as
# if it belonged to the ledger.
dump_pattern="$prefix-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump.enc"

# The newest stamp that has all three files of a run (spec step 2). A stamp missing a sibling —
# a run that failed partway — is skipped rather than drilled as if it were complete.
newest_stem=""
for dump in $(find "$dir" -maxdepth 1 -name "$dump_pattern" 2>/dev/null | sort -r); do
  stem="${dump%.dump.enc}"
  if [ -e "$stem.documents.tar.enc" ] && [ -e "$stem.manifest.json.enc" ]; then
    newest_stem="$stem"
    break
  fi
done
if [ -z "$newest_stem" ]; then
  echo "FAIL: no backup stamp in $dir has all three files (dump, documents, manifest)." >&2
  exit 1
fi
stamp="$(basename "$newest_stem")"
stamp="${stamp#"$prefix"-}"
drill_out="$dir/$prefix-$stamp.drill.json"

compose() { docker compose --profile test "$@"; }
started_here=
temp_db=
temp_docs=
drill_written=
stage="starting the isolated test server"

write_fallback_drill() {
  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{\n  "stamp": "%s",\n  "drillTime": "%s",\n  "items": {"%s": "fail"},\n  "result": "fail"\n}\n' \
    "$stamp" "$now" "$stage" >"$drill_out"
}

cleanup() {
  # An EXIT trap's own exit status becomes the script's; captured first and returned last so
  # nothing below can turn a real failure into a reported success, or vice versa.
  local status=$?
  if [ -n "$temp_db" ]; then
    compose exec -T db-test dropdb -U oneledger --if-exists "$temp_db" >/dev/null 2>&1 || true
  fi
  [ -n "$temp_docs" ] && rm -rf "$temp_docs"
  if [ "$status" -ne 0 ] && [ -z "$drill_written" ]; then
    write_fallback_drill
  fi
  [ -n "$started_here" ] && { scripts/test-db.sh down >/dev/null 2>&1 || true; }
  return "$status"
}
trap cleanup EXIT

# Reuses db-test if it is already running for something else, starts it otherwise — and only
# stops what it itself started (spec step 1: "never runs down on a server it did not start").
if [ -n "$(compose ps -q db-test 2>/dev/null)" ]; then
  eval "$(scripts/test-db.sh env)"
else
  eval "$(scripts/test-db.sh up)"
  started_here=1
fi
[[ "$POSTGRES_DB" == *_test ]] ||
  { echo "FAIL: refusing — the isolated test server did not report a _test database." >&2; exit 1; }

stage="creating the temporary database"
temp_db="drill_$$_$(date -u +%s)"
temp_docs="$(mktemp -d "${TMPDIR:-/tmp}/oneledger-drill.XXXXXXXX")"
compose exec -T db-test createdb -U oneledger "$temp_db"

stage="restoring the dump and documents"
# Any decrypt, tar or pg_restore error here is a failed drill (spec step 3) — the cipher has no
# integrity check, so a tampered file fails loudly rather than restoring silently wrong data.
# `set -e` is relaxed around exactly these steps so a failure is recorded, not just a crash.
set +e
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE <"$newest_stem.dump.enc" 2>/dev/null |
  compose exec -T db-test pg_restore -U oneledger -d "$temp_db" --no-owner
restore_status=$?
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE <"$newest_stem.documents.tar.enc" 2>/dev/null |
  tar -x -C "$temp_docs"
extract_status=$?
set -e

stage="comparing the manifest"
# Item-by-item comparison (spec step 4) needs a recomputed manifest, which needs a database that
# actually restored and a document directory that actually extracted.
recorded_manifest="$(openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE \
  <"$newest_stem.manifest.json.enc" 2>/dev/null || true)"

recomputed_manifest="null"
if [ "$restore_status" -eq 0 ] && [ "$extract_status" -eq 0 ]; then
  docs_count="$(find "$temp_docs" -type f | wc -l | tr -d ' ')"
  docs_hash="$(find "$temp_docs" -type f -exec sha256sum {} + 2>/dev/null |
    awk '{print $1}' | sort | sha256sum | awk '{print $1}')"
  recomputed_manifest="$(compose exec -T db-test \
    psql -U oneledger -d "$temp_db" -X -q -t -A \
      -v docs_count="$docs_count" -v docs_hash="$docs_hash" \
    <scripts/backup-manifest.sql)"
fi

# `datetime.UTC` needs Python 3.11; `timezone.utc` has always worked, and this must run on
# whatever python3 the owner's host has, not this checkout's own pinned version.
result="$(python3 - "$stamp" "$drill_out" "$recorded_manifest" "$recomputed_manifest" \
  "$restore_status" "$extract_status" <<'PY'
import json
import sys
from datetime import datetime, timezone

stamp, out_path, recorded_raw, recomputed_raw, restore_status, extract_status = sys.argv[1:7]

items = {
    "decryptAndRestoreDump": "pass" if restore_status == "0" else "fail",
    "decryptAndExtractDocuments": "pass" if extract_status == "0" else "fail",
}

try:
    recorded = json.loads(recorded_raw) if recorded_raw.strip() else None
except ValueError:
    recorded = None
    items["decryptManifest"] = "fail"
else:
    items["decryptManifest"] = "pass" if recorded is not None else "fail"

try:
    recomputed = json.loads(recomputed_raw) if recomputed_raw.strip() else None
except ValueError:
    recomputed = None

if recorded is not None and recomputed is not None:
    for key in ("flywayVersion", "tableCounts", "amountSums", "documents"):
        items[key] = "pass" if recorded.get(key) == recomputed.get(key) else "fail"
else:
    for key in ("flywayVersion", "tableCounts", "amountSums", "documents"):
        items.setdefault(key, "fail")

overall = "pass" if items and all(v == "pass" for v in items.values()) else "fail"

with open(out_path, "w") as f:
    json.dump(
        {
            "stamp": stamp,
            "drillTime": datetime.now(timezone.utc).isoformat(),
            "items": items,
            "result": overall,
        },
        f,
        indent=2,
    )
    f.write("\n")

print(overall)
PY
)"
drill_written=1

echo "$drill_out (stamp $stamp): $result"
[ "$result" = "pass" ]
