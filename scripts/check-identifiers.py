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

**Rule 3 — a report reads `v_transaction_reportable`, never the base table (I3).** The view
excludes transfer legs, excluded rows and balance-sheet movements; a report reading `transaction`
directly puts all three back into spending, and nothing downstream can tell. A few files read the
base table on purpose — each named in `BASE_TABLE_READERS` with the reason — and any other file
that reads it fails until someone adds it deliberately, saying why. A balance is one of those
reasons: a balance must include the transfers a report must exclude.

**Rule 4 — a flow is never multiplied by a rate outside the freeze (I5).** A transaction's base
and secondary figures are frozen once, at the rate of its own day. A read that multiplies a flow
by a rate re-translates history at whatever rate it found. `money.translate()` is the freeze and
V10's `frozen_amounts_derive` is its definition, so both may. **A balance times today's rate is
allowed everywhere and meant to be** (ADR-0002, `multi-currency-and-fx.html` §6): a balance is a
position, translated as of now; only a flow is frozen.

Both rules read SQL where it lives — inside string literals — with comments removed, and match
its keywords in the upper case this repository writes SQL in, so prose that says a figure is
read "from transaction rows" is not a query. Lower-case SQL would slip past; there is none. Both
guard product code: tests build ledgers and re-derive frozen figures independently, and report
nothing.

Run it the way the hook does:  python3 scripts/check-identifiers.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
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

# Rule 2 applies to the browser's own source. Python holds money in `Decimal` and Postgres in
# `NUMERIC`; neither can be turned into a float by these. `.mjs` is deliberately absent — the
# build scripts under `web/scripts/` handle no money and legitimately count things.
BROWSER = (".ts", ".tsx")
TO_NUMBER = re.compile(r"\b(parseFloat|parseInt|Number)\s*\(")

# A property whose name says it holds money. Matched as a property access — `row.amount` — so a
# local named `total` counting rows is not swept in with it.
MONEY_FIELD = re.compile(
    r"\.\s*(amount|amountBase|amountSecondary|balance|openingBalance|closingBalance|"
    r"currentBalance|creditLimit|declaredOpening|declaredClosing|computedClosing|delta|"
    r"originalAmount|runningBalance|rate|total)\b"
)
ARITHMETIC = frozenset("-+*/")


_OBJECT = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$.")


def _after(text: str, index: int) -> str:
    """The nearest non-space character at or after `index`."""
    while index < len(text) and text[index] in " \t":
        index += 1
    return text[index] if index < len(text) else ""


def _before_the_object(text: str, index: int) -> str:
    """The nearest non-space character before the member expression ending at `index`.

    `2*row.amount` puts the operator before `row`, not before `.amount`, so the walk steps back
    over the object it is a member of first. Without that, only arithmetic written to the right
    of a money field is seen — half the rule.
    """
    while index >= 0 and text[index] in _OBJECT:
        index -= 1
    while index >= 0 and text[index] in " \t":
        index -= 1
    return text[index] if index >= 0 else ""


def segments(name: str) -> list[str]:
    """`amount_inr`, `amountInr` and `AMOUNT_INR` all split into the same words."""
    found: list[str] = []
    for part in re.split(r"[_\-.]", name):
        found.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+", part) or [part])
    return found


def _blank(text: str) -> str:
    """Every character replaced by a space, newlines kept — so offsets and line numbers hold."""
    return re.sub(r"[^\n]", " ", text)


def _blank_literal(match: re.Match[str]) -> str:
    """Blank a quoted region, but keep what an interpolation holds.

    `${…}` in a template literal and `{…}` in an f-string are expressions, not text — a screen
    building a label is exactly where a currency-named local turns up. The braces themselves go
    with the literal so that what is left parses as the expression it is. A plain Python string
    is not scanned for `{…}`: there it is a `.format` placeholder, which names a field rather
    than a variable.
    """
    body = match.group()
    interpolated = _INTERPOLATION if body[:1] == "`" or _FSTRING.match(body) else None
    if interpolated is None:
        return _blank(body)

    out = []
    last = 0
    for inner in interpolated.finditer(body):
        out.append(_blank(body[last : inner.start(1)]))
        out.append(inner.group(1))
        last = inner.end(1)
    out.append(_blank(body[last:]))
    return "".join(out)


