from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
EMAIL = "owner@example.com"


def signed_token(**changes: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(USER_ID),
        "email": EMAIL,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=12)).timestamp()),
        "jti": str(SESSION_ID),
        "iss": "oneledger",
        "aud": "oneledger-web",
    }
    claims.update(changes)
    return jwt.encode(claims, auth.session_secret(), algorithm="HS256")


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email=EMAIL
    )


def client_with(token: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set("session", token)
    return client


def allow_old_and_new_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_lookup_active_session", lambda *_: owner(), raising=False)


def test_session_without_expiry_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    allow_old_and_new_lookup(monkeypatch)
    token = signed_token()
    claims = jwt.decode(
        token, auth.session_secret(), algorithms=["HS256"], options={"verify_signature": False}
    )
    for claim in ("exp", "iat", "jti", "iss", "aud"):
        del claims[claim]
    token = jwt.encode(claims, auth.session_secret(), algorithm="HS256")
    assert client_with(token).get("/whoami").status_code == 403


@pytest.mark.parametrize("claim,value", [("sub", "not-a-uuid"), ("jti", 7), ("iat", "now")])
def test_malformed_session_claims_fail_safely(
    monkeypatch: pytest.MonkeyPatch, claim: str, value: object
) -> None:
    allow_old_and_new_lookup(monkeypatch)
    assert client_with(signed_token(**{claim: value})).get("/whoami").status_code == 403


def test_session_without_an_active_owner_record_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_lookup_active_session", lambda *_: None, raising=False)
    assert client_with(signed_token()).get("/whoami").status_code == 403


def test_issue_session_persists_matching_twelve_hour_record() -> None:
    saved: list[tuple[UUID, UUID, datetime]] = []
    token = auth.issue_session(USER_ID, EMAIL, store=lambda *args: saved.append(args))
    claims = jwt.decode(
        token,
        auth.session_secret(),
        algorithms=["HS256"],
        issuer="oneledger",
        audience="oneledger-web",
    )
    session_id, user_id, expires_at = saved[0]
    assert (UUID(claims["jti"]), UUID(claims["sub"])) == (session_id, user_id)
    assert claims["email"] == EMAIL
    assert claims["exp"] - claims["iat"] == 12 * 60 * 60
    assert int(expires_at.timestamp()) == claims["exp"]
    assert auth.verify_session(token, lookup=lambda *_: owner()) == owner()


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "member@example.com"},
        {"iss": "another-service"},
        {"aud": "another-client"},
        {"exp": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())},
    ],
)
def test_wrong_identity_or_expired_signature_is_rejected(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    allow_old_and_new_lookup(monkeypatch)
    assert client_with(signed_token(**changes)).get("/whoami").status_code == 403


def test_session_lifetime_must_be_exactly_twelve_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    allow_old_and_new_lookup(monkeypatch)
    issued = int(datetime.now(UTC).timestamp())
    token = signed_token(iat=issued, exp=issued + 13 * 60 * 60)
    assert client_with(token).get("/whoami").status_code == 403


@pytest.mark.parametrize(
    ("cookie_secure", "secure_attribute"),
    [("0", False), ("1", True)],
)
def test_logout_revokes_the_exact_session_and_rejects_replay(
    monkeypatch: pytest.MonkeyPatch, cookie_secure: str, secure_attribute: bool
) -> None:
    revoked: set[UUID] = set()
    monkeypatch.setenv("COOKIE_SECURE", cookie_secure)
    monkeypatch.setattr(
        auth,
        "_lookup_active_session",
        lambda *_: None if SESSION_ID in revoked else owner(),
        raising=False,
    )
    monkeypatch.setattr(
        auth,
        "revoke_session",
        lambda session: revoked.add(session.session_id),
        raising=False,
    )

    old_cookie = signed_token()
    client = client_with(old_cookie)
    response = client.post("/auth/logout", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 204
    assert SESSION_ID in revoked
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("session=")
    assert "Max-Age=0" in cookie
    assert "expires=" in cookie.lower()
    assert "Path=/" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert ("Secure" in cookie) is secure_attribute
    client.cookies.set("session", old_cookie)
    assert client.get("/whoami").status_code == 403


@pytest.mark.parametrize("origin", [None, "https://attacker.example"])
def test_cookie_authenticated_mutation_requires_exact_origin(
    monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    monkeypatch.setattr(auth, "_lookup_active_session", lambda *_: owner(), raising=False)
    headers = {"Origin": origin} if origin else {}
    response = client_with(signed_token()).post(
        "/fx/backfill?start=2024-01-02&end=2024-01-01", headers=headers
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "invalid origin"}
