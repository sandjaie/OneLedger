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
    """Stands in for the connection and its cursor, and records what the count was asked.

    The SQL itself is held by `test_doctor_live.py`, against a real database.
    """

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


def test_the_newest_backup_is_the_latest_stamp_its_stack_wrote(tmp_path: Path) -> None:
    for name in (
        "oneledger-20260907T111759Z.dump.enc",
        "oneledger-20260909T080000Z.dump.enc",
        # None of these is one of this stack's backups: an unpublished temporary file,
        # something else, a hand-placed name with a date that does not exist, and another
        # compose project's dump written beside the ledger's.
        ".oneledger-tmp.ab12cd34",
        "notes-20261231T000000Z.txt",
        "oneledger-20260230T000000Z.dump.enc",
        "oneledger-verify-20261231T000000Z.dump.enc",
    ):
        (tmp_path / name).write_bytes(b"")

    assert doctor.newest_backup(tmp_path) == datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    assert doctor.newest_backup(tmp_path, "oneledger-verify") == datetime(2026, 12, 31, tzinfo=UTC)
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


def test_two_days_is_where_a_missed_run_becomes_a_warning() -> None:
    assert doctor.backup_finding(NOW - timedelta(days=1, hours=23), 0, NOW).level == "ok"
    late = doctor.backup_finding(NOW - timedelta(days=2, hours=1), 0, NOW)
    assert late.level == "warning"
    assert "2 days old" in late.message


def test_a_backup_from_today_is_ok_and_still_counts_what_came_after() -> None:
    finding = doctor.backup_finding(NOW - timedelta(hours=5), 1, NOW)
    assert finding.level == "ok"
    assert "2026-09-10 07:00 UTC" in finding.message
    assert "1 transaction " in finding.message


def test_identity_backfill_finding_reports_legacy_and_current_states() -> None:
    pending = doctor.identity_backfill_finding(2)
    assert pending.level == "warning"
    assert pending.message == "2 account identities await statement backfill."
    assert doctor.identity_backfill_finding(0).model_dump() == {
        "level": "ok",
        "message": "Every account identity is on the current local model.",
    }


def test_doctor_is_refused_without_an_owner_session() -> None:
    assert TestClient(app).get("/doctor").status_code == 401


def test_doctor_counts_the_owners_transactions_since_this_stacks_newest_backup(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    # The newer file belongs to another compose project; the route reads the prefix compose
    # gave this one.
    (tmp_path / "scratch-20260901T000000Z.dump.enc").write_bytes(b"")
    (tmp_path / "oneledger-20260909T080000Z.dump.enc").write_bytes(b"")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("BACKUP_PREFIX", "scratch")
    connection = Connection(12)
    monkeypatch.setattr(doctor, "connect", lambda: connection)
    monkeypatch.setattr(doctor, "identity_backfills_remaining", lambda _: 0)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/doctor")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    section, identity = response.json()["sections"]
    assert section["title"] == "Backups"
    [finding] = section["findings"]
    assert "12 transactions" in finding["message"]
    assert connection.params == (HOUSEHOLD_ID, datetime(2026, 9, 1, tzinfo=UTC))
    assert identity == {
        "title": "Account identities",
        "findings": [
            {"level": "ok", "message": "Every account identity is on the current local model."}
        ],
    }
