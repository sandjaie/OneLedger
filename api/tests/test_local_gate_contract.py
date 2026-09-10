from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".pre-commit-config.yaml"


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


def test_full_safety_suite_remains_on_pre_push() -> None:
    for hook_id in (
        "markdownlint",
        "mypy",
        "tsc",
        "pytest",
        "vitest",
        "api-types",
        "diff-size",
        "db-invariants",
    ):
        assert "stages: [pre-push]" in _hook(hook_id)


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
