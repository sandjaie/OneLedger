"""Signed, revocable owner sessions: the API authorization boundary."""

from __future__ import annotations

import os
import secrets
from base64 import urlsafe_b64encode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import jwt
from fastapi import Cookie, HTTPException

from .db import connect

SESSION_ISSUER = "oneledger"
SESSION_AUDIENCE = "oneledger-web"
SESSION_HOURS = 12
FLOW_AUDIENCE = "oneledger-oidc-flow"
FLOW_MINUTES = 10
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class NotAuthorized(Exception):
    pass


@dataclass(frozen=True)
class OwnerSession:
    user_id: UUID
    household_id: UUID
    session_id: UUID
    email: str


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str


SessionLookup = Callable[[UUID, UUID, str], OwnerSession | None]
SessionStore = Callable[[UUID, UUID, datetime], None]


def auth_mode(value: str | None = None, web_origin: str | None = None) -> str:
    """Return the configured login mode, refusing passwordless access off loopback."""
    mode = (value if value is not None else os.environ.get("AUTH_MODE", "google")).strip().lower()
    if mode not in {"google", "local"}:
        raise RuntimeError("AUTH_MODE must be 'google' or 'local'")
    if mode == "local":
        origin = urlsplit(
            web_origin
            if web_origin is not None
            else os.environ.get("WEB_ORIGIN", "http://localhost:3000")
        )
        if (
            origin.scheme != "http"
            or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise RuntimeError("AUTH_MODE=local requires an HTTP loopback WEB_ORIGIN")
    return mode


def b64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def create_oidc_flow(now: datetime | None = None) -> tuple[str, str, str, str]:
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    state, nonce, verifier = (
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(64),
    )
    token = jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "iat": int(issued_at.timestamp()),
            "exp": int((issued_at + timedelta(minutes=FLOW_MINUTES)).timestamp()),
            "iss": SESSION_ISSUER,
            "aud": FLOW_AUDIENCE,
        },
        session_secret(),
        algorithm="HS256",
    )
    return token, state, nonce, b64url(sha256(verifier.encode()).digest())


def verify_oidc_flow(token: str, presented_state: str) -> tuple[str, str]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            session_secret(),
            algorithms=["HS256"],
            issuer=SESSION_ISSUER,
            audience=FLOW_AUDIENCE,
            options={"require": ["state", "nonce", "verifier", "iat", "exp", "iss", "aud"]},
        )
        if (
            not all(
                isinstance(claims[name], str) and claims[name]
                for name in ("state", "nonce", "verifier")
            )
            or type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or claims["exp"] - claims["iat"] != FLOW_MINUTES * 60
            or not compare_digest(claims["state"], presented_state)
        ):
            raise NotAuthorized("invalid sign-in flow")
        return claims["nonce"], claims["verifier"]
    except (jwt.PyJWTError, KeyError, TypeError, NotAuthorized) as exc:
        raise NotAuthorized("invalid sign-in flow") from exc


def _google_jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(GOOGLE_JWKS_URL)


def verify_google_id_token(
    token: str, nonce: str, *, jwks_client: jwt.PyJWKClient | None = None
) -> GoogleIdentity:
    try:
        client = jwks_client or _google_jwks_client()
        key = client.get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=os.environ["GOOGLE_CLIENT_ID"],
            issuer=GOOGLE_ISSUERS,
            options={
                "require": ["iss", "aud", "iat", "exp", "nonce", "sub", "email", "email_verified"]
            },
        )
        if (
            not isinstance(claims["nonce"], str)
            or not compare_digest(claims["nonce"], nonce)
            or not isinstance(claims["sub"], str)
            or not claims["sub"]
            or not isinstance(claims["email"], str)
            or claims["email_verified"] is not True
        ):
            raise NotAuthorized("invalid Google identity")
        return GoogleIdentity(claims["sub"], claims["email"].strip().lower())
    except (jwt.PyJWTError, KeyError, TypeError, NotAuthorized) as exc:
        raise NotAuthorized("invalid Google identity") from exc


def bind_google_owner(subject: str, email: str) -> UUID:
    normalized = email.strip().lower()
    if normalized != _allowed_email():
        raise NotAuthorized("address is not the permitted account")
    # A database failure is not a verdict about this identity. Converting one into NotAuthorized
    # sent a correctly configured owner to /unauthorized instead of the retry path; the callback
    # already routes unexpected exceptions to retry, exactly as it does for issue_session.
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """WITH candidate AS (
                 SELECT id FROM app_user
                  WHERE role = 'owner' AND lower(btrim(email)) = %s
               ), eligible AS (
                 SELECT id FROM candidate WHERE (SELECT count(*) FROM candidate) = 1
               )
               UPDATE app_user u SET google_subject = COALESCE(u.google_subject, %s)
                FROM eligible e
               WHERE u.id = e.id
                 AND (u.google_subject IS NULL OR u.google_subject = %s)
                 AND NOT EXISTS (
                   SELECT 1 FROM app_user other
                    WHERE other.id <> u.id AND other.google_subject = %s
                 )
               RETURNING u.id""",
            (normalized, subject, subject, subject),
        )
        row = cur.fetchone()
    if row is None:
        raise NotAuthorized("no unique permitted owner binding")
    return UUID(str(row[0]))


