"""Synthetic detailed-investment PDFs built on the shared byte fixture."""

from __future__ import annotations

from dataclasses import dataclass

from tests.fixtures.synthetic_pdf import Placement, build_pdf

_PAGE_SIZE = (860.0, 792.0)
_COLUMNS = (
    (20.0, "DATE"),
    (100.0, "EVENT"),
    (255.0, "UNITS"),
    (335.0, "PRICE"),
    (405.0, "GROSS"),
    (475.0, "CHARGES"),
    (550.0, "TAX"),
    (610.0, "NET"),
    (690.0, "CURRENCY"),
    (765.0, "REFERENCE"),
)


@dataclass(frozen=True, slots=True)
class InvestmentEventLine:
    event_date: str
    event_kind: str
    units: str
    price: str
    gross_amount: str
    charges: str
    withheld_tax: str
    net_amount: str
    currency: str = "XAA"
    source_event_id: str = "SYNTHETIC-EVENT"


def _page_header(page: int, *, account_kind: str, security_reference: str) -> list[Placement]:
    placements = [
        Placement(page=page, x=20, y=760, text="DETAILED INVESTMENT STATEMENT V1"),
        Placement(page=page, x=20, y=730, text=f"ACCOUNT {account_kind} MASKED ****A1B2"),
        Placement(page=page, x=20, y=700, text=f"SECURITY {security_reference}"),
        Placement(page=page, x=20, y=670, text="TRANSACTIONS"),
    ]
    placements.extend(Placement(page=page, x=x, y=640, text=heading) for x, heading in _COLUMNS)
    return placements


def build_detailed_investment_pdf(
    events: list[InvestmentEventLine],
    *,
    rows_per_page: int | None = None,
    account_kind: str = "folio",
    security_reference: str = "SYNTHETIC-SECURITY-A",
) -> bytes:
    """Return synthetic bytes; no source PDF or provider-specific format is retained."""
    if rows_per_page is not None and rows_per_page < 1:
        raise ValueError("rows_per_page must be positive")
    page_count = 1 if rows_per_page is None else max(1, (len(events) - 1) // rows_per_page + 1)
    placements: list[Placement] = []
    for page in range(page_count):
        placements.extend(
            _page_header(page, account_kind=account_kind, security_reference=security_reference)
        )

    per_page = rows_per_page or max(1, len(events))
    for index, event in enumerate(events):
        page = index // per_page
        row = index % per_page
        y = 605.0 - row * 32.0
        values = (
            event.event_date,
            event.event_kind,
            event.units,
            event.price,
            event.gross_amount,
            event.charges,
            event.withheld_tax,
            event.net_amount,
            event.currency,
            event.source_event_id,
        )
        placements.extend(
            Placement(page=page, x=x, y=y, text=value)
            for (x, _), value in zip(_COLUMNS, values, strict=True)
        )
    return build_pdf(placements, page_count=page_count, page_size=_PAGE_SIZE)
