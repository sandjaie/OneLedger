#!/usr/bin/env python3
"""Everything that has to be true of docs/ before it is published.

Run it locally the same way CI does:  python3 scripts/check-docs.py
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOID = {"area", "base", "br", "col", "embed", "hr", "img",
        "input", "link", "meta", "param", "source", "track", "wbr"}
LINK_PATTERNS = [
    re.compile(r'href="(?!https?:|mailto:|#)([^"]+)"'),   # html
    re.compile(r'\]\((?!https?:|mailto:|#)([^)]+)\)'),    # markdown
]


class TagBalance(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, tuple[int, int]]] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"stray </{tag}> at {self.getpos()}")
            return
        opened, where = self.stack.pop()
        if opened != tag:
            self.errors.append(f"</{tag}> at {self.getpos()} closes <{opened}> opened at {where}")


# Vendored trees carry their own .md and .html. Checking them means auditing other people's
# documentation, and every link they get wrong becomes our build failure.
SKIP = {".git", ".venv", "node_modules", ".next", "__pycache__", ".ruff_cache",
        ".mypy_cache", ".pytest_cache", "dist", "build"}



def _tracked(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    """The files git tracks, not every file under the root.

    A nested checkout — `.claude/worktrees/*`, where a parallel session works — is a whole second
    copy of this repository. Walking into it made this check pass or fail on someone else's
    uncommitted work: on 10 September 2026 it counted 525 documents in a tree that tracks 140.
    Outside a git checkout it falls back to walking the directory.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=False
    )
    if listed.returncode != 0:
        return sorted(root.rglob("*"))
    return [root / name for name in sorted(filter(None, listed.stdout.split("\0")))
            if (root / name).is_file()]

def documents() -> list[pathlib.Path]:
    return sorted(p for p in _tracked()
                  if p.suffix in (".html", ".md") and not SKIP.intersection(p.parts))


FENCE = re.compile(r"(?ms)^```.*?^```")


def prose_only(text: str, suffix: str) -> str:
    """Markdown link syntax means nothing inside a fenced code block.

    `def carry_forward[T](published: dict[date, T], ...)` is a Python generic, and reads as
    `[T](published: dict...)` to a link matcher — a broken link that is not a link at all.
    Blank the fences rather than deleting them, so reported line numbers stay true.
    """
    if suffix != ".md":
        return text
    return FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def check_links() -> list[str]:
    out = []
    for path in documents():
        text = prose_only(path.read_text(), path.suffix)
        for pattern in LINK_PATTERNS:
            for match in pattern.finditer(text):
                target = match.group(1).split("#")[0]
                if target and not (path.parent / target).exists():
                    out.append(f"{path.relative_to(ROOT)} → {match.group(1)}")
    return out


def check_html() -> list[str]:
    out = []
    for path in sorted(p for p in _tracked() if p.suffix == ".html"):
        if SKIP.intersection(path.parts):
            continue
        checker = TagBalance()
        checker.feed(path.read_text())
        problems = checker.errors + [f"unclosed <{t}> at {w}" for t, w in checker.stack]
        out += [f"{path.relative_to(ROOT)}: {p}" for p in problems]
    return out


def check_no_real_data() -> list[str]:
    """A statement must never be tracked by git. Invariant, not a preference.

    One implementation, in scripts/check-no-real-data.sh, so the pre-commit hook and CI
    cannot drift apart on what counts as real data.
    """
    result = subprocess.run([str(ROOT / "scripts" / "check-no-real-data.sh")],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode == 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.startswith("  ")]


ADR_LINK = re.compile(r'<a href="[^"]*?(\d{4})-[a-z0-9-]+\.html(?:#[^"]*)?">(?:ADR-)?(\d{4})\b[^<]*</a>')
ADR_MD_LINK = re.compile(r'\[(?:ADR-)?(\d{4})\]\([^)]*?(\d{4})-[a-z0-9-]+\.html\)')


def check_adr_numbers() -> list[str]:
    """A link labelled ADR-0013 must point at 0013. Renumbering is where this breaks."""
    out = []
    for path in documents():
        text = path.read_text()
        for target, label in ADR_LINK.findall(text):
            if target != label:
                out.append(f"{path.relative_to(ROOT)}: labelled {label}, points at {target}")
        for label, target in ADR_MD_LINK.findall(text):
            if target != label:
                out.append(f"{path.relative_to(ROOT)}: labelled {label}, points at {target}")
    return out


CHECKS = [
    ("broken links", check_links),
    ("ADR link labels disagreeing with their target", check_adr_numbers),
    ("malformed html", check_html),
    ("real financial data tracked by git", check_no_real_data),
]

if __name__ == "__main__":
    failed = 0
    for name, check in CHECKS:
        problems = check()
        print(f"{'FAIL' if problems else 'ok  '}  {name}"
              f"{f' ({len(problems)})' if problems else ''}")
        for problem in problems:
            print(f"        {problem}")
        failed += len(problems)
    print(f"\n{len(documents())} documents checked")
    sys.exit(1 if failed else 0)
