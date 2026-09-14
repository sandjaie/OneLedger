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
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh

dir="${BACKUP_DIR:-backups}"
prefix="${BACKUP_PREFIX:-${COMPOSE_PROJECT_NAME:-oneledger}}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is unset — set it in .env}"
export BACKUP_PASSPHRASE

# The newest stamp that has all three files of a run (spec step 2). A stamp missing a sibling —
# a run that failed partway — is skipped rather than drilled as if it were complete.
newest_stem=""
for dump in $(find "$dir" -maxdepth 1 -name "$prefix-*.dump.enc" 2>/dev/null | sort -r); do
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

# Reuses db-test if it is already running for something else, starts it otherwise — and only
# stops what it itself started (spec step 1: "never runs down on a server it did not start").
compose() { docker compose --profile test "$@"; }
started_here=
if [ -n "$(compose ps -q db-test 2>/dev/null)" ]; then
  eval "$(scripts/test-db.sh env)"
else
  eval "$(scripts/test-db.sh up)"
  started_here=1
fi
[[ "$POSTGRES_DB" == *_test ]] ||
  { echo "FAIL: refusing — the isolated test server did not report a _test database." >&2; exit 1; }

temp_db="drill_$$_$(date -u +%s)"
temp_docs="$(mktemp -d "${TMPDIR:-/tmp}/oneledger-drill.XXXXXXXX")"

cleanup() {
  compose exec -T db-test dropdb -U oneledger --if-exists "$temp_db" >/dev/null 2>&1 || true
  rm -rf "$temp_docs"
  [ -n "$started_here" ] && scripts/test-db.sh down >/dev/null 2>&1 || true
}
trap cleanup EXIT

compose exec -T db-test createdb -U oneledger "$temp_db"

# Any decrypt, tar or pg_restore error here is a failed drill (spec step 3) — the cipher has no
# integrity check, so a tampered file fails loudly rather than restoring silently wrong data.
# `set -e` is relaxed around exactly these steps so a failure is recorded, not just a crash.
set +e
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE < "$newest_stem.dump.enc" 2>/dev/null |
  compose exec -T db-test pg_restore -U oneledger -d "$temp_db" --no-owner
restore_status=$?
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE < "$newest_stem.documents.tar.enc" 2>/dev/null |
  tar -x -C "$temp_docs"
extract_status=$?
set -e

# Item-by-item comparison (spec step 4) needs a recomputed manifest, which needs a database that
# actually restored and a document directory that actually extracted.
recorded_manifest="$(openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE \
  < "$newest_stem.manifest.json.enc" 2>/dev/null || true)"

recomputed_manifest="null"
if [ "$restore_status" -eq 0 ] && [ "$extract_status" -eq 0 ]; then
  docs_count="$(find "$temp_docs" -type f | wc -l | tr -d ' ')"
  docs_hash="$(find "$temp_docs" -type f -exec sha256sum {} + 2>/dev/null |
    awk '{print $1}' | sort | sha256sum | awk '{print $1}')"
  recomputed_manifest="$(compose exec -T db-test \
    psql -U oneledger -d "$temp_db" -X -q -t -A \
      -v docs_count="$docs_count" -v docs_hash="$docs_hash" \
    < scripts/backup-manifest.sql)"
fi

result="$(python3 - "$stamp" "$drill_out" "$recorded_manifest" "$recomputed_manifest" \
  "$restore_status" "$extract_status" <<'PY'
import json
import sys
from datetime import UTC, datetime

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
            "drillTime": datetime.now(UTC).isoformat(),
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

echo "$drill_out (stamp $stamp): $result"
[ "$result" = "pass" ]
