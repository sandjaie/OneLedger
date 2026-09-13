from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".pre-commit-config.yaml"
CI = ROOT / ".github/workflows/ci.yml"
VERIFY_RUNNER = ROOT / "scripts/verify.sh"
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

    assert "stages: [manual]" in _hook("markdownlint")


def test_pre_push_only_enforces_the_verification_receipt_and_diff_size() -> None:
    config = CONFIG.read_text()
    pre_push_ids = set(
        re.findall(
            r"(?ms)^      - id: ([^\n]+)\n(?:(?!^      - id: ).)*?"
            r"^        stages: \[pre-push\]$",
            config,
        )
    )

    assert pre_push_ids == {"verified-head", "diff-size"}

    for hook_id in ("pytest", "vitest", "db-invariants", "mypy", "tsc", "api-types"):
        assert f"      - id: {hook_id}\n" not in config

    assert "scripts/verify.sh --check-receipt" in _hook("verified-head")
    ci = CI.read_text()
    assert "run: scripts/verify.sh" in ci
    assert "postgresql-client" in ci
    assert "git fetch --no-tags origin develop:refs/remotes/origin/develop" in ci


def test_verify_reuses_the_isolated_test_server_for_every_database_check() -> None:
    verify = VERIFY_RUNNER.read_text()
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
    assert re.search(
        r'else\n\s+test_server_ready=1\n\s+eval "\$\(scripts/test-db\.sh up\)"',
        verify,
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _docs_only_repository(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    bin_dir.mkdir()

    shutil.copy2(VERIFY_RUNNER, repo / "scripts/verify.sh")
    (repo / "scripts/check-docs.py").write_text('print("docs-ok")\n')
    (repo / "scripts/check-diff-size.sh").write_text('#!/usr/bin/env bash\necho "size-ok"\n')
    (repo / "scripts/check-diff-size.sh").chmod(0o755)
    (repo / "docs/index.md").write_text("base\n")
    (bin_dir / "pre-commit").write_text('#!/usr/bin/env bash\necho "pre-commit-ok"\n')
    (bin_dir / "pre-commit").chmod(0o755)

    _git(repo, "init", "-b", "develop")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "docs/change")
    (repo / "docs/index.md").write_text("changed\n")
    _git(repo, "add", "docs/index.md")
    _git(repo, "commit", "-m", "docs")

    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    return repo, environment


def test_verify_without_arguments_checks_docs_only_and_writes_a_head_receipt(
    tmp_path: Path,
) -> None:
    repo, environment = _docs_only_repository(tmp_path)
    expected_head = _git(repo, "rev-parse", "HEAD")

    result = subprocess.run(
        ["bash", "scripts/verify.sh"],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"VERIFY scope=docs-only head={expected_head}" in result.stdout
    assert "PASS exact-head" in result.stdout
    assert (repo / ".git/oneledger-verified-head").read_text().strip() == expected_head


def test_verify_refuses_a_dirty_tree(tmp_path: Path) -> None:
    repo, environment = _docs_only_repository(tmp_path)
    (repo / "docs/index.md").write_text("dirty\n")

    result = subprocess.run(
        ["bash", "scripts/verify.sh"],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "working tree must be clean" in result.stderr


def test_receipt_check_refuses_a_commit_created_after_verification(tmp_path: Path) -> None:
    repo, environment = _docs_only_repository(tmp_path)
    subprocess.run(["bash", "scripts/verify.sh"], cwd=repo, env=environment, check=True)
    current = subprocess.run(
        ["bash", "scripts/verify.sh", "--check-receipt"],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert current.returncode == 0, current.stderr

    (repo / "docs/index.md").write_text("new head\n")
    _git(repo, "add", "docs/index.md")
    _git(repo, "commit", "-m", "new head")
    stale = subprocess.run(
        ["bash", "scripts/verify.sh", "--check-receipt"],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert stale.returncode != 0
    assert "HEAD has not passed scripts/verify.sh" in stale.stderr


def test_receipt_check_uses_the_commit_being_pushed(tmp_path: Path) -> None:
    repo, environment = _docs_only_repository(tmp_path)
    subprocess.run(["bash", "scripts/verify.sh"], cwd=repo, env=environment, check=True)
    environment["PRE_COMMIT_TO_REF"] = _git(repo, "rev-parse", "develop")

    result = subprocess.run(
        ["bash", "scripts/verify.sh", "--check-receipt"],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "HEAD has not passed scripts/verify.sh" in result.stderr


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
