"""`scripts/check-docs.py` reads the documents git tracks, not every file under the root.

Parallel sessions work in worktrees under `.claude/worktrees/`, each a whole second copy of this
repository. Walking into one made the docs check pass or fail on another session's half-written
page — on 10 September 2026 it counted 525 documents in a tree that tracks far fewer.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
# Loaded by path: the script's name has a hyphen, as its neighbours' do.
_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check-docs.py")
assert _spec and _spec.loader
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)


def test_a_nested_checkout_is_not_this_tree(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "mine.md").write_text("# Mine\n")
    subprocess.run(["git", "add", "mine.md"], cwd=tmp_path, check=True)
    nested = tmp_path / ".claude" / "worktrees" / "someone-else"
    nested.mkdir(parents=True)
    (nested / "theirs.md").write_text("[broken](nowhere.md)\n")

    seen = {path.name for path in check_docs._tracked(tmp_path)}

    assert seen == {"mine.md"}


def test_delivery_terminology_reports_retired_current_identifiers(
    tmp_path: pathlib.Path,
) -> None:
    canonical = tmp_path / "roadmap.html"
    canonical.write_text("<p>M7 then Phase 2A then V2. The next slice follows.</p>\n")

    problems = check_docs.check_delivery_terminology([canonical])

    assert len(problems) == 4
    assert any("M7" in problem for problem in problems)
    assert any("Phase 2A" in problem for problem in problems)
    assert any("V2" in problem for problem in problems)
    assert any("slice" in problem for problem in problems)


def test_delivery_terminology_allows_crosswalk_and_excludes_history(
    tmp_path: pathlib.Path,
) -> None:
    master = tmp_path / "master-implementation-plan.html"
    master.write_text(
        "<p>Phase 1.6 follows Phase 1.5.</p>\n"
        "<!-- historical-numbering-crosswalk:start -->\n"
        "<p>M7 was the former investment milestone in V2-era documents.</p>\n"
        "<!-- historical-numbering-crosswalk:end -->\n"
    )
    historical = tmp_path / "2026-09-09-phase-delivery-plan.md"
    historical.write_text("Phase 2B and slice 1.8 are historical.\n")

    problems = check_docs.check_delivery_terminology([master])

    assert problems == []


def test_delivery_terminology_ignores_historical_link_targets(
    tmp_path: pathlib.Path,
) -> None:
    canonical = tmp_path / "progress.md"
    canonical.write_text(
        "[Phase 1.1 evidence](reports/2026-09-09-slice-1.1-handoff.md)\n"
        '<a href="reports/2026-09-08-m2-evidence.html">import evidence</a>\n'
    )

    problems = check_docs.check_delivery_terminology([canonical])

    assert problems == []
