from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from oneledger import auth
from oneledger.main import app
from oneledger.routes import doctor

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email="o@example.com"
    )


class Connection:
    """Stands in for the connection and its cursor, and records what the count was asked."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.params: tuple[object, ...] | None = None

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def cursor(self) -> Connection:
        return self

    def execute(self, _: str, params: tuple[object, ...] | None = None) -> None:
        self.params = params

    def fetchone(self) -> tuple[int]:
        return (self.count,)


def test_the_newest_backup_is_the_latest_stamp_its_name_records(tmp_path: Path) -> None:
    for name in (
        "oneledger-20260907T111759Z.dump.enc",
        "oneledger-20260909T080000Z.dump.enc",
        # Neither of these is a backup: an unpublished temporary file and something else.
        ".oneledger-tmp.ab12cd34",
        "notes-20261231T000000Z.txt",
    ):
        (tmp_path / name).write_bytes(b"")

    assert doctor.newest_backup(tmp_path) == datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    assert doctor.newest_backup(tmp_path / "absent") is None


def test_no_backup_at_all_is_a_warning() -> None:
    finding = doctor.backup_finding(None, 4184, NOW)
    assert finding.level == "warning"
    assert "No backup" in finding.message
    assert "4,184 transactions" in finding.message


def test_a_backup_older_than_two_days_says_what_the_ledger_would_lose() -> None:
    finding = doctor.backup_finding(NOW - timedelta(days=3, hours=1), 4184, NOW)
    assert finding.level == "warning"
    assert "3 days old" in finding.message
    assert "the ledger has 4,184 transactions it does not hold" in finding.message


def test_a_backup_from_today_is_ok_and_still_counts_what_came_after() -> None:
    finding = doctor.backup_finding(NOW - timedelta(hours=5), 1, NOW)
    assert finding.level == "ok"
    assert "2026-09-10 07:00 UTC" in finding.message
    assert "1 transaction " in finding.message


def test_doctor_counts_the_owners_transactions_since_the_newest_backup(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "oneledger-20260909T080000Z.dump.enc").write_bytes(b"")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    connection = Connection(12)
    monkeypatch.setattr(doctor, "connect", lambda: connection)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/doctor")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    [section] = response.json()["sections"]
    assert section["title"] == "Backups"
    [finding] = section["findings"]
    assert "12 transactions" in finding["message"]
    assert connection.params == (HOUSEHOLD_ID, datetime(2026, 9, 9, 8, 0, tzinfo=UTC))
