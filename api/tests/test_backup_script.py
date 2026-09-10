"""scripts/backup-db.sh: the schedule's tick and the retention rule.

The script is copied into a temporary tree and run there with a stand-in `pg_dump` on PATH and
an environment built from nothing, so no run here reaches a database, reads the repository's
`.env`, or touches its `backups/`.
"""

import os
import shutil
import subprocess
import time
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
    tree: Path, *args: str, dump: str = "echo synthetic-dump"
) -> subprocess.CompletedProcess[str]:
    pg_dump = tree / "bin" / "pg_dump"
    pg_dump.write_text(f"#!/bin/sh\n{dump}\n")
    pg_dump.chmod(0o755)
    env = {
        "PATH": f"{tree / 'bin'}:{os.environ['PATH']}",
        "BACKUP_PASSPHRASE": PASSPHRASE,
        "DATABASE_URL": "postgresql://synthetic.invalid/none",
    }
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


def names(tree: Path) -> list[str]:
    return sorted(path.name for path in (tree / "backups").iterdir())


def test_a_tick_within_a_day_of_the_last_backup_takes_none(tree: Path) -> None:
    backup(tree, "oneledger-20260910T080000Z.dump.enc", HOUR)

    result = run(tree, "--if-due")

    assert result.returncode == 0, result.stderr
    assert names(tree) == ["oneledger-20260910T080000Z.dump.enc"]


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


def test_a_backup_prunes_what_is_older_than_thirty_days(tree: Path) -> None:
    backup(tree, "oneledger-20260801T000000Z.dump.enc", 40 * DAY)
    backup(tree, "oneledger-20260812T000000Z.dump.enc", 29 * DAY)

    result = run(tree)

    assert result.returncode == 0, result.stderr
    kept = names(tree)
    assert "oneledger-20260801T000000Z.dump.enc" not in kept
    assert "oneledger-20260812T000000Z.dump.enc" in kept
    assert len(kept) == 2


def test_a_failed_dump_prunes_nothing_and_leaves_nothing(tree: Path) -> None:
    # A job that cannot dump must not age out the last good copies while it keeps failing.
    backup(tree, "oneledger-20260801T000000Z.dump.enc", 40 * DAY)

    result = run(tree, dump="exit 1")

    assert result.returncode != 0
    assert names(tree) == ["oneledger-20260801T000000Z.dump.enc"]
