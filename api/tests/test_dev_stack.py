from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "scripts" / "dev-stack.sh"


def _workspace(tmp_path: Path, *, volumes_exist: bool = True) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    assert STACK.exists(), "scripts/dev-stack.sh is missing"
    shutil.copy2(STACK, scripts / "dev-stack.sh")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ALLOWED_USER_EMAIL=owner@example.com\n")

    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    inspect_exit = 0 if volumes_exist else 1
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        f'if [ "$1 $2" = "volume inspect" ]; then exit {inspect_exit}; fi\n'
        "exit 0\n"
    )
    docker.chmod(0o755)
    return scripts / "dev-stack.sh", binary


def _run(
    tmp_path: Path, *args: str, volumes_exist: bool = True
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    script, binary = _workspace(tmp_path, volumes_exist=volumes_exist)
    log = tmp_path / "docker.log"
    env = os.environ.copy()
    env["PATH"] = f"{binary}:{env['PATH']}"
    env["DOCKER_LOG"] = str(log)
    completed = subprocess.run(
        [str(script), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return completed, calls


def test_up_reuses_the_working_ledger_and_applies_migrations(tmp_path: Path) -> None:
    completed, calls = _run(tmp_path, "up")

    assert completed.returncode == 0, completed.stderr
    assert calls == [
        "volume inspect oneledger_pgdata",
        "volume inspect oneledger_documents",
        "compose --project-name oneledger up -d --build --wait",
        "compose --project-name oneledger --profile tools run --rm flyway migrate",
    ]
    assert "http://localhost:3000" in completed.stdout
    assert "working ledger" in completed.stdout


def test_up_creates_only_missing_persistent_volumes(tmp_path: Path) -> None:
    completed, calls = _run(tmp_path, "up", volumes_exist=False)

    assert completed.returncode == 0, completed.stderr
    assert calls[:4] == [
        "volume inspect oneledger_pgdata",
        "volume create oneledger_pgdata",
        "volume inspect oneledger_documents",
        "volume create oneledger_documents",
    ]


def test_down_stops_containers_without_deleting_data(tmp_path: Path) -> None:
    completed, calls = _run(tmp_path, "down")

    assert completed.returncode == 0, completed.stderr
    assert calls == ["compose --project-name oneledger down"]
    assert "-v" not in " ".join(calls)


def test_status_reports_the_development_project(tmp_path: Path) -> None:
    completed, calls = _run(tmp_path, "status")

    assert completed.returncode == 0, completed.stderr
    assert calls == ["compose --project-name oneledger ps"]


def test_logs_can_be_scoped_to_one_service(tmp_path: Path) -> None:
    completed, calls = _run(tmp_path, "logs", "web")

    assert completed.returncode == 0, completed.stderr
    assert calls == ["compose --project-name oneledger logs --tail 200 web"]


def test_logs_show_all_services_by_default(tmp_path: Path) -> None:
    completed, calls = _run(tmp_path, "logs")

    assert completed.returncode == 0, completed.stderr
    assert calls == ["compose --project-name oneledger logs --tail 200"]


def test_invalid_arguments_do_not_call_docker(tmp_path: Path) -> None:
    completed, calls = _run(tmp_path, "reset")

    assert completed.returncode == 2
    assert calls == []
    assert "up|down|status|logs" in completed.stderr