def session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET is not set: sessions cannot be verified without it. "
            "Copy .env.example to .env and generate one with `openssl rand -hex 32`."
        )
    return secret


def _allowed_email(value: str | None = None) -> str:
    allowed = value if value is not None else os.environ.get("ALLOWED_USER_EMAIL", "")
    if not isinstance(allowed, str) or not allowed.strip():
        raise NotAuthorized("permitted account is not configured")
    return allowed.strip().lower()


def local_owner() -> tuple[UUID, str]:
    """Resolve the one configured owner without creating a second identity mechanism."""
    email = _allowed_email()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, lower(btrim(email))
                 FROM app_user
                WHERE role = 'owner' AND lower(btrim(email)) = %s""",
            (email,),
        )
        rows = cur.fetchall()
    if len(rows) != 1:
        raise NotAuthorized("no unique configured local owner")
    row = rows[0]
    return UUID(str(row[0])), str(row[1])


def _lookup_active_session(user_id: UUID, session_id: UUID, email: str) -> OwnerSession | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT u.household_id
                 FROM app_session s JOIN app_user u ON u.id = s.app_user_id
                WHERE s.id = %s AND s.app_user_id = %s
                  AND s.revoked_at IS NULL AND s.expires_at > now()
                  AND u.role = 'owner' AND lower(btrim(u.email)) = %s""",
            (session_id, user_id, email),
        )
        row = cur.fetchone()
    return OwnerSession(user_id, row[0], session_id, email) if row else None


def verify_session(
    token: str, *, lookup: SessionLookup | None = None, allowed_email: str | None = None
) -> OwnerSession:
    """Validate every signed claim, then require the matching active owner record."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            session_secret(),
            algorithms=["HS256"],
            issuer=SESSION_ISSUER,
            audience=SESSION_AUDIENCE,
            options={"require": ["sub", "email", "exp", "iat", "jti", "iss", "aud"]},
        )
        if (
            not isinstance(claims["sub"], str)
            or not isinstance(claims["jti"], str)
            or not isinstance(claims["email"], str)
            or type(claims["exp"]) is not int
            or type(claims["iat"]) is not int
            or claims["exp"] - claims["iat"] != SESSION_HOURS * 60 * 60
        ):
            raise NotAuthorized("malformed session claims")
        user_id, session_id = UUID(claims["sub"]), UUID(claims["jti"])
        email = claims["email"].strip().lower()
        if email != _allowed_email(allowed_email):
            raise NotAuthorized("address is not the permitted account")
        owner = (lookup or _lookup_active_session)(user_id, session_id, email)
        if owner is None:
            raise NotAuthorized("session is not active for an owner")
        return owner
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, NotAuthorized) as exc:
        raise NotAuthorized("invalid owner session") from exc


def _store_session(session_id: UUID, user_id: UUID, expires_at: datetime) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO app_session (id, app_user_id, expires_at)
               SELECT %s, id, %s FROM app_user
                WHERE id = %s AND role = 'owner' AND lower(btrim(email)) = %s
               RETURNING id""",
            (session_id, expires_at, user_id, _allowed_email()),
        )
        if cur.fetchone() is None:
            raise NotAuthorized("no permitted owner row")


def issue_session(
    user_id: UUID,
    email: str,
    *,
    store: SessionStore | None = None,
    now: datetime | None = None,
) -> str:
    """Persist then sign the 12-hour local session used by the later OIDC callback."""
    normalized = email.strip().lower()
    if normalized != _allowed_email():
        raise NotAuthorized("address is not the permitted account")
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + timedelta(hours=SESSION_HOURS)
    session_id = uuid4()
    (store or _store_session)(session_id, user_id, expires_at)
    return jwt.encode(
        {
            "sub": str(user_id),
            "email": normalized,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": str(session_id),
            "iss": SESSION_ISSUER,
            "aud": SESSION_AUDIENCE,
        },
        session_secret(),
        algorithm="HS256",
    )


def revoke_session(owner: OwnerSession) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE app_session SET revoked_at = COALESCE(revoked_at, now())
                WHERE id = %s AND app_user_id = %s""",
            (owner.session_id, owner.user_id),
        )


def require_owner(session: str = Cookie(default="")) -> OwnerSession:
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    try:
        return verify_session(session)
    except NotAuthorized as exc:
        raise HTTPException(status_code=403, detail="not authorized") from exc
