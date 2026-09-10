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


@pytest.mark.parametrize(
    "name,body",
    [
        # Postgres double quotes delimit an *identifier*, not a string — and "not a column" is
        # the first thing the rule names, so the syntax that declares one most explicitly must
        # not be the syntax the check cannot read.
        ("t.sql", 'CREATE TABLE t ("amount_inr" INT);'),
        ("t.sql", 'ALTER TABLE t RENAME "balance_aed" TO x;'),
        # An interpolation holds an expression. A template literal is where a screen builds its
        # labels, which is exactly where a currency-named local turns up.
        ("screen.tsx", "const x = `${amountInr}`;"),
        ("screen.tsx", "const x = `total ${total_aed} shown`;"),
        ("thing.py", 'x = f"{amount_inr}"'),
    ],
)
def test_a_name_the_syntax_hides_is_still_an_identifier(
    tmp_path: pathlib.Path, name: str, body: str
) -> None:
    assert scan(tmp_path, name, body), f"{body!r} evades the check"


def test_a_single_quoted_sql_string_is_still_data(tmp_path: pathlib.Path) -> None:
    """The other half of the SQL rule: `'AED'` is a value, and seeds are full of them."""
    assert scan(tmp_path, "seed.sql", "INSERT INTO currency (code) VALUES ('AED');") == []


def test_a_format_placeholder_is_not_an_interpolation(tmp_path: pathlib.Path) -> None:
    """Only an f-string interpolates. `"{amount_inr}"` elsewhere is a `.format` template — text
    that names a placeholder, not code that names a variable."""
    assert scan(tmp_path, "thing.py", 'TEMPLATE = "{amount_inr}"') == []


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
        # A rule about money that a formatter can switch off is not a rule. Prettier inserts
        # those spaces today; nothing makes that a guarantee.
        "const t = row.amount+other.amount;",
        "const t = row.amount*2;",
        "const t = 2*row.amount;",
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


# ---------------------------------------------------------------------------------------
# Rule 3 — a report reads v_transaction_reportable, never the base table (I3)
#
# The view excludes transfer legs, excluded rows and balance-sheet movements; a report that
# reads `transaction` directly reintroduces all three and nothing downstream can tell. A handful
# of files read the base table on purpose — the ledger listing, import, classification, transfer
# matching, account balances — and each is named in the script with its reason.
# ---------------------------------------------------------------------------------------

REPORT = "api/oneledger/routes/dashboard.py"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT sum(amount_base) FROM transaction WHERE household_id = %s",
        "SELECT a.name FROM account a JOIN transaction t ON t.account_id = a.id",
        "UPDATE transaction SET notes = %s WHERE id = %s",
        "INSERT INTO transaction (id) VALUES (%s)",
    ],
)
def test_a_new_reader_of_the_base_table_is_refused(tmp_path: pathlib.Path, statement: str) -> None:
    found = scan(tmp_path, REPORT, f'QUERY = """{statement}"""\n')
    assert found, f"{statement!r} reads the base table from a file not named as a reader"
    assert "v_transaction_reportable" in found[0]
    assert "I3" in found[0]


@pytest.mark.parametrize(
    "reader",
    [
        "api/oneledger/routes/transactions.py",
        "api/oneledger/routes/ingest.py",
        "api/oneledger/routes/rules.py",
        "api/oneledger/routes/transfers.py",
        "api/oneledger/routes/accounts.py",
        "api/oneledger/ingest/pipeline.py",
        "db/migrations/V99__something.sql",
    ],
)
def test_each_named_reader_may_read_the_base_table(tmp_path: pathlib.Path, reader: str) -> None:
    body = (
        "SELECT * FROM transaction t;\n"
        if reader.endswith(".sql")
        else 'QUERY = "SELECT * FROM transaction t"\n'
    )
    assert scan(tmp_path, reader, body) == []


def test_a_report_reads_the_reportable_view(tmp_path: pathlib.Path) -> None:
    body = (
        'QUERY = "SELECT sum(amount_base) FROM v_transaction_reportable WHERE household_id = %s"\n'
    )
    assert scan(tmp_path, REPORT, body) == []


def test_prose_about_transactions_is_not_a_query(tmp_path: pathlib.Path) -> None:
    """The keywords are SQL's, in the upper case this repository writes them in. English that
    says a figure is read from transaction rows is not a query and must not be refused."""
    body = (
        "# the balance is read from transaction rows, and moves from transaction to transaction\n"
        'def f() -> None:\n    """Money moved from transaction rows into the view."""\n'
    )
    assert scan(tmp_path, REPORT, body) == []


