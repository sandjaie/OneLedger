# api/oneledger/auth.py
"""Authorization. Since ADR-0013 this is the boundary, not RLS.

Google authenticates any Google account, so a valid sign-in proves identity and nothing else.
A caller is authorized only if their session resolves to a row in app_user.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import jwt
from fastapi import Cookie, HTTPException

from .db import connect


class NotAuthorized(Exception):
    pass


def resolve_owner(
    claims: dict[str, Any],
    lookup: Callable[[str], str | None],
    allowed_email: str | None = None,
) -> str:
    """Fails closed: a missing subject, an unknown subject, or the wrong address is a rejection."""
    subject = claims.get("sub")
    if not subject:
        raise NotAuthorized("no subject in token")

    # Passed in, not read from the environment: a function that reaches for os.environ is a
    # function whose tests need monkeypatching to say anything. claims_from_id_token already
    # takes `allowed` this way; this matches it.
    allowed = (allowed_email or "").strip().lower()
    email = (claims.get("email") or "").strip().lower()
    if not allowed or email != allowed:
        raise NotAuthorized("address is not the permitted account")

    household_id = lookup(subject)
    if household_id is None:
        raise NotAuthorized("no app_user for this subject")
    return household_id


def _lookup(subject: str) -> str | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT household_id FROM app_user WHERE id = %s", (subject,))
        row = cur.fetchone()
    return str(row[0]) if row else None


def session_secret() -> str:
    """The key that signs session cookies. Absent is a configuration failure, not a request one.

    Reading os.environ["SESSION_SECRET"] inline raised KeyError from inside require_owner's
    try block, where the except clause does not catch it — so a service deployed without the
    variable answered every request with a 500 and no explanation. main.py calls this at
    startup so the process refuses to run instead.
    """
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET is not set: sessions cannot be verified without it. "
            "Copy .env.example to .env and generate one with `openssl rand -hex 32`."
        )
    return secret


def require_owner(session: str = Cookie(default="")) -> str:
    """FastAPI dependency. Every route depends on this — there is no unauthenticated route.

    The cookie is issued by this service after Google OIDC (Task 10), signed with SESSION_SECRET.
    """
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    try:
        claims = jwt.decode(session, session_secret(), algorithms=["HS256"])
        return resolve_owner(claims, _lookup, os.environ.get("ALLOWED_USER_EMAIL"))
    except (jwt.PyJWTError, NotAuthorized) as exc:
        raise HTTPException(status_code=403, detail="not authorized") from exc
