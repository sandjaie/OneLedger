"""Signed, revocable owner sessions: the API authorization boundary."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from fastapi import Cookie, HTTPException

from .db import connect

SESSION_ISSUER = "oneledger"
SESSION_AUDIENCE = "oneledger-web"
SESSION_HOURS = 12


class NotAuthorized(Exception):
    pass


@dataclass(frozen=True)
class OwnerSession:
    user_id: UUID
    household_id: UUID
    session_id: UUID
    email: str


SessionLookup = Callable[[UUID, UUID, str], OwnerSession | None]
SessionStore = Callable[[UUID, UUID, datetime], None]


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
