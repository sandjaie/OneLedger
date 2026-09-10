"""scripts/backup-db.sh: the schedule's tick and the retention rule.

The script is copied into a temporary tree and run there with a stand-in `pg_dump` on PATH and
an environment built from nothing, so no run here reaches a database, reads the repository's
`.env`, or touches its `backups/`.
"""

import os
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOUR = 60 * 60
DAY = 24 * HOUR
PASSPHRASE = "synthetic-passphrase"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    for name in ("backup-db.sh", "load-env.sh"):
        shutil.copy2(REPO / "scripts" / name, tmp_path / "scripts" / name)
    (tmp_path / "bin").mkdir()
    (tmp_path / "backups").mkdir()
    return tmp_path


def run(
    tree: Path,
    *args: str,
    dump: str = "echo synthetic-dump",
    prefix: str | None = None,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    pg_dump = tree / "bin" / "pg_dump"
    pg_dump.write_text(f"#!/bin/sh\n{dump}\n")
    pg_dump.chmod(0o755)
    env = {
        "PATH": f"{tree / 'bin'}:{os.environ['PATH']}",
        "BACKUP_PASSPHRASE": PASSPHRASE,
        "DATABASE_URL": "postgresql://synthetic.invalid/none",
        **(extra or {}),
    }
    if prefix:
        env["BACKUP_PREFIX"] = prefix
    return subprocess.run(
        ["bash", str(tree / "scripts" / "backup-db.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def backup(tree: Path, name: str, age_seconds: int) -> Path:
    path = tree / "backups" / name
    path.write_bytes(b"an older backup")
    then = time.time() - age_seconds
    os.utime(path, (then, then))
    return path


def backups(tree: Path, count: int, age_seconds: int) -> list[Path]:
    """`count` backups, oldest name first, one an hour from 1 July 2026, all `age_seconds` old."""
    start = datetime(2026, 7, 1, tzinfo=UTC)
    return [
        backup(tree, f"oneledger-{start + timedelta(hours=i):%Y%m%dT%H%M%SZ}.dump.enc", age_seconds)
        for i in range(count)
    ]


def names(tree: Path) -> list[str]:
    return sorted(path.name for path in (tree / "backups").iterdir())


def test_a_tick_within_a_day_of_the_last_backup_takes_none(tree: Path) -> None:
    backup(tree, "oneledger-20260910T080000Z.dump.enc", HOUR)

    result = run(tree, "--if-due")

    assert result.returncode == 0, result.stderr
    assert names(tree) == ["oneledger-20260910T080000Z.dump.enc"]


def test_run_by_hand_it_dumps_however_recent_the_last_backup(tree: Path) -> None:
    # The owner's own backup before a risky change must not be skipped as "not due".
    backup(tree, "oneledger-20260910T080000Z.dump.enc", HOUR)

    result = run(tree)

    assert result.returncode == 0, result.stderr
    assert len(names(tree)) == 2


def test_a_tick_a_day_after_the_last_backup_takes_an_encrypted_one(tree: Path) -> None:
    backup(tree, "oneledger-20260909T080000Z.dump.enc", DAY + HOUR)

    result = run(tree, "--if-due")

    assert result.returncode == 0, result.stderr
    [taken] = [name for name in names(tree) if name != "oneledger-20260909T080000Z.dump.enc"]
    decrypted = subprocess.run(
        [
            "openssl",
            "enc",
            "-d",
            "-aes-256-cbc",
            "-pbkdf2",
            "-pass",
            "env:BACKUP_PASSPHRASE",
            "-in",
            str(tree / "backups" / taken),
        ],
        env={**os.environ, "BACKUP_PASSPHRASE": PASSPHRASE},
        capture_output=True,
        check=True,
    )
    assert decrypted.stdout == b"synthetic-dump\n"


def test_another_stacks_fresh_dump_is_not_this_stacks_backup(tree: Path) -> None:
    # A scratch project run from this checkout writes beside the ledger's backups; its dump of
    # an empty database must not make the ledger's tick believe it is covered.
    backup(tree, "oneledger-verify-20260910T080000Z.dump.enc", HOUR)

    result = run(tree, "--if-due")

    assert result.returncode == 0, result.stderr
    assert [name for name in names(tree) if not name.startswith("oneledger-verify-")]
    assert run(tree, prefix="oneledger-verify").returncode == 0
    assert sum(name.startswith("oneledger-verify-") for name in names(tree)) == 2


def test_retention_keeps_the_thirty_newest_however_old_they_are(tree: Path) -> None:
    # The owner was away for more than a month and the stack was reset: the first dump is of an
    # empty database, and it must not age out every copy of the real one.
    old = backups(tree, 31, 40 * DAY)

    result = run(tree)

    assert result.returncode == 0, result.stderr
    kept = names(tree)
    assert len(kept) == 30
    assert old[0].name not in kept
    assert old[1].name not in kept
    assert old[2].name in kept


def test_retention_never_prunes_a_backup_younger_than_thirty_days(tree: Path) -> None:
    backups(tree, 35, 29 * DAY)

    result = run(tree)

    assert result.returncode == 0, result.stderr
    assert len(names(tree)) == 36


def test_a_failed_dump_prunes_nothing_and_leaves_nothing(tree: Path) -> None:
    # A job that cannot dump must not age out the last good copies while it keeps failing.
    backups(tree, 31, 40 * DAY)

    result = run(tree, dump="exit 1")

    assert result.returncode != 0
    assert len(names(tree)) == 31


def test_a_project_whose_name_extends_this_one_is_still_another_stack(tree: Path) -> None:
    # `oneledger-2-…` begins with `oneledger-` and a digit; only a whole stamp is this stack's.
    backup(tree, "oneledger-20260908T080000Z.dump.enc", 2 * DAY)
    backup(tree, "oneledger-2-20260910T080000Z.dump.enc", HOUR)

    result = run(tree, "--if-due")

    assert result.returncode == 0, result.stderr
    assert len([name for name in names(tree) if not name.startswith("oneledger-2-")]) == 2


def test_a_hand_run_in_a_scratch_shell_names_its_dump_after_that_project(tree: Path) -> None:
    # The verify recipe exports COMPOSE_PROJECT_NAME and a scratch DATABASE_URL; a dump taken
    # there is of the scratch database and must not be filed as the ledger's.
    result = run(tree, extra={"COMPOSE_PROJECT_NAME": "oneledger-verify"})

    assert result.returncode == 0, result.stderr
    [taken] = names(tree)
    assert taken.startswith("oneledger-verify-")
