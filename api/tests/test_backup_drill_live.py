"""scripts/backup-db.sh and scripts/restore-drill.sh against the isolated test server: the one
proof that a backup restores rather than merely exists (security-and-privacy.html §9: "A backup
that has never been restored is a hypothesis"; spec "Restore drill"). A synthetic documents
directory and a synthetic `BACKUP_DIR` under `tmp_path` — no run here touches the checkout's own
`backups/`, `~/statements/` or any real passphrase. Skipped, like every other `*_live.py` file,
when no test database is reachable.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from .live_database import test_dsn as _test_dsn

REPO = Path(__file__).resolve().parents[2]
PASSPHRASE = "synthetic-drill-passphrase"


@pytest.fixture
def dsn() -> str:
    """The `db-test` container's own `oneledger_test` database — not pytest's own throwaway
    per-session database (conftest.py): that one is seeded by executing the migration files
    directly and has no `flyway_schema_history` table, while `scripts/test-db.sh up` migrates
    this one with real Flyway, exactly as every other environment is. `_test_dsn()` still gates
    on a reachable server, matching every other `*_live.py` file's skip."""
    _test_dsn()
    port = os.environ.get("POSTGRES_PORT") or "55434"
    return f"postgresql://oneledger:oneledger@127.0.0.1:{port}/oneledger_test"


@pytest.fixture
def stack(tmp_path: Path, dsn: str) -> dict[str, str]:
    backups = tmp_path / "backups"
    documents = tmp_path / "documents"
    backups.mkdir()
    documents.mkdir()
    (documents / "statement.pdf").write_bytes(b"synthetic statement bytes")
    (documents / "nested").mkdir()
    (documents / "nested" / "payslip.pdf").write_bytes(b"synthetic payslip bytes")
    # Unique per test run, like every scratch prefix in this codebase, so a backup left behind
    # by a failed run is never mistaken for another test's or the ledger's own.
    prefix = f"drill-{os.getpid()}-{tmp_path.name}"
    return {
        "BACKUP_DIR": str(backups),
        "BACKUP_PREFIX": prefix,
        "BACKUP_PASSPHRASE": PASSPHRASE,
        "DOCUMENTS_DIR": str(documents),
        "DATABASE_URL": dsn,
    }


def run_backup(stack: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "backup-db.sh")],
        cwd=REPO,
        env={**os.environ, **stack},
        capture_output=True,
        text=True,
        check=False,
    )


def run_drill(stack: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **stack}
    # The drill never reads DATABASE_URL — it always restores through `docker compose --profile
    # test exec db-test` (spec step 1). Dropping it here proves that rather than only asserting
    # it: if the script ever grew a path that used it, this fixture's DSN would make that path
    # look like it worked, defeating the point.
    env.pop("DATABASE_URL", None)
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "restore-drill.sh")],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def stamp_files(stack: dict[str, str], suffix: str) -> list[Path]:
    return sorted(Path(stack["BACKUP_DIR"]).glob(f"{stack['BACKUP_PREFIX']}-*.{suffix}"))


def read_drill(stack: dict[str, str]) -> dict[str, object]:
    [drill_json] = stamp_files(stack, "drill.json")
    parsed = json.loads(drill_json.read_text())
    assert isinstance(parsed, dict)
    return parsed


def test_a_synthetic_backup_produces_the_three_files(stack: dict[str, str]) -> None:
    result = run_backup(stack)
    assert result.returncode == 0, result.stderr

    files = sorted(Path(stack["BACKUP_DIR"]).glob(f"{stack['BACKUP_PREFIX']}-*"))
    suffixes = sorted(f.name.split(".", 1)[1] for f in files)
    assert suffixes == ["documents.tar.enc", "dump.enc", "manifest.json.enc"]


def test_the_drill_passes_against_a_fresh_backup(stack: dict[str, str]) -> None:
    assert run_backup(stack).returncode == 0

    result = run_drill(stack)

    assert result.returncode == 0, result.stderr + result.stdout
    drill = read_drill(stack)
    assert drill["result"] == "pass"
    items = drill["items"]
    assert isinstance(items, dict) and items
    assert all(value == "pass" for value in items.values())


def test_a_tampered_dump_is_a_failed_drill_with_a_nonzero_exit(stack: dict[str, str]) -> None:
    assert run_backup(stack).returncode == 0
    [dump] = stamp_files(stack, "dump.enc")
    original = dump.read_bytes()
    # The cipher has no integrity check (spec step 3): corrupting the tail must not restore
    # silently wrong data, it must fail loudly.
    dump.write_bytes(original[:-32] + bytes(32))

    result = run_drill(stack)

    assert result.returncode != 0
    drill = read_drill(stack)
    assert drill["result"] == "fail"
    assert drill["items"]["decryptAndRestoreDump"] == "fail"  # type: ignore[index]


def test_a_tampered_document_is_a_failed_drill_with_a_nonzero_exit(stack: dict[str, str]) -> None:
    assert run_backup(stack).returncode == 0
    [archive] = stamp_files(stack, "documents.tar.enc")
    original = archive.read_bytes()
    archive.write_bytes(original[:-32] + bytes(32))

    result = run_drill(stack)

    assert result.returncode != 0
    drill = read_drill(stack)
    assert drill["result"] == "fail"


def test_the_drill_refuses_anything_but_the_isolated_test_server() -> None:
    """Structural proof of spec step 1 ("refuses any target that is not the isolated test
    server"): every database command the script runs goes through `docker compose --profile
    test exec db-test`, and it never opens `$DATABASE_URL` or references the production compose
    file — so there is no code path here that could reach the working ledger, whatever a caller
    sets. `test_the_drill_passes_against_a_fresh_backup` already runs it with $DATABASE_URL
    removed and proves that changes nothing."""
    code_lines = [
        line
        for line in (REPO / "scripts" / "restore-drill.sh").read_text().splitlines()
        if not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "DATABASE_URL" not in code
    assert "docker-compose.production" not in code
    # Join line continuations so a wrapped `compose exec -T db-test \` + `psql ...` reads as one
    # logical statement — otherwise the tool name and the `compose exec` that runs it can land on
    # different physical lines and this check would miss the connection between them.
    logical_lines = "\n".join(code_lines).replace("\\\n", " ").splitlines()
    for tool in ("psql", "pg_restore", "createdb", "dropdb"):
        for line in logical_lines:
            if f" {tool} " in f" {line} ":
                assert "compose exec" in line or "compose(" in line, line
