"""Synthetic detailed-investment PDFs built on the shared byte fixture."""

from __future__ import annotations

from dataclasses import dataclass

from tests.fixtures.synthetic_pdf import Placement, build_pdf

_PAGE_SIZE = (1350.0, 792.0)
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
_POSITION_COLUMNS = (
    (20.0, "AS_OF"),
    (130.0, "UNITS"),
    (275.0, "COST"),
    (410.0, "VALUE"),
    (545.0, "CURRENCY"),
    (650.0, "NAV"),
    (750.0, "REFERENCE"),
)
_ACTION_COLUMNS = (
    (20.0, "DATE"),
    (100.0, "EVENT"),
    (255.0, "UNITS"),
    (335.0, "RATIO"),
    (410.0, "PRICE"),
    (480.0, "GROSS"),
    (550.0, "CHARGES"),
    (625.0, "TAX"),
    (685.0, "NET"),
    (750.0, "CURRENCY"),
    (825.0, "ACTION_REF"),
    (1100.0, "REFERENCE"),
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


@dataclass(frozen=True, slots=True)
class InvestmentCheckpointLine:
    as_of_date: str
    reported_units: str
    reported_cost_native: str
    reported_value_native: str
    currency: str
    statement_nav: str
    source_event_id: str = "SYNTHETIC-CHECKPOINT"


@dataclass(frozen=True, slots=True)
class InvestmentActionLine:
    event_date: str
    event_kind: str
    units: str
    unit_ratio: str
    price: str
    gross_amount: str
    charges: str
    withheld_tax: str
    net_amount: str
    action_reference: str
    currency: str = "XAA"
    source_event_id: str = "SYNTHETIC-ACTION"


def _page_header(
    page: int,
    *,
    account_reference: str | None,
    security_reference: str | None,
    position: bool = False,
    action: bool = False,
) -> list[Placement]:
    placements = [
        Placement(page=page, x=20, y=760, text="DETAILED INVESTMENT STATEMENT V1"),
        Placement(
            page=page,
            x=20,
            y=670,
            text="END POSITION" if position else "UNIT ACTIONS" if action else "TRANSACTIONS",
        ),
    ]
    if account_reference is not None:
        placements.append(Placement(page=page, x=20, y=730, text=f"ACCOUNT {account_reference}"))
    if security_reference is not None:
        placements.append(Placement(page=page, x=20, y=700, text=f"SECURITY {security_reference}"))
    columns = _POSITION_COLUMNS if position else _ACTION_COLUMNS if action else _COLUMNS
    placements.extend(Placement(page=page, x=x, y=640, text=heading) for x, heading in columns)
    return placements


def build_detailed_investment_pdf(
    events: list[InvestmentEventLine],
    *,
    actions: list[InvestmentActionLine] | None = None,
    checkpoints: list[InvestmentCheckpointLine] | None = None,
    rows_per_page: int | None = None,
    action_rows_per_page: int | None = None,
    checkpoint_rows_per_page: int | None = None,
    account_kind: str = "folio",
    security_reference: str = "SYNTHETIC-SECURITY-A",
    page_targets: list[tuple[str | None, str | None]] | None = None,
    data_size: float = 10.0,
) -> bytes:
    """Return synthetic bytes; no source PDF or provider-specific format is retained."""
    if rows_per_page is not None and rows_per_page < 1:
        raise ValueError("rows_per_page must be positive")
    if checkpoint_rows_per_page is not None and checkpoint_rows_per_page < 1:
        raise ValueError("checkpoint_rows_per_page must be positive")
    if action_rows_per_page is not None and action_rows_per_page < 1:
        raise ValueError("action_rows_per_page must be positive")
    actions = actions or []
    checkpoints = checkpoints or []
    event_pages = (
        0
        if not events and (actions or checkpoints)
        else (1 if rows_per_page is None else max(1, (len(events) - 1) // rows_per_page + 1))
    )
    action_pages = (
        0 if not actions else (len(actions) - 1) // (action_rows_per_page or len(actions)) + 1
    )
    checkpoint_pages = (
        0
        if not checkpoints
        else (len(checkpoints) - 1) // (checkpoint_rows_per_page or len(checkpoints)) + 1
    )
    page_count = max(1, event_pages + action_pages + checkpoint_pages)
    placements: list[Placement] = []
    for page in range(page_count):
        account, security = (
            page_targets[page]
            if page_targets is not None
            else (f"{account_kind} MASKED ****A1B2", security_reference)
        )
        placements.extend(
            _page_header(
                page,
                account_reference=account,
                security_reference=security,
                position=page >= event_pages + action_pages,
                action=event_pages <= page < event_pages + action_pages,
            )
        )

    per_page = rows_per_page or max(1, len(events))
    for index, event in enumerate(events):
        page = index // per_page
        row = index % per_page
        y = 605.0 - row * 32.0
        event_values = (
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
            Placement(page=page, x=x, y=y, text=value, size=data_size)
            for (x, _), value in zip(_COLUMNS, event_values, strict=True)
        )
    action_per_page = action_rows_per_page or max(1, len(actions))
    for index, action in enumerate(actions):
        page = event_pages + index // action_per_page
        row = index % action_per_page
        y = 605.0 - row * 32.0
        action_values = (
            action.event_date,
            action.event_kind,
            action.units,
            action.unit_ratio,
            action.price,
            action.gross_amount,
            action.charges,
            action.withheld_tax,
            action.net_amount,
            action.currency,
            action.action_reference,
            action.source_event_id,
        )
        placements.extend(
            Placement(page=page, x=x, y=y, text=value, size=data_size)
            for (x, _), value in zip(_ACTION_COLUMNS, action_values, strict=True)
        )
    checkpoint_per_page = checkpoint_rows_per_page or max(1, len(checkpoints))
    for index, checkpoint in enumerate(checkpoints):
        page = event_pages + action_pages + index // checkpoint_per_page
        row = index % checkpoint_per_page
        y = 605.0 - row * 32.0
        checkpoint_values = (
            checkpoint.as_of_date,
            checkpoint.reported_units,
            checkpoint.reported_cost_native,
            checkpoint.reported_value_native,
            checkpoint.currency,
            checkpoint.statement_nav,
            checkpoint.source_event_id,
        )
        placements.extend(
            Placement(page=page, x=x, y=y, text=value, size=data_size)
            for (x, _), value in zip(_POSITION_COLUMNS, checkpoint_values, strict=True)
        )
    return build_pdf(placements, page_count=page_count, page_size=_PAGE_SIZE)