def test_sql_quoted_in_a_comment_is_not_a_query(tmp_path: pathlib.Path) -> None:
    """A comment may quote the very query it warns against, in the upper case SQL is written in.
    The rules read SQL where it runs — inside string literals — and a comment is not where it
    runs. Without this, explaining the rule in the code that obeys it would break the build."""
    body = (
        "# never write: SELECT sum(amount) FROM transaction — read the reportable view instead\n"
        "# and never: SUM(amount * fx_rate_to_base), which re-translates history\n"
        'QUERY = "SELECT sum(amount_base) FROM v_transaction_reportable"\n'
    )
    assert scan(tmp_path, REPORT, body) == []


def test_a_test_may_set_up_the_base_table(tmp_path: pathlib.Path) -> None:
    """Fixtures build ledgers; they report nothing. The rule guards product code."""
    body = 'cur.execute("INSERT INTO transaction (id) VALUES (%s)", (1,))\n'
    assert scan(tmp_path, "api/tests/test_something_live.py", body) == []
    assert (
        scan(tmp_path, "db/tests/0099_something.sql", "INSERT INTO transaction (id) VALUES (1);")
        == []
    )


# ---------------------------------------------------------------------------------------
# Rule 4 — a flow is never multiplied by a rate outside the freeze (I5)
#
# A transaction's base and secondary figures are frozen once, at the rate of its own day, and
# never recomputed. A read that multiplies a flow by a rate re-translates history at whatever
# rate it found — last March's spending moving with today's exchange rate.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression",
    [
        "SELECT SUM(amount * fx_rate_to_base) FROM v_transaction_reportable",
        "SELECT SUM(t.amount * t.fx_rate_to_secondary) FROM v_transaction_reportable t",
        "total = row.amount_base * market_rate",
        "x = rate * amount",
    ],
)
def test_translating_a_flow_at_read_time_is_refused(
    tmp_path: pathlib.Path, expression: str
) -> None:
    found = scan(tmp_path, REPORT, f'QUERY = """{expression}"""\n')
    assert found, f"{expression!r} translates a flow at read time"
    assert any("I5" in line for line in found)


def test_the_ledger_s_own_derivation_check_may_multiply(tmp_path: pathlib.Path) -> None:
    """V10's `frozen_amounts_derive` defines the frozen figures as amount times rate."""
    body = "CHECK (abs(amount_base - round(amount * fx_rate_to_base, 4)) <= 0.0001);"
    assert scan(tmp_path, "db/migrations/V99__something.sql", body) == []


@pytest.mark.parametrize("freeze", ["api/oneledger/money.py", "api/oneledger/ingest/pipeline.py"])
def test_the_freeze_itself_may_multiply(tmp_path: pathlib.Path, freeze: str) -> None:
    assert scan(tmp_path, freeze, "frozen = (amount * rate).quantize(CENT)\n") == []


def test_a_balance_translated_at_today_s_rate_is_meant_to_pass(tmp_path: pathlib.Path) -> None:
    """ADR-0002 and `multi-currency-and-fx.html` §6: a balance is a position, translated as of
    now on purpose; only a flow is frozen. A rule that refused this would have the next reader
    delete working code to make a check pass."""
    body = 'QUERY = "SELECT sum(current_balance * rate) FROM account a JOIN fx_rate r ON true"\n'
    assert scan(tmp_path, REPORT, body) == []


def test_a_nested_checkout_is_not_this_tree(tmp_path: pathlib.Path) -> None:
    """The check reads the files git tracks, not every file under the root.

    Parallel sessions work in worktrees under `.claude/worktrees/`, each a whole second copy of
    this repository. Walking into one made this check pass or fail on someone else's uncommitted
    work — which is how it was found, on 10 September 2026, when it scanned 967 files in a tree
    that tracks 163.
    """
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "mine.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "mine.py"], cwd=tmp_path, check=True)
    nested = tmp_path / ".claude" / "worktrees" / "someone-else"
    nested.mkdir(parents=True)
    (nested / "theirs.py").write_text("amount_inr = 0\n")

    scanned = {path.name for path in check.sources(tmp_path)}

    assert scanned == {"mine.py"}
    assert check.scan_tree(tmp_path) == []