# The expression inside an interpolation, without its braces.
_INTERPOLATION = re.compile(r"\$?\{([^{}]*)\}")
_FSTRING = re.compile(r"[a-zA-Z]*[fF][a-zA-Z]*[\"\']")


def code_only(text: str, suffix: str) -> str:
    """The file with its comments and string literals blanked.

    Both are where a currency code legitimately appears — a seed row, a test fixture, an error
    message, a docstring naming an example.

    **SQL is lexed on its own terms.** Postgres double quotes delimit an *identifier*, not a
    string, and "not a column" is the first thing ADR-0012's rule names — so blanking a
    double-quoted region there would hide the syntax that declares a column most explicitly.
    Single quotes are SQL's string literal and are blanked as everywhere else.
    """
    if suffix == ".py":
        text = re.sub(r'(?:[a-zA-Z]*)"""(?:.|\n)*?"""', _blank_literal, text)
        text = re.sub(r"(?:[a-zA-Z]*)'''(?:.|\n)*?'''", _blank_literal, text)
        text = re.sub(r"#[^\n]*", lambda m: _blank(m.group()), text)
    else:
        text = re.sub(r"/\*(?:.|\n)*?\*/", lambda m: _blank(m.group()), text)
        text = re.sub(r"//[^\n]*", lambda m: _blank(m.group()), text)
        text = re.sub(r"--[^\n]*", lambda m: _blank(m.group()), text)

    if suffix != ".sql":
        text = re.sub(r'(?:[a-zA-Z]*)"(?:[^"\\\n]|\\.)*"', _blank_literal, text)
    text = re.sub(r"(?:[a-zA-Z]*)'(?:[^'\\\n]|\\.)*'", _blank_literal, text)
    text = re.sub(r"`(?:[^`\\]|\\.)*`", _blank_literal, text)
    return text


# Rule 3's named readers. A literal, not a pragma a caller can write for itself: `CLAUDE.md` says
# this check may not be silenced, and an exemption anyone can add is a silenced check.
BASE_TABLE_READERS = {
    # The ledger listing: transfer legs and duplicates stay visible and labelled there.
    "api/oneledger/routes/transactions.py",
    # Import writes the rows and checks them against what is already committed.
    "api/oneledger/routes/ingest.py",
    "api/oneledger/ingest/",
    # Classification preview and backfill must see rows that already carry an answer.
    "api/oneledger/routes/rules.py",
    # Classification repair compares and updates the exact stored state, including protected rows.
    "api/oneledger/routes/classification_decisions.py",
    # Description repair compares and updates the exact committed import rows, never a report.
    "api/oneledger/routes/description_reparse.py",
    # Classification review groups unanswered ledger rows and never reports spending or income.
    "api/oneledger/routes/classification_review.py",
    # Transfer matching looks for unlinked legs, which the reportable view drops by design.
    "api/oneledger/routes/transfers.py",
    # Funding decisions inspect and relabel exact cash leaves; they are not flow reports.
    "api/oneledger/investments/funding.py",
    # Holding detail reads one confirmed cash leaf's own currency; those leaves are excluded
    # from the reportable view by design (I3), so reading it there would find nothing.
    "api/oneledger/investments/portfolio.py",
    # A balance and the guards on an account's anchor must include every movement, transfers too.
    "api/oneledger/routes/accounts.py",
    # A resolution's linked posting is read by its own id, including one later split: the history
    # still shows its original date and amount with `eligible: false`, not a missing row.
    "api/oneledger/routes/commitment_resolutions.py",
    # Data Quality counts the rows a backup does not hold, and a backup holds every row.
    "api/oneledger/routes/doctor.py",
    # The backup manifest sums every row a restored dump must reproduce, transfer legs and
    # excluded rows included — a report's own exclusions would make a faithful dump look wrong.
    "scripts/backup-manifest.sql",
    "db/migrations/",
}

# Rule 4's freeze sites: the definition, and the one function that applies it.
# `attribution.py` multiplies a flow's or holding funding event's frozen native amount by the
# closing rate on purpose — the spec's FX bridge, moving FX on intra-period flows out of their
# own line and into FX translation (Phase 2.2 spec "Attribution for one month").
RATE_MULTIPLIERS = {
    "db/migrations/",
    "api/oneledger/money.py",
    "api/oneledger/ingest/",
    "api/oneledger/attribution.py",
}

