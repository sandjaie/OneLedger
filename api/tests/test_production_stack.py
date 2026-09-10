from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FILE = ROOT / "docker-compose.production.yml"
PRODUCTION_ENV = ROOT / ".env.production.example"
PRODUCTION_SCRIPT = ROOT / "scripts" / "production-stack.sh"


def _compose_config(file: Path, env_file: Path) -> dict[str, Any]:
    # A live-test run exports POSTGRES_* for its disposable database. Shell variables outrank
    # Docker Compose's --env-file, so letting those leak here would test the test database's
    # port instead of the explicitly named production/development example configuration.
    env = os.environ.copy()
    for name in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "AUTH_MODE",
        "WEB_ORIGIN",
        "NEXT_PUBLIC_API_URL",
    ):
        env.pop(name, None)
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "oneledger-production",
            "--env-file",
            str(env_file),
            "-f",
            str(file),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, Any], json.loads(completed.stdout))


def test_production_compose_resolves_to_the_isolated_runtime() -> None:
    """Reusing a port, project, dependency, or volume could touch the working ledger."""
    config = _compose_config(PRODUCTION_FILE, PRODUCTION_ENV)
    services = config["services"]
    volumes = config["volumes"]
    assert isinstance(services, dict)
    assert isinstance(volumes, dict)
    assert set(services) == {"api", "backup", "bootstrap", "db", "migrate", "web"}
    assert config["name"] == "oneledger-production"
    assert services["db"]["ports"] == [
        {
            "mode": "ingress",
            "target": 5432,
            "published": "55432",
            "protocol": "tcp",
            "host_ip": "127.0.0.1",
        }
    ]
    assert services["api"]["ports"][0]["published"] == "8100"
    assert services["web"]["ports"][0]["published"] == "3100"
    assert services["api"]["environment"]["AUTH_MODE"] == "local"
    assert services["api"]["environment"]["WEB_ORIGIN"] == "http://localhost:3100"
    assert services["web"]["build"]["args"]["NEXT_PUBLIC_API_URL"] == "http://localhost:8100"
    assert services["migrate"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert (
        services["bootstrap"]["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )
    assert (
        services["api"]["depends_on"]["bootstrap"]["condition"] == "service_completed_successfully"
    )
    assert (
        services["backup"]["depends_on"]["bootstrap"]["condition"]
        == "service_completed_successfully"
    )
    assert volumes["pgdata"] == {"name": "oneledger-production_pgdata", "external": True}
    assert volumes["documents"] == {
        "name": "oneledger-production_documents",
        "external": True,
    }


def test_development_compose_opts_into_local_auth_without_changing_its_ports() -> None:
    """A production-stack addition must not move or deauthorize the acceptance ledger."""
    config = _compose_config(ROOT / "docker-compose.yml", ROOT / ".env.example")
    services = config["services"]
    assert services["api"]["environment"]["AUTH_MODE"] == "local"
    assert services["api"]["ports"][0]["published"] == "8000"
    assert services["db"]["ports"][0]["published"] == "5432"


def _fake_workspace(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(PRODUCTION_SCRIPT, scripts / "production-stack.sh")
    (tmp_path / "docker-compose.production.yml").write_text("services: {}\n")
    (tmp_path / ".env.production.example").write_text("ALLOWED_USER_EMAIL=owner@example.com\n")
    (tmp_path / ".env.production").write_text("ALLOWED_USER_EMAIL=owner@example.com\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'if [ "$1 $2" = "volume inspect" ]; then exit 1; fi\n'
        "exit 0\n"
    )
    docker.chmod(0o755)
    return scripts / "production-stack.sh", binary


def _run_script(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    script, binary = _fake_workspace(tmp_path)
    log = tmp_path / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
    }
    completed = subprocess.run(
        [str(script), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return completed, calls


@pytest.mark.parametrize(("command", "tail"), [("status", "ps"), ("down", "down")])
def test_lifecycle_commands_are_pinned_to_the_production_project(
    tmp_path: Path, command: str, tail: str
) -> None:
    """Dropping the project/file/env flags could make a command target development."""
    completed, calls = _run_script(tmp_path, command)
    assert completed.returncode == 0, completed.stderr
    assert calls == [
        "compose --project-name oneledger-production --env-file .env.production "
        f"-f docker-compose.production.yml {tail}"
    ]


def test_reset_refuses_without_the_exact_confirmation(tmp_path: Path) -> None:
    """A typo or bare reset must never remove any volume."""
    completed, calls = _run_script(tmp_path, "reset", "--confirm", "production")
    assert completed.returncode != 0
    assert calls == []


def test_confirmed_reset_removes_only_the_two_production_volumes(tmp_path: Path) -> None:
    """A broad or interpolated target could erase the development or test database."""
    completed, calls = _run_script(tmp_path, "reset", "--confirm", "oneledger-production")
    assert completed.returncode == 0, completed.stderr
    assert calls == [
        "compose --project-name oneledger-production --env-file .env.production "
        "-f docker-compose.production.yml down",
        "volume rm oneledger-production_pgdata",
        "volume rm oneledger-production_documents",
    ]


def test_init_creates_private_configuration_and_exact_external_volumes(
    tmp_path: Path,
) -> None:
    """Using shared secrets or implicit volume names would couple the two environments."""
    script, binary = _fake_workspace(tmp_path)
    (tmp_path / ".env.production").unlink()
    log = tmp_path / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
    }
    completed = subprocess.run(
        [str(script), "init"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    created = tmp_path / ".env.production"
    assert stat.S_IMODE(created.stat().st_mode) == 0o600
    values = dict(
        line.split("=", 1)
        for line in created.read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values["ALLOWED_USER_EMAIL"] == "owner@example.com"
    assert all(
        values[key]
        for key in (
            "POSTGRES_PASSWORD",
            "SESSION_SECRET",
            "ACCOUNT_HASH_SALT",
            "BACKUP_PASSPHRASE",
        )
    )
    assert log.read_text().splitlines() == [
        "volume inspect oneledger-production_pgdata",
        "volume create oneledger-production_pgdata",
        "volume inspect oneledger-production_documents",
        "volume create oneledger-production_documents",
    ]
