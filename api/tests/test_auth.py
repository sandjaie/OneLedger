# api/tests/test_auth.py
import pytest

from oneledger.auth import NotAuthorized, resolve_owner


def test_rejects_a_token_with_no_matching_app_user() -> None:
    """Google authenticates any Google account. Authentication is not authorization."""
    with pytest.raises(NotAuthorized):
        resolve_owner(
            claims={"sub": "00000000-0000-0000-0000-000000000000", "email": "stranger@example.com"},
            lookup=lambda uid: None,
            allowed_email="owner@example.com",
        )


def test_rejects_a_missing_subject_rather_than_defaulting_open() -> None:
    with pytest.raises(NotAuthorized):
        resolve_owner(
            claims={}, lookup=lambda uid: "household-1", allowed_email="owner@example.com"
        )


def test_accepts_a_token_that_resolves_to_an_app_user() -> None:
    household = resolve_owner(
        claims={"sub": "11111111-1111-1111-1111-111111111111", "email": "owner@example.com"},
        lookup=lambda uid: "household-1",
        allowed_email="owner@example.com",
    )
    assert household == "household-1"
