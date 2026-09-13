from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".pre-commit-config.yaml"
CI = ROOT / ".github/workflows/ci.yml"
VERIFY = ROOT / ".claude/skills/verify/SKILL.md"
PRODUCTION = ROOT / "scripts/production-stack.sh"
COMPOSE = ROOT / "docker-compose.yml"


def _hook(hook_id: str) -> str:
    text = CONFIG.read_text()
    marker = f"      - id: {hook_id}\n"
    start = text.index(marker)
    end = text.find("\n      - id: ", start + len(marker))
    return text[start:] if end == -1 else text[start:end]


def test_commit_stage_is_staged_file_fast() -> None:
    for hook_id in ("ruff", "ruff-format", "eslint"):
        block = _hook(hook_id)
        assert "pass_filenames: false" not in block
        assert "stages: [pre-push]" not in block

    for hook_id in ("markdownlint", "mypy", "tsc"):
        assert "stages: [pre-push]" in _hook(hook_id)


def test_static_checks_remain_on_pre_push_and_suites_remain_in_verify_ci() -> None:
    for hook_id in (
        "markdownlint",
        "mypy",
        "tsc",
        "api-types",
        "diff-size",
    ):
        assert "stages: [pre-push]" in _hook(hook_id)

    config = CONFIG.read_text()
    for hook_id in ("pytest", "vitest", "db-invariants"):
        assert f"      - id: {hook_id}\n" not in config

    verify = VERIFY.read_text()
    ci = CI.read_text()
    for command in ("uv run pytest -q", "scripts/hooks/db-tests.sh", "npx vitest run"):
        assert command in verify
    for command in ("uv run pytest -q", "scripts/hooks/db-tests.sh", "npm run test"):
        assert command in ci


def test_markdown_lint_remains_in_ci_and_verify() -> None:
    command = "pre-commit run markdownlint --all-files --hook-stage pre-push"
    assert command in CI.read_text()
    assert command in VERIFY.read_text()


def test_verify_reuses_the_isolated_test_server_for_every_database_check() -> None:
    verify = VERIFY.read_text()
    production_port = re.search(r"POSTGRES_PORT=(\d+)", PRODUCTION.read_text())
    development_port = re.search(r"\$\{DB_PORT:-(\d+)\}", COMPOSE.read_text())
    test_port = re.search(r"\$\{TEST_DB_PORT:-(\d+)\}", COMPOSE.read_text())

    assert production_port is not None
    assert development_port is not None
    assert test_port is not None
    assert test_port.group(1) not in {production_port.group(1), development_port.group(1)}
    assert 'eval "$(scripts/test-db.sh up)"' in verify
    assert 'eval "$(scripts/test-db.sh env)"' in verify
    assert "scripts/hooks/db-tests.sh" in verify
    assert "COMPOSE_PROJECT_NAME=oneledger-verify" not in verify
    assert re.search(r"(?m)^\s*(?:export\s+)?DB_PORT=", verify) is None


def _recording_tool(tmp_path: Path, name: str) -> tuple[Path, dict[str, str]]:
    capture = tmp_path / f"{name}.args"
    tool = tmp_path / name
    tool.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    tool.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"
    environment["CAPTURE"] = str(capture)
    return capture, environment


def test_api_hook_translates_staged_paths_after_chdir(tmp_path: Path) -> None:
    capture, environment = _recording_tool(tmp_path, "uv")

    subprocess.run(
        [
            "scripts/hooks/api.sh",
            "ruff",
            "check",
            "--fix",
            "api/oneledger/main.py",
            "api/tests/test_health.py",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )

    assert capture.read_text().splitlines() == [
        "run",
        "ruff",
        "check",
        "--fix",
        "oneledger/main.py",
        "tests/test_health.py",
    ]


def test_web_lint_hook_translates_and_forwards_staged_paths(tmp_path: Path) -> None:
    capture, environment = _recording_tool(tmp_path, "npx")

    subprocess.run(
        [
            "scripts/hooks/web.sh",
            "lint",
            "web/app/page.tsx",
            "web/lib/api.ts",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )

    assert capture.read_text().splitlines() == [
        "--no-install",
        "eslint",
        "app/page.tsx",
        "lib/api.ts",
    ]
