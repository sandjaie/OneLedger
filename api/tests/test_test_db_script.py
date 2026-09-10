"""`scripts/test-db.sh` is safe from a worktree.

Parallel sessions work in worktrees under `.claude/worktrees/`, and each one is its own compose
project. On 10 September 2026 `up` from a worktree created a second `db-test` that could not
bind the main checkout's port, printed nothing to `eval`, and the live tests skipped — green
having checked nothing. `down` removed `oneledger_pgdata_test` by name, which is the main
checkout's volume, while leaving the worktree's own behind.

`docker` and `psql` are stand-ins here that record what they were asked to do.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

_DOCKER = """#!/bin/sh
echo "TEST_DB_PORT=${TEST_DB_PORT:-} $*" >> "$FAKE_LOG"
case "$*" in
  *" port "*) [ -n "${FAKE_PORT:-}" ] || exit 1; echo "$FAKE_PORT" ;;
  *" up "*) exit "${FAKE_UP_RC:-0}" ;;
esac
"""


def _run(tmp_path: pathlib.Path, command: str, **env: str) -> subprocess.CompletedProcess[str]:
    # A checkout whose directory name compose would normalise to `zen-franklin1`.
    checkout = tmp_path / "Zen-Franklin.1"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "test-db.sh", checkout / "scripts" / "test-db.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "docker": _DOCKER,
        "psql": '#!/bin/sh\nexit "${FAKE_PSQL_RC:-0}"\n',
        "sleep": "#!/bin/sh\n",
    }.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "TEST_DB_"))}
    return subprocess.run(
        ["bash", str(checkout / "scripts" / "test-db.sh"), command],
        env={
            **base,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(tmp_path / "docker.log"),
            **env,
        },
        capture_output=True,
        text=True,
    )


def _docker_calls(tmp_path: pathlib.Path) -> str:
    return (tmp_path / "docker.log").read_text()


def _eval_status(stdout: str) -> int:
    return subprocess.run(["bash", "-c", 'eval "$1"', "_", stdout]).returncode


def test_down_drops_this_checkouts_volume_and_not_the_main_ones(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "down")

    assert result.returncode == 0, result.stderr
    calls = _docker_calls(tmp_path)
    assert "volume rm -f zen-franklin1_pgdata_test" in calls
    assert "oneledger_pgdata_test" not in calls


def test_down_follows_an_explicit_project_name(tmp_path: pathlib.Path) -> None:
    _run(tmp_path, "down", COMPOSE_PROJECT_NAME="scratch-2")

    assert "volume rm -f scratch-2_pgdata_test" in _docker_calls(tmp_path)


def test_up_fails_loudly_when_the_container_does_not_start(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "up", FAKE_UP_RC="1", TEST_DB_PORT="55999")

    assert result.returncode != 0
    assert "TEST_DB_PORT" in result.stderr
    assert "ONELEDGER_TEST_DSN" not in result.stdout
    assert _eval_status(result.stdout) != 0


def test_up_fails_loudly_when_the_database_never_answers(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "up", FAKE_PSQL_RC="1", TEST_DB_PORT="55999")

    assert result.returncode != 0
    assert "55999" in result.stderr
    assert "ONELEDGER_TEST_DSN" not in result.stdout
    assert _eval_status(result.stdout) != 0


def test_env_reports_the_port_this_checkouts_container_holds(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "env", FAKE_PORT="127.0.0.1:55998")

    assert result.returncode == 0, result.stderr
    assert "127.0.0.1:55998/oneledger_test" in result.stdout
    assert "POSTGRES_PORT=55998" in result.stdout


def test_up_takes_another_port_when_the_default_is_held(tmp_path: pathlib.Path) -> None:
    held = socket.socket()
    try:
        held.bind(("127.0.0.1", 55434))
    except OSError:
        pass  # held already — by the main checkout's db-test, most likely
    try:
        result = _run(tmp_path, "up")
    finally:
        held.close()

    assert result.returncode == 0, result.stderr
    port = next(
        line.split("=", 1)[1] for line in result.stdout.splitlines() if "POSTGRES_PORT=" in line
    )
    assert port != "55434"
    # The caller's own compose calls must see it too, or they recreate db-test on the default.
    assert f"export TEST_DB_PORT={port}" in result.stdout
    # Every compose call sees the same port, or `run flyway-test` recreates db-test on the default.
    compose_calls = [line for line in _docker_calls(tmp_path).splitlines() if " port " not in line]
    assert compose_calls and all(line.startswith(f"TEST_DB_PORT={port} ") for line in compose_calls)
