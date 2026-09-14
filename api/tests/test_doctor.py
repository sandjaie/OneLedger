import os
from datetime import UTC, date, datetime, timedelta
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


def test_a_backup_missing_a_sibling_file_is_a_warning_even_when_fresh() -> None:
    finding = doctor.backup_finding(NOW - timedelta(hours=5), 1, NOW, files_complete=False)
    assert finding.level == "warning"
    assert "missing the documents archive or manifest" in finding.message
    assert "1 transaction " in finding.message


def test_a_stale_backup_still_names_the_missing_files() -> None:
    finding = doctor.backup_finding(NOW - timedelta(days=3), 1, NOW, files_complete=False)
    assert finding.level == "warning"
    assert "3 days old" in finding.message
    assert "missing the documents archive or manifest" in finding.message


def test_newest_backup_complete_checks_all_three_files_by_name(tmp_path: Path) -> None:
    stamp = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    (tmp_path / "oneledger-20260909T080000Z.dump.enc").write_bytes(b"")
    assert doctor.newest_backup_complete(tmp_path, "oneledger", stamp) is False

    (tmp_path / "oneledger-20260909T080000Z.documents.tar.enc").write_bytes(b"")
    (tmp_path / "oneledger-20260909T080000Z.manifest.json.enc").write_bytes(b"")
    assert doctor.newest_backup_complete(tmp_path, "oneledger", stamp) is True


def test_newest_drill_reads_the_most_recently_written_file(tmp_path: Path) -> None:
    assert doctor.newest_drill(tmp_path, "oneledger") is None

    older = tmp_path / "oneledger-20260901T000000Z.drill.json"
    newer = tmp_path / "oneledger-20260909T080000Z.drill.json"
    older.write_text('{"result": "fail"}')
    newer.write_text('{"result": "pass"}')
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert doctor.newest_drill(tmp_path, "oneledger") == {"result": "pass"}
    # A hand-placed or corrupt file is treated as no drill at all, not a crash.
    (tmp_path / "oneledger-20261231T000000Z.drill.json").write_text("not json")
    os.utime(tmp_path / "oneledger-20261231T000000Z.drill.json", (3, 3))
    assert doctor.newest_drill(tmp_path, "oneledger") is None


def test_no_drill_ever_run_is_a_warning() -> None:
    finding = doctor.drill_finding(None, NOW)
    assert finding.level == "warning"
    assert "No restore drill" in finding.message


def test_a_failed_drill_is_a_warning_regardless_of_age() -> None:
    drill: dict[str, object] = {"result": "fail", "drillTime": NOW.isoformat()}
    finding = doctor.drill_finding(drill, NOW)
    assert finding.level == "warning"
    assert "failed" in finding.message


def test_a_drill_older_than_thirty_one_days_is_a_warning() -> None:
    drill_time = NOW - timedelta(days=32)
    drill: dict[str, object] = {"result": "pass", "drillTime": drill_time.isoformat()}
    finding = doctor.drill_finding(drill, NOW)
    assert finding.level == "warning"
    assert "32 days ago" in finding.message


def test_a_recent_passing_drill_is_ok() -> None:
    drill_time = NOW - timedelta(days=1)
    drill: dict[str, object] = {"result": "pass", "drillTime": drill_time.isoformat()}
    finding = doctor.drill_finding(drill, NOW)
    assert finding.level == "ok"
    assert "passed" in finding.message


def test_wealth_history_findings_report_incomplete_months_and_ok_when_clean() -> None:
    assert doctor.wealth_history_findings_from_counts(0, None) == [
        doctor.Finding(level="ok", message="Every computed month of wealth history is complete.")
    ]
    findings = doctor.wealth_history_findings_from_counts(2, date(2026, 7, 31))
    assert findings == [
        doctor.Finding(
            level="warning",
            message="2 months of wealth history are incomplete, latest 2026-07.",
        )
    ]


def test_identity_backfill_finding_reports_legacy_and_current_states() -> None:
    pending = doctor.identity_backfill_finding(2)
    assert pending.level == "warning"
    assert pending.message == "2 account identities await statement backfill."
    assert doctor.identity_backfill_finding(0).model_dump() == {
        "level": "ok",
        "message": "Every account identity is on the current local model.",
    }


def test_investment_findings_report_each_issue_and_ok_when_clean() -> None:
    messages = [f.message for f in doctor.investment_findings_from_counts((2, 1, 3, 1, 0))]
    assert "2 investment import reviews block committing." in messages
    assert "1 open holding has no dated price and cannot be valued." in messages
    assert "3 open holdings have a price older than seven days." in messages
    assert "1 investment purchase has no linked cash funding." in messages
    assert not any("realised gain" in message for message in messages)
    assert doctor.investment_findings_from_counts((1, 0, 0, 0, 1)) == [
        doctor.Finding(level="warning", message="1 investment import review blocks committing."),
        doctor.Finding(
            level="warning",
            message="1 disposal is awaiting lot evidence before realised gain is available.",
        ),
    ]
    assert doctor.investment_findings_from_counts((0, 0, 0, 0, 0)) == [
        doctor.Finding(level="ok", message="No investment data-quality issues found.")
    ]


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
    monkeypatch.setattr(doctor, "investment_findings", lambda _: [])
    monkeypatch.setattr(doctor, "coverage_findings", lambda _: [])
    monkeypatch.setattr(doctor, "wealth_history_findings", lambda _: [])
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/doctor")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    section, identity, investments, coverage, wealth_history = response.json()["sections"]
    assert section["title"] == "Backups"
    finding, drill = section["findings"]
    assert "12 transactions" in finding["message"]
    assert drill == {"level": "warning", "message": "No restore drill has ever been run."}
    assert connection.params == (HOUSEHOLD_ID, datetime(2026, 9, 1, tzinfo=UTC))
    assert identity == {
        "title": "Account identities",
        "findings": [
            {"level": "ok", "message": "Every account identity is on the current local model."}
        ],
    }
    assert investments == {"title": "Investments", "findings": []}
    assert coverage == {"title": "Coverage", "findings": []}
    assert wealth_history == {"title": "Wealth history", "findings": []}
