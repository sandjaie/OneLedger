"""Pure summary-statement checkpoint extraction contracts."""

from __future__ import annotations

from decimal import Decimal

from oneledger.ingest.words import Line, Page, Word
from oneledger.investments.adapters import extract_statement

_COLUMNS = (
    (20.0, "AS_OF"),
    (130.0, "UNITS"),
    (275.0, "COST"),
    (410.0, "VALUE"),
    (545.0, "CURRENCY"),
    (650.0, "REFERENCE"),
)


def _words(values: tuple[tuple[float, str], ...], top: float) -> Line:
    return Line(
        top,
        [Word(x, x + max(8.0, len(value) * 5.0), top, top + 9.0, value) for x, value in values],
    )


def _text(value: str, top: float) -> Line:
    words = tuple((20.0 + index * 65.0, part) for index, part in enumerate(value.split()))
    return _words(words, top)


def _summary_page(
    *,
    security: str | None = "SYNTHETIC SECURITY",
    units: str = "2.50000000",
    cost: str = "25.0000",
    value: str = "30.0000",
    reference: str = "SYNTHETIC-CHECKPOINT",
) -> Page:
    lines = [
        _text("SUMMARY INVESTMENT STATEMENT V1", 20.0),
        _text("ACCOUNT folio MASKED ****A1B2", 40.0),
    ]
    if security is not None:
        lines.append(_text(f"SECURITY {security}", 60.0))
    lines.extend(
        (
            _text("POSITION SUMMARY", 80.0),
            _words(_COLUMNS, 100.0),
            _words(
                tuple(
                    (x, value)
                    for (x, _), value in zip(
                        _COLUMNS,
                        ("2026-06-30", units, cost, value, "XAA", reference),
                        strict=True,
                    )
                    if value
                ),
                120.0,
            ),
        )
    )
    return Page(lines, has_text=True)


def test_content_selected_summary_emits_only_exact_checkpoint_facts() -> None:
    result = extract_statement((_summary_page(),))

    assert result.supported is True
    assert result.source_contract == "summary_fund_statement_v1"
    assert result.extraction_version == "1"
    assert result.reasons == ()
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.drafts == ()
    assert group.reasons == ()
    assert len(group.checkpoints) == 1
    checkpoint = group.checkpoints[0]
    assert checkpoint.as_of_date.isoformat() == "2026-06-30"
    assert checkpoint.reported_units == Decimal("2.50000000")
    assert checkpoint.reported_cost_native == Decimal("25.0000")
    assert checkpoint.reported_value_native == Decimal("30.0000")
    assert checkpoint.currency == "XAA"
    assert checkpoint.declared_unit_scale == 8
    assert checkpoint.evidence.page_number == 1
    assert checkpoint.evidence.row_number == 6
    assert checkpoint.evidence.fingerprint_version == "typed_summary_checkpoint_v1"
    assert len(checkpoint.evidence.fingerprint) == 64
    assert not hasattr(checkpoint, "price")


def test_unknown_summary_security_abstains_without_manufacturing_history() -> None:
    result = extract_statement((_summary_page(security=None),))

    assert result.supported is True
    assert result.groups == ()
    assert [reason.code for reason in result.reasons] == ["unknown_security"]
    assert "SYNTHETIC-CHECKPOINT" not in repr(result)


def test_summary_checkpoint_precision_is_atomic_and_privacy_safe() -> None:
    private_reference = "PRIVATE-IDENTIFIER-SHAPED-ALPHA123456789"
    result = extract_statement((_summary_page(units="2.123456789", reference=private_reference),))

    assert result.groups[0].checkpoints == ()
    assert [reason.code for reason in result.groups[0].reasons] == ["unrecognized_event"]
    assert private_reference not in repr(result)
