"""The static check CLAUDE.md says fails the build, tested against its own rules.

A checker nobody has watched fail is a checker nobody has shown to check anything — the same
trap as an untested redaction. Each rule here is exercised on a violation and on the nearest
legitimate construction it must not flag, because a check that cries wolf gets silenced, and
CLAUDE.md says this one may not be.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
# Loaded by path: the script is named for the hook that runs it, as its neighbours are, and a
# hyphen is not an importable module name.
_spec = importlib.util.spec_from_file_location(
    "check_identifiers", ROOT / "scripts" / "check-identifiers.py"
)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def scan(tmp_path: pathlib.Path, name: str, body: str) -> list[str]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    # The label, not the temporary path: rule 2 keys off it, so a fixture written to
    # `web/app/screen.tsx` must be seen as living there.
    found: list[str] = check.scan_file(path, name)
    return found


# ---------------------------------------------------------------------------------------
# Rule 1 — no currency, institution or country name in a program identifier (ADR-0012)
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "amount_inr = 0",
        "def total_aed(): pass",
        "class HdfcParser: pass",
        "amountInr = 0",
        "UAE_DEFAULT = 1",
    ],
)
def test_a_currency_or_institution_in_an_identifier_is_refused(
    tmp_path: pathlib.Path, body: str
) -> None:
    found = scan(tmp_path, "thing.py", body)
    assert found, f"{body!r} names a currency, institution or country and must be refused"
    assert "identifier" in found[0].lower()


@pytest.mark.parametrize(
    "body",
    [
        # The reporting pair, which is what the rule exists to require.
        "amount_base = 0\namount_secondary = 0",
        "base_currency = row.code",
        # A currency code in a *string* is data, not an identifier: seeds, fixtures and
        # messages all carry them legitimately.
        'code = "AED"',
        "# amount_inr would be wrong here",
        '"""A docstring naming INR as an example."""',
    ],
)
def test_a_currency_named_as_data_or_prose_is_not_an_identifier(
    tmp_path: pathlib.Path, body: str
) -> None:
    assert scan(tmp_path, "thing.py", body) == []


def test_the_grouping_convention_needs_no_exception(tmp_path: pathlib.Path) -> None:
    """`number_format` holds `'indian'` — a named numeral-grouping convention.

    It is the one country adjective in the tree, and it needs no allowance: it is a *string*,
    carried as a property of a currency row and read from it, so a currency that groups that way
    needs no code change. Reading only code settles it, and the same word as an identifier is
    still refused — which is what an allowance broad enough to cover the string would have lost.
    """
    assert scan(tmp_path, "format.ts", 'if (currency.numberFormat === "indian") {}') == []
    assert scan(tmp_path, "check.sql", "number_format IN ('international','indian')") == []
    assert scan(tmp_path, "format.ts", "const indianTotal = 0;")


# ---------------------------------------------------------------------------------------
# Rule 2 — TypeScript formats money and never computes it
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["const n = parseFloat(row.amount);", "const n = Number(row.amount);", "parseInt(x, 10);"],
)
def test_turning_a_string_into_a_javascript_number_is_refused(
    tmp_path: pathlib.Path, body: str
) -> None:
    found = scan(tmp_path, "web/app/screen.tsx", body)
    assert found, f"{body!r} makes a decimal string a float and must be refused"
    assert "number" in found[0].lower()


def test_formatting_money_from_its_string_is_allowed(tmp_path: pathlib.Path) -> None:
    assert scan(tmp_path, "web/app/screen.tsx", "formatMoney(row.amount, currency);") == []


@pytest.mark.parametrize(
    "body",
    [
        "const total = row.amount + other.amount;",
        "const half = batch.declaredClosing / 2;",
        "const d = a.amountBase - b.amountBase;",
    ],
)
def test_arithmetic_on_a_money_field_is_refused(tmp_path: pathlib.Path, body: str) -> None:
    found = scan(tmp_path, "web/app/screen.tsx", body)
    assert found, f"{body!r} computes money in TypeScript and must be refused"
    assert "arithmetic" in found[0].lower()


@pytest.mark.parametrize(
    "body",
    [
        # Counts are not money. Postgres aggregates money; TypeScript may still count rows.
        "const n = batch.rowsExtracted + 1;",
        "const label = `${group.count} rows`;",
        # String concatenation around a formatted amount is not arithmetic on it.
        'const text = "closing " + formatMoney(row.amount, currency);',
    ],
)
def test_counting_and_labelling_are_not_money_arithmetic(tmp_path: pathlib.Path, body: str) -> None:
    assert scan(tmp_path, "web/app/screen.tsx", body) == []


# ---------------------------------------------------------------------------------------
# The tree it actually guards
# ---------------------------------------------------------------------------------------


def test_the_repository_passes_its_own_check() -> None:
    """Red here is a real violation in the tree, not a broken test.

    CLAUDE.md says this check may not be silenced, so the fix is the identifier or the
    arithmetic — never an exemption added to make this pass.
    """
    assert check.scan_tree(ROOT) == []
