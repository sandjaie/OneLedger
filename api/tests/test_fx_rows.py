# api/tests/test_fx_rows.py
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from oneledger.auth import OwnerSession, require_owner
from oneledger.fx import Peg
from oneledger.fxrange import reporting_config, to_rows
from oneledger.main import app

PEGS = {"AED": Peg(anchor="USD", units_per_anchor=Decimal("3.6725"))}
USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")


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


class _Cursor:
    """Answers `reporting_config`'s three queries in order. No database, no fixtures."""

    def __init__(
        self, household: tuple[str, str], pegs: list[object], accounts: list[object]
    ) -> None:
        self._household = household
        self._fetchall = [pegs, accounts]

    def execute(self, *_: object, **__: object) -> None: ...
    def fetchone(self) -> object:
        return self._household

    def fetchall(self) -> object:
        return self._fetchall.pop(0)

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None: ...


def _conn(household: tuple[str, str], pegs: list[object], accounts: list[object]) -> object:
    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor(household, pegs, accounts)

    return _Conn()


def test_the_reporting_currency_is_never_its_own_base() -> None:
    """Adding peg anchors can put the quote currency back into the bases.

    Reporting in USD with a secondary of AED pulls USD in as AED's anchor, and a USD/USD row
    both violates the no_self_pair constraint and means nothing.
    """
    quote, bases, _ = reporting_config(
        _conn(("USD", "AED"), [("AED", "USD", Decimal("3.6725"))], [("AED",)])  # type: ignore[arg-type]
    )
    assert quote == "USD"
    assert "USD" not in bases, "the reporting currency must never appear as a base"


def test_every_currency_in_a_peg_chain_is_stored_not_only_its_ends() -> None:
    """A->B->USD must store B as well, or the B->USD peg identity has nothing to check.

    Expanding one hop stores A and B and omits USD; the identity check then joins B->INR rows
    against USD->INR rows that were never written, matches nothing, and reports a clean run.
    """
    _, bases, _ = reporting_config(
        _conn(
            ("INR", "XAA"),
            [("XAA", "XBB", Decimal(2)), ("XBB", "USD", Decimal(3))],
            [("XAA",)],
        )  # type: ignore[arg-type]
    )
    assert bases == ["USD", "XAA", "XBB"]


def test_a_pegged_reporting_currency_stores_its_own_anchor() -> None:
    """Reporting in a pegged currency writes INR->AED and nothing that exposes the peg.

    Every one of those rows travelled through the 3.6725, and without a USD->AED row there is
    nothing to check that factor against. The stored USD->AED rate *is* the peg rate.
    """
    _, bases, _ = reporting_config(
        _conn(("AED", "INR"), [("AED", "USD", Decimal("3.6725"))], [("INR",)])  # type: ignore[arg-type]
    )
    assert "USD" in bases, "a pegged reporting currency must store its anchor"
    assert "AED" not in bases


def test_fx_rate_openapi_schema_has_structured_client_fields() -> None:
    schema = app.openapi()
    response_schema = schema["paths"]["/fx/{on}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema["type"] == "array"
    assert response_schema["items"] == {"$ref": "#/components/schemas/FxRateResponse"}

    rate_schema = schema["components"]["schemas"]["FxRateResponse"]
    assert set(rate_schema["required"]) == {
        "base",
        "quote",
        "rate",
        "source",
        "carried",
        "carried_from",
    }
    assert rate_schema["properties"]["base"]["type"] == "string"
    assert rate_schema["properties"]["quote"]["type"] == "string"
    assert rate_schema["properties"]["rate"]["type"] == "string"
    assert rate_schema["properties"]["source"]["type"] == "string"
    assert rate_schema["properties"]["carried"]["type"] == "boolean"
    assert rate_schema["properties"]["carried_from"]["anyOf"] == [
        {"type": "string", "format": "date"},
        {"type": "null"},
    ]


def test_fx_rate_endpoint_serializes_decimal_rate_and_carry_date(
    monkeypatch: MonkeyPatch,
) -> None:
    from oneledger.routes import fx

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def execute(self, *_: object) -> None:
            pass

        def fetchall(self) -> list[tuple[str, str, Decimal, str, bool, date | None]]:
            return [("AED", "INR", Decimal("22.6800000000"), "derived_peg", True, date(2024, 1, 5))]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def cursor(self) -> Cursor:
            return Cursor()

    app.dependency_overrides[require_owner] = lambda: OwnerSession(
        user_id=USER_ID,
        household_id=HOUSEHOLD_ID,
        session_id=SESSION_ID,
        email="owner@example.com",
    )
    monkeypatch.setattr(fx, "connect", lambda: Connection(), raising=False)
    try:
        response = TestClient(app).get("/fx/2024-01-06")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "base": "AED",
            "quote": "INR",
            "rate": "22.6800000000",
            "source": "derived_peg",
            "carried": True,
            "carried_from": "2024-01-05",
        }
    ]
