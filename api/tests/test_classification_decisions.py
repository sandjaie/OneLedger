"""Classification-repair request boundaries that need no database.

The live suite proves storage and ledger effects. These tests keep unsafe source data and
unbounded selections from reaching that boundary at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from oneledger import auth
from oneledger.main import app
from oneledger.routes.classification_decisions import DerivedChanges

ORIGIN = {"Origin": "http://localhost:3000"}


def _owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=uuid4(), household_id=uuid4(), session_id=uuid4(), email="owner@example.invalid"
    )


def _preview_item(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "transactionId": str(uuid4()),
        "changes": {"txnType": "expense", "isExcluded": False},
    }
    body.update(changes)
    return body


def test_preview_refuses_transaction_originals_before_opening_the_database() -> None:
    app.dependency_overrides[auth.require_owner] = _owner
    try:
        response = TestClient(app).post(
            "/classification-runs/preview",
            json={
                "knowledgeVersionId": str(uuid4()),
                "reason": "synthetic repair",
                "items": [
                    _preview_item(
                        changes={
                            "txnType": "expense",
                            "amount": "-37.2500",
                            "descriptionRaw": "PRIVATE ORIGINAL",
                        }
                    )
                ],
            },
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "PRIVATE ORIGINAL" not in response.text


def test_preview_refuses_more_than_two_hundred_explicit_items() -> None:
    app.dependency_overrides[auth.require_owner] = _owner
    try:
        response = TestClient(app).post(
            "/classification-runs/preview",
            json={
                "knowledgeVersionId": str(uuid4()),
                "reason": "synthetic bounded repair",
                "items": [_preview_item() for _ in range(201)],
            },
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_preview_refuses_a_transaction_named_twice() -> None:
    transaction_id = str(uuid4())
    app.dependency_overrides[auth.require_owner] = _owner
    try:
        response = TestClient(app).post(
            "/classification-runs/preview",
            json={
                "knowledgeVersionId": str(uuid4()),
                "reason": "synthetic unambiguous repair",
                "items": [
                    _preview_item(transactionId=transaction_id),
                    _preview_item(transactionId=transaction_id),
                ],
            },
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_classification_mutations_require_an_owner_session() -> None:
    app.dependency_overrides.clear()
    anonymous = TestClient(app)
    run_id = uuid4()

    assert anonymous.post("/classification-runs/preview", json={}).status_code == 401
    assert anonymous.post(f"/classification-runs/{run_id}/apply", json={}).status_code == 401
    assert anonymous.post(f"/classification-runs/{run_id}/undo", json={}).status_code == 401


def test_apply_accepts_only_the_persisted_run_and_an_idempotency_key() -> None:
    app.dependency_overrides[auth.require_owner] = _owner
    try:
        response = TestClient(app).post(
            f"/classification-runs/{uuid4()}/apply",
            json={"idempotencyKey": "bounded-key", "items": [_preview_item()]},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_undo_derives_its_guard_from_the_source_run_not_a_client_snapshot() -> None:
    app.dependency_overrides[auth.require_owner] = _owner
    try:
        response = TestClient(app).post(
            f"/classification-runs/{uuid4()}/undo",
            json={
                "idempotencyKey": "bounded-undo-key",
                "reason": "synthetic undo",
                "expectedState": {"txnType": "expense"},
            },
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_confidence_cannot_carry_precision_the_ledger_would_round_away() -> None:
    with pytest.raises(ValidationError):
        DerivedChanges.model_validate({"classificationConfidence": "0.1234"})

    assert DerivedChanges.model_validate({"classificationConfidence": "0.123"})


def test_required_derived_values_may_be_omitted_but_not_cleared() -> None:
    for field in ("txnType", "classifiedBy", "isReviewed", "isExcluded"):
        with pytest.raises(ValidationError):
            DerivedChanges.model_validate({field: None})

    assert DerivedChanges.model_validate({"categoryId": None})
