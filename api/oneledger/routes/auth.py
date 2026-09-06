import os
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .. import auth
from ..auth import OwnerSession

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthExchangeError(Exception):
    pass


def _config(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise OAuthExchangeError("sign-in configuration is incomplete")
    return value


def _web_destination(path: str) -> str:
    return os.environ.get("WEB_ORIGIN", "http://localhost:3000").rstrip("/") + path


def _clear_flow(response: Response) -> Response:
    response.delete_cookie(
        "oidc_flow",
        path="/auth",
        secure=os.environ.get("COOKIE_SECURE") == "1",
        httponly=True,
        samesite="lax",
    )
    return response


def _exchange_code(code: str, verifier: str) -> str:
    try:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": _config("GOOGLE_CLIENT_ID"),
                "client_secret": _config("GOOGLE_CLIENT_SECRET"),
                "redirect_uri": _config("GOOGLE_REDIRECT_URI"),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
            timeout=10,
        )
        response.raise_for_status()
        token = response.json().get("id_token")
        if not isinstance(token, str) or not token:
            raise OAuthExchangeError("token response had no ID token")
        return token
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise OAuthExchangeError("Google token exchange failed") from exc


@router.get("/login")
def login() -> Response:
    try:
        client_id = _config("GOOGLE_CLIENT_ID")
        _config("GOOGLE_CLIENT_SECRET")
        redirect_uri = _config("GOOGLE_REDIRECT_URI")
        flow_cookie, state, nonce, challenge = auth.create_oidc_flow()
    except OAuthExchangeError, RuntimeError:
        return _clear_flow(JSONResponse({"detail": "sign-in unavailable"}, status_code=503))
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}", status_code=303)
    response.set_cookie(
        "oidc_flow",
        flow_cookie,
        max_age=auth.FLOW_MINUTES * 60,
        path="/auth",
        secure=os.environ.get("COOKIE_SECURE") == "1",
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback")
def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    oidc_flow: Annotated[str, Cookie()] = "",
) -> Response:
    retry = RedirectResponse(
        _web_destination("/login?reason=authentication_failed"), status_code=303
    )
    if error or not code or not state or not oidc_flow:
        return _clear_flow(retry)
    try:
        nonce, verifier = auth.verify_oidc_flow(oidc_flow, state)
    except auth.NotAuthorized:
        return _clear_flow(retry)
    try:
        identity = auth.verify_google_id_token(_exchange_code(code, verifier), nonce)
        user_id = auth.bind_google_owner(identity.subject, identity.email)
        session = auth.issue_session(user_id, identity.email)
    except OAuthExchangeError:
        return _clear_flow(retry)
    except auth.NotAuthorized:
        denied = RedirectResponse(_web_destination("/unauthorized"), status_code=303)
        return _clear_flow(denied)
    except Exception:
        return _clear_flow(retry)
    response = RedirectResponse(_web_destination("/"), status_code=303)
    response.set_cookie(
        "session",
        session,
        max_age=auth.SESSION_HOURS * 60 * 60,
        path="/",
        secure=os.environ.get("COOKIE_SECURE") == "1",
        httponly=True,
        samesite="lax",
    )
    return _clear_flow(response)


@router.post("/logout", status_code=204)
def logout(response: Response, owner: Annotated[OwnerSession, Depends(auth.require_owner)]) -> None:
    auth.revoke_session(owner)
    response.delete_cookie(
        "session",
        path="/",
        secure=os.environ.get("COOKIE_SECURE") == "1",
        httponly=True,
        samesite="lax",
    )
