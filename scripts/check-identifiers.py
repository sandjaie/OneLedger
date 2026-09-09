#!/usr/bin/env python3
"""ADR-0012's identifier rule and the money rule, enforced against the tree.

`CLAUDE.md` states both as things a static check fails the build on. Until now no such check
existed, so both were enforced only by whoever happened to read the diff.

**Rule 1 — no currency, institution or country name is ever an identifier.** Not a column, a
type, an enum member, a function name or a constant. `amount_base` / `amount_secondary`, never
`amount_inr`; `formatMoney` reads a currency row's `symbol`, `decimals` and `number_format`,
never a `"INR" | "AED"` union. The harm is a product that silently assumes one country's banks
and needs a code change for the fifth. A name inside a *string* is data, not an identifier —
seeds, fixtures, messages and prose all carry currency codes legitimately, and this check reads
only the code around them.

`currency.number_format` holds `'international'` or `'indian'` — the 3,3,3 and the 2,2,3
lakh/crore grouping — and needs no exception here. It is a *string*, carried as a property of a
currency row and read from it, so a currency that groups that way needs no code change, which is
the harm ADR-0012 exists to prevent. The rule's wording ("not a locale constant") is broader than
its purpose; reading only code settles it without an allowance anyone could widen.

**Rule 2 — TypeScript formats money and does no arithmetic on it.** A decimal string that
becomes a JavaScript number has already lost the guarantee `NUMERIC(20,4)` exists to give:
`0.1 + 0.2` is not `0.3`, and a ledger that disagrees with itself by a hundredth is a ledger
nobody trusts. Aggregation happens in Postgres.

Run it the way the hook does:  python3 scripts/check-identifiers.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directories that hold no source of ours.
SKIP_DIRS = {"node_modules", ".venv", ".next", "__pycache__", ".git", "pdf-statements", "dist"}

# Generated from the OpenAPI schema, so a name here is the API's and is caught at its source.
SKIP_PARTS = ("web/types/api",)

SOURCE = {".py", ".ts", ".tsx", ".sql", ".mjs"}

# Currency codes that actually tempt someone in this repository — the ones the documents use as
# examples. Not the whole ISO 4217 list: every three-letter word would match something.
#
# Matched case-insensitively against a whole segment, so `amount_inr` and `amountInr` are caught
# alongside `AMOUNT_INR`. A segment is a word boundary, not a substring, so `audit_log` splits to
# `audit` and never trips `AUD`.
CODES = frozenset(
    code.lower()
    for code in "INR AED USD EUR GBP SAR QAR OMR KWD BHD CHF JPY CNY AUD CAD SGD".split()
)

# Currency, institution and country names, matched case-insensitively against one segment of an
# identifier. `axis` and `citi` are deliberately absent: an axis is a chart's and `citi` is
# inside `city`, and a rule that cries wolf is a rule someone silences.
NAMES = frozenset(
    (
        "rupee rupees paisa dirham dirhams fils dollar dollars euro euros pound pounds "
        "riyal riyals dinar dinars yen yuan franc "
        "hdfc icici sbi enbd mashreq kotak adcb fab "
        "india indian uae emirati emirates dubai sharjah"
    ).split()
)

# Rule 2 applies to the browser. Python holds money in `Decimal` and Postgres in `NUMERIC`;
# neither can be turned into a float by these.
WEB = re.compile(r"(^|/)web/")
TO_NUMBER = re.compile(r"\b(parseFloat|parseInt|Number)\s*\(")

# A property whose name says it holds money. Matched as a property access — `row.amount` — so a
# local named `total` counting rows is not swept in with it.
MONEY_FIELD = re.compile(
    r"\.\s*(amount|amountBase|amountSecondary|balance|openingBalance|closingBalance|"
    r"currentBalance|creditLimit|declaredOpening|declaredClosing|computedClosing|delta|"
    r"originalAmount|runningBalance|rate|total)\b"
)
ARITHMETIC = re.compile(r"\s*[-+*/]\s*")


def segments(name: str) -> list[str]:
    """`amount_inr`, `amountInr` and `AMOUNT_INR` all split into the same words."""
    found: list[str] = []
    for part in re.split(r"[_\-.]", name):
        found.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+", part) or [part])
    return found


def code_only(text: str, suffix: str) -> str:
    """The file with its comments and string literals blanked.

    Both are where a currency code legitimately appears — a seed row, a test fixture, an error
    message, a docstring naming an example. Blanking rather than deleting keeps every offset,
    so a line number still points where it should.
    """

    def blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group())

    if suffix == ".py":
        text = re.sub(r'"""(?:.|\n)*?"""', blank, text)
        text = re.sub(r"'''(?:.|\n)*?'''", blank, text)
        text = re.sub(r"#[^\n]*", blank, text)
    else:
        text = re.sub(r"/\*(?:.|\n)*?\*/", blank, text)
        text = re.sub(r"//[^\n]*", blank, text)
        text = re.sub(r"--[^\n]*", blank, text)
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', blank, text)
    text = re.sub(r"'(?:[^'\\\n]|\\.)*'", blank, text)
    text = re.sub(r"`(?:[^`\\]|\\.)*`", blank, text)
    return text


def scan_file(path: pathlib.Path, label: str) -> list[str]:
    """Every violation in one file, as a line the fixer can act on."""
    try:
        raw = path.read_text(errors="ignore")
    except OSError:  # pragma: no cover - unreadable file
        return []

    found: list[str] = []
    code = code_only(raw, path.suffix)

    def line_of(offset: int) -> int:
        return raw.count("\n", 0, offset) + 1

    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", code):
        for segment in segments(match.group()):
            if segment.lower() in CODES or segment.lower() in NAMES:
                number = line_of(match.start())
                found.append(
                    f"{label}:{number}: identifier `{match.group()}` names a currency, "
                    f"institution or country (ADR-0012). Carry it as data on a currency, "
                    f"institution or account row instead."
                )
                break

    if WEB.search(label.replace("\\", "/")) or label.endswith((".ts", ".tsx")):
        for match in TO_NUMBER.finditer(code):
            number = line_of(match.start())
            found.append(
                f"{label}:{number}: `{match.group(1)}` turns a decimal string into a "
                f"JavaScript number. Money is a string on the wire and stays one — "
                f"TypeScript formats it and never computes it."
            )
        for match in MONEY_FIELD.finditer(code):
            after = code[match.end() : match.end() + 4]
            before = code[max(0, match.start() - 3) : match.start()]
            if ARITHMETIC.fullmatch(after[:2]) or ARITHMETIC.fullmatch(before[-2:]):
                number = line_of(match.start())
                found.append(
                    f"{label}:{number}: arithmetic on `{match.group().strip()}`. "
                    f"Money is aggregated in Postgres, never in the browser."
                )

    return found


def sources(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix in SOURCE
        and not any(part in SKIP_DIRS for part in path.parts)
        and not any(part in path.as_posix() for part in SKIP_PARTS)
    ]


def scan_tree(root: pathlib.Path) -> list[str]:
    found: list[str] = []
    for path in sources(root):
        found.extend(scan_file(path, path.relative_to(root).as_posix()))
    return found


def main() -> int:
    found = scan_tree(ROOT)
    for line in found:
        print(line)
    print(f"\n{len(sources(ROOT))} source files checked", file=sys.stderr)
    if found:
        print(
            f"\n{len(found)} violation(s). CLAUDE.md: do not silence this check — fix the "
            f"identifier or move the arithmetic into Postgres.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
