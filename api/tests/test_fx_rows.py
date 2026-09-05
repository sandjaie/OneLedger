# api/tests/test_fx_rows.py
from datetime import date
from decimal import Decimal

from oneledger.fx import Peg
from oneledger.routes.fx import to_rows

PEGS = {"AED": Peg(anchor="USD", units_per_anchor=Decimal("3.6725"))}


def test_rows_carry_provenance() -> None:
    """Every stored rate declares whether it was published or carried, and from where."""
    published = {
        date(2024, 1, 5): {"INR": Decimal("91.28"), "USD": Decimal("1.0956")},
        date(2024, 1, 8): {"INR": Decimal("91.50"), "USD": Decimal("1.0940")},
    }
    rows = to_rows(
        published, date(2024, 1, 5), date(2024, 1, 8), quote="INR", bases=["USD", "AED"], pegs=PEGS
    )

    assert len(rows) == 8  # 4 days x 2 currency pairs
    usd = [r for r in rows if r["base_currency"] == "USD"]
    assert [r["is_carried_forward"] for r in usd] == [False, True, True, False]
    assert usd[1]["carried_from"] == date(2024, 1, 5)
    # the fx_rate CHECK requires a carried row to name its origin
    assert all(not r["is_carried_forward"] or r["carried_from"] for r in rows)
    # a pegged currency is always derived, never fetched
    assert {r["source"] for r in rows if r["base_currency"] == "AED"} == {"derived_peg"}


def test_the_reporting_currency_is_never_its_own_base() -> None:
    """Adding peg anchors can put the quote currency back into the bases.

    Reporting in USD with a secondary of AED pulls USD in as AED's anchor, and a USD/USD row
    both violates the no_self_pair constraint and means nothing.
    """
    from oneledger.routes.fx import reporting_config

    class _Cursor:
        def __init__(self) -> None:
            self._answers = [("USD", "AED"), [("AED", "USD", Decimal("3.6725"))], [("AED",)]]

        def execute(self, *_: object, **__: object) -> None: ...
        def fetchone(self) -> object:
            return self._answers[0]

        def fetchall(self) -> object:
            return self._answers.pop(1)

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_: object) -> None: ...

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

    quote, bases, _ = reporting_config(_Conn())  # type: ignore[arg-type]
    assert quote == "USD"
    assert "USD" not in bases, "the reporting currency must never appear as a base"
