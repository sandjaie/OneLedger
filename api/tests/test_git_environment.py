"""The suite runs under git's pre-push hook, and must never touch the repository that hook is for.

A hook runs with `GIT_DIR` exported — and, from a linked worktree, `GIT_WORK_TREE` — so that the
git commands it starts find the repository. pytest inherits them, and a test that runs `git init`
in a temporary directory then re-initialises the hook's repository instead. On 10 September 2026
that wrote `core.bare = true`, then `core.worktree` naming one worktree, into the config every
checkout of this repository shares: each of them then read and wrote that one worktree's files.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

API = pathlib.Path(__file__).resolve().parents[1]


def test_a_hooks_repository_is_untouched_by_a_test_that_runs_git(tmp_path: pathlib.Path) -> None:
    # Cleared here as well as in conftest: were conftest's protection gone and this run under a
    # hook, the setup `git init` below would itself re-initialise the hook's repository — the
    # damage this test exists to catch. No test database either: this run is about git alone.
    local = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
    ).stdout.split()
    clean = {
        name: value
        for name, value in os.environ.items()
        if name not in local and name != "ONELEDGER_TEST_DSN"
    }
    hooks_repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(hooks_repository)], check=True, env=clean)
    work_tree = tmp_path / "work-tree"
    work_tree.mkdir()
    config = hooks_repository / ".git" / "config"
    before = config.read_text()

    # What a hook in a linked worktree exports.
    env = clean | {"GIT_DIR": str(hooks_repository / ".git"), "GIT_WORK_TREE": str(work_tree)}
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_check_docs.py::test_a_nested_checkout_is_not_this_tree",
        ],
        cwd=API,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert config.read_text() == before
    assert run.returncode == 0, run.stdout
