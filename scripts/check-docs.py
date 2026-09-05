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


def documents() -> list[pathlib.Path]:
    return sorted(p for p in ROOT.rglob("*")
                  if p.suffix in (".html", ".md") and ".git" not in p.parts)


def check_links() -> list[str]:
    out = []
    for path in documents():
        text = path.read_text()
        for pattern in LINK_PATTERNS:
            for match in pattern.finditer(text):
                target = match.group(1).split("#")[0]
                if target and not (path.parent / target).exists():
                    out.append(f"{path.relative_to(ROOT)} → {match.group(1)}")
    return out


def check_html() -> list[str]:
    out = []
    for path in sorted(ROOT.rglob("*.html")):
        if ".git" in path.parts:
            continue
        checker = TagBalance()
        checker.feed(path.read_text())
        problems = checker.errors + [f"unclosed <{t}> at {w}" for t, w in checker.stack]
        out += [f"{path.relative_to(ROOT)}: {p}" for p in problems]
    return out


def check_no_real_data() -> list[str]:
    """A statement must never be tracked by git. Invariant, not a preference."""
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    return [f for f in tracked
            if f.lower().endswith(".pdf") or f.startswith("pdf-statements/")]


CHECKS = [
    ("broken links", check_links),
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
