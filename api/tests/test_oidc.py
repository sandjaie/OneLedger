from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app
from oneledger.routes import auth as auth_routes

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
EMAIL = "owner@example.com"
SENTINEL = "sensitive-callback-code"


class StaticJwksClient(jwt.PyJWKClient):
    def __init__(self, jwk: dict[str, Any]) -> None:
        super().__init__("https://unused.invalid/jwks")
        self.jwk = jwk

    def fetch_data(self) -> dict[str, Any]:
        return {"keys": [self.jwk]}


@pytest.fixture
def google_keys() -> tuple[RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    return private_key, jwk


@pytest.fixture(autouse=True)
def google_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")


def sign_google_token(private_key: RSAPrivateKey, **changes: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "nonce": "expected-nonce",
        "sub": "google-subject",
        "email": EMAIL,
        "email_verified": True,
    }
    claims.update(changes)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def decode_flow(cookie: str) -> dict[str, Any]:
    return jwt.decode(
        cookie,
        auth.session_secret(),
        algorithms=["HS256"],
        issuer="oneledger",
        audience="oneledger-oidc-flow",
    )


@pytest.mark.parametrize(("cookie_secure", "secure"), [("0", False), ("1", True)])
def test_login_uses_state_nonce_and_pkce_in_a_signed_short_lived_cookie(
    monkeypatch: pytest.MonkeyPatch, cookie_secure: str, secure: bool
) -> None:
    monkeypatch.setenv("COOKIE_SECURE", cookie_secure)
    response = TestClient(app).get("/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(response.headers["location"]).query)
    cookie = response.headers["set-cookie"]
    flow = decode_flow(response.cookies["oidc_flow"])
    expected_challenge = auth.b64url(sha256(flow["verifier"].encode()).digest())
    assert response.status_code == 303
    assert response.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query == {
        "client_id": ["test-client-id"],
        "redirect_uri": ["http://localhost:8000/auth/callback"],
        "response_type": ["code"],
        "scope": ["openid email"],
        "state": [flow["state"]],
        "nonce": [flow["nonce"]],
        "code_challenge": [expected_challenge],
        "code_challenge_method": ["S256"],
        "prompt": ["select_account"],
    }
    assert flow["exp"] - flow["iat"] == 600
    assert all(flag in cookie for flag in ("HttpOnly", "Max-Age=600", "Path=/", "SameSite=lax"))
    assert ("Secure" in cookie) is secure


@pytest.mark.parametrize("case", ["absent", "mismatch", "expired", "denied"])
def test_invalid_state_is_sanitized_and_prevents_exchange(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    exchanged: list[str] = []
    monkeypatch.setattr(auth_routes, "_exchange_code", lambda *_: exchanged.append("called"))
    client = TestClient(app)
    state = "presented-state"
    if case == "mismatch":
        client.get("/auth/login", follow_redirects=False)
    elif case == "expired":
        now = datetime.now(UTC)
        token, state, _, _ = auth.create_oidc_flow(now - timedelta(minutes=11))
        client.cookies.set("oidc_flow", token)

    query = f"error={SENTINEL}" if case == "denied" else f"code={SENTINEL}&state={state}"
    response = client.get(f"/auth/callback?{query}", follow_redirects=False)

    assert response.status_code == 303
    assert (
        response.headers["location"] == "http://localhost:3000/login?reason=authentication_failed"
    )
    assert exchanged == []
    assert SENTINEL not in str(response.headers) + response.text
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://issuer.invalid"},
        {"aud": "another-client"},
        {"nonce": "wrong-nonce"},
        {"exp": 1},
        {"sub": ""},
        {"email_verified": False},
        {"email_verified": "true"},
        None,
    ],
)
def test_google_token_rejects_invalid_identity_claims(
    google_keys: tuple[RSAPrivateKey, dict[str, Any]], changes: dict[str, object] | None
) -> None:
    private_key, jwk = google_keys
    signer = private_key if changes is not None else rsa.generate_private_key(65537, 2048)
    token = sign_google_token(signer, **(changes or {}))
    with pytest.raises(auth.NotAuthorized):
        auth.verify_google_id_token(token, "expected-nonce", jwks_client=StaticJwksClient(jwk))


def test_token_exchange_sends_the_exact_redirect_and_pkce_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, str] = {}

    def post(url: str, *, data: dict[str, str], timeout: int) -> httpx.Response:
        assert timeout == 10
        sent.update(data)
        return httpx.Response(200, json={"id_token": "token"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert auth_routes._exchange_code("code", "verifier") == "token"
    assert sent["redirect_uri"] == "http://localhost:8000/auth/callback"
    assert sent["code_verifier"] == "verifier"


def test_callback_exchanges_with_pkce_binds_owner_and_sets_session(
    monkeypatch: pytest.MonkeyPatch, google_keys: tuple[RSAPrivateKey, dict[str, Any]]
) -> None:
    private_key, jwk = google_keys
    client = TestClient(app)
    login = client.get("/auth/login", follow_redirects=False)
    flow = decode_flow(login.cookies["oidc_flow"])
    token = sign_google_token(private_key, nonce=flow["nonce"])
    exchanged: list[tuple[str, str]] = []
    stored: list[object] = []

    def exchange(code: str, verifier: str) -> str:
        exchanged.append((code, verifier))
        return token

    monkeypatch.setattr(auth_routes, "_exchange_code", exchange)
    monkeypatch.setattr(auth, "_google_jwks_client", lambda: StaticJwksClient(jwk))
    monkeypatch.setattr(auth, "bind_google_owner", lambda *_: USER_ID)
    monkeypatch.setattr(auth, "_store_session", lambda *values: stored.append(values))

    response = client.get(
        f"/auth/callback?code={SENTINEL}&state={flow['state']}", follow_redirects=False
    )

    cookies = response.headers.get_list("set-cookie")
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:3000/"
    assert exchanged == [(SENTINEL, flow["verifier"])]
    assert stored and response.cookies["session"]
    assert any(c.startswith("oidc_flow=") and "Max-Age=0" in c for c in cookies)
    assert any(
        c.startswith("session=")
        and all(f in c for f in ("HttpOnly", "Max-Age=43200", "Path=/", "SameSite=lax"))
        for c in cookies
    )


@pytest.mark.parametrize("failure", ["wrong-email", "database"])
def test_identity_or_database_denial_gets_no_session(
    monkeypatch: pytest.MonkeyPatch,
    google_keys: tuple[RSAPrivateKey, dict[str, Any]],
    failure: str,
) -> None:
    private_key, jwk = google_keys
    client = TestClient(app)
    login = client.get("/auth/login", follow_redirects=False)
    flow = decode_flow(login.cookies["oidc_flow"])
    token = sign_google_token(
        private_key,
        nonce=flow["nonce"],
        email="other@example.com" if failure == "wrong-email" else EMAIL,
    )
    monkeypatch.setattr(auth_routes, "_exchange_code", lambda *_: token)
    monkeypatch.setattr(auth, "_google_jwks_client", lambda: StaticJwksClient(jwk))
    if failure == "database":
        monkeypatch.setattr(auth, "bind_google_owner", lambda *_: USER_ID)
        monkeypatch.setattr(
            auth, "issue_session", lambda *_: (_ for _ in ()).throw(RuntimeError("database down"))
        )

    response = client.get(
        f"/auth/callback?code={SENTINEL}&state={flow['state']}", follow_redirects=False
    )
    suffix = "/login?reason=authentication_failed" if failure == "database" else "/unauthorized"
    assert response.headers["location"] == "http://localhost:3000" + suffix
    assert not any(c.startswith("session=") for c in response.headers.get_list("set-cookie"))


@pytest.mark.parametrize(
    "missing", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"]
)
def test_login_without_google_configuration_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv(missing, "")
    response = TestClient(app).get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json() == {"detail": "sign-in unavailable"}
    assert "oidc_flow" not in response.cookies


def test_login_without_a_session_secret_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RuntimeError arm of the handler: session_secret() raises when the key is unset."""
    monkeypatch.setenv("SESSION_SECRET", "")
    response = TestClient(app).get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json() == {"detail": "sign-in unavailable"}


def test_local_login_issues_the_existing_session_without_google_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling the Google branch in local mode would make offline development unavailable."""
    stored: list[object] = []
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("WEB_ORIGIN", "http://localhost:3000")
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "local_owner", lambda: (USER_ID, EMAIL), raising=False)
    monkeypatch.setattr(auth, "_store_session", lambda *values: stored.append(values))

    response = TestClient(app).get("/auth/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:3000/"
    assert stored
    assert response.cookies["session"]
    assert "oidc_flow" not in response.cookies


def test_owner_binding_rejects_a_subject_already_bound_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subject stored on a different app_user is a conflicting binding, not an outage.

    Without a collision guard the UPDATE of an unbound owner hits idx_app_user_google_subject
    and UniqueViolation is routed to retry. The statement must match no row instead.
    """

    class Cursor:
        row: tuple[UUID] | None = None

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def execute(self, sql: str, _params: tuple[object, ...]) -> None:
            if "NOT EXISTS" not in sql:
                raise psycopg.errors.UniqueViolation("idx_app_user_google_subject")
            self.row = None

        def fetchone(self) -> tuple[UUID] | None:
            return self.row

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr(auth, "connect", lambda: Connection(), raising=False)
    with pytest.raises(auth.NotAuthorized):
        auth.bind_google_owner("taken-subject", EMAIL)


def test_a_conflicting_subject_is_unauthorized_not_a_retry(
    monkeypatch: pytest.MonkeyPatch, google_keys: tuple[RSAPrivateKey, dict[str, Any]]
) -> None:
    private_key, jwk = google_keys
    client = TestClient(app)
    login = client.get("/auth/login", follow_redirects=False)
    flow = decode_flow(login.cookies["oidc_flow"])
    token = sign_google_token(private_key, nonce=flow["nonce"])

    monkeypatch.setattr(auth_routes, "_exchange_code", lambda *_: token)
    monkeypatch.setattr(auth, "_google_jwks_client", lambda: StaticJwksClient(jwk))
    monkeypatch.setattr(
        auth, "bind_google_owner", lambda *_: (_ for _ in ()).throw(auth.NotAuthorized("taken"))
    )

    response = client.get(
        f"/auth/callback?code={SENTINEL}&state={flow['state']}", follow_redirects=False
    )
    assert response.headers["location"] == "http://localhost:3000/unauthorized"
    assert not any(c.startswith("session=") for c in response.headers.get_list("set-cookie"))


def test_owner_binding_lets_database_failures_through(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable() -> object:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(auth, "connect", unreachable, raising=False)
    with pytest.raises(psycopg.OperationalError):
        auth.bind_google_owner("google-subject", EMAIL)


def test_a_database_outage_during_binding_offers_retry_not_rejection(
    monkeypatch: pytest.MonkeyPatch, google_keys: tuple[RSAPrivateKey, dict[str, Any]]
) -> None:
    """A correctly permitted owner must never be told their account is not allowed because the
    database was down. /unauthorized is a verdict about identity; this is an outage."""
    private_key, jwk = google_keys
    client = TestClient(app)
    login = client.get("/auth/login", follow_redirects=False)
    flow = decode_flow(login.cookies["oidc_flow"])
    token = sign_google_token(private_key, nonce=flow["nonce"])

    monkeypatch.setattr(auth_routes, "_exchange_code", lambda *_: token)
    monkeypatch.setattr(auth, "_google_jwks_client", lambda: StaticJwksClient(jwk))
    monkeypatch.setattr(
        auth, "connect", lambda: (_ for _ in ()).throw(psycopg.OperationalError("down"))
    )

    response = client.get(
        f"/auth/callback?code={SENTINEL}&state={flow['state']}", follow_redirects=False
    )

    assert response.headers["location"] == (
        "http://localhost:3000/login?reason=authentication_failed"
    )
    assert not any(c.startswith("session=") for c in response.headers.get_list("set-cookie"))
    assert SENTINEL not in response.text