# Tests build ledgers and re-derive frozen figures on purpose; they report nothing.
TEST_CODE = ("api/tests/", "db/tests/", "/__tests__/")

BASE_TABLE = re.compile(r"\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO)\s+transaction\b")
PRODUCT = re.compile(r"([A-Za-z_][\w.]*)\s*\*\s*([A-Za-z_][\w.]*)")
FLOWS = frozenset({"amount", "amount_base", "amount_secondary", "amountBase", "amountSecondary"})


def _is_rate(name: str) -> bool:
    last = name.rsplit(".", 1)[-1]
    return last.lower().endswith("rate") or last.startswith(("rate_to", "fx_rate", "fxRate"))


def _under(label: str, places: set[str]) -> bool:
    return any(
        label.startswith(place) if place.endswith("/") else label == place for place in places
    )


def _sql_visible(text: str, suffix: str) -> str:
    """The file with comments blanked and strings kept, because SQL lives in strings."""
    if suffix == ".py":
        text = re.sub(r"#[^\n]*", lambda m: _blank(m.group()), text)
    else:
        text = re.sub(r"/\*(?:.|\n)*?\*/", lambda m: _blank(m.group()), text)
        text = re.sub(r"//[^\n]*", lambda m: _blank(m.group()), text)
    return re.sub(r"--[^\n]*", lambda m: _blank(m.group()), text)


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

    if label.endswith(BROWSER):
        for match in TO_NUMBER.finditer(code):
            number = line_of(match.start())
            found.append(
                f"{label}:{number}: `{match.group(1)}` turns a decimal string into a "
                f"JavaScript number. Money is a string on the wire and stays one — "
                f"TypeScript formats it and never computes it."
            )
        for match in MONEY_FIELD.finditer(code):
            # The nearest non-space character on each side, not a fixed window: a rule about
            # money that `row.amount+x` slips past is a rule the formatter is enforcing.
            if _after(code, match.end()) in ARITHMETIC or (
                _before_the_object(code, match.start() - 1) in ARITHMETIC
            ):
                number = line_of(match.start())
                found.append(
                    f"{label}:{number}: arithmetic on `{match.group().strip()}`. "
                    f"Money is aggregated in Postgres, never in the browser."
                )

    if not any(part in label for part in TEST_CODE):
        visible = _sql_visible(raw, path.suffix)
        if not _under(label, BASE_TABLE_READERS):
            for match in BASE_TABLE.finditer(visible):
                found.append(
                    f"{label}:{line_of(match.start())}: `{match.group()}` reads the base table. "
                    f"A report reads v_transaction_reportable, which excludes transfer legs, "
                    f"excluded rows and balance-sheet movements (I3). If this is not a report "
                    f"— a balance, a matcher, an import — add the file to BASE_TABLE_READERS "
                    f"with the reason."
                )
        if not _under(label, RATE_MULTIPLIERS):
            for match in PRODUCT.finditer(visible):
                left, right = match.group(1), match.group(2)
                flow = left.rsplit(".", 1)[-1] in FLOWS or right.rsplit(".", 1)[-1] in FLOWS
                if flow and (_is_rate(left) or _is_rate(right)):
                    found.append(
                        f"{label}:{line_of(match.start())}: `{match.group()}` multiplies a flow "
                        f"by a rate. A transaction's base and secondary figures are frozen at "
                        f"its own day's rate and read from amount_base / amount_secondary (I5); "
                        f"money.translate() is the freeze. A balance times today's rate is "
                        f"allowed — only a flow is frozen."
                    )

    return found


def sources(root: pathlib.Path) -> list[pathlib.Path]:
    """The files git tracks, not every file under the root.

    A nested checkout — `.claude/worktrees/*`, where a parallel session works — is a whole second
    copy of this repository, and walking into it made this check pass or fail on someone else's
    uncommitted work. The pre-commit hook sees a new file once it is staged, which is when git
    tracks it. Outside a git checkout it falls back to walking the directory.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=False
    )
    candidates = (
        [root / name for name in sorted(filter(None, listed.stdout.split("\0")))]
        if listed.returncode == 0
        else sorted(root.rglob("*"))
    )
    return [
        path
        for path in candidates
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
