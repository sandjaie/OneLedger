# api/oneledger/main.py
import os
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response

from .auth import OwnerSession, require_owner, session_secret
from .routes import (
    accounts,
    auth,
    categories,
    currencies,
    fx,
    household,
    ingest,
    layouts,
    rules,
    transactions,
    transfers,
)

# Fail at startup, not on the first request. A service that boots without the key that signs
# its sessions is broken in a way every caller will experience as an unexplained 500.
session_secret()

# /docs, /redoc and /openapi.json are registered by default and depend on nothing, which
# would add three public routes beyond health and the login/callback bootstrap boundary.
# /openapi.json alone hands an anonymous caller the entire route surface. Generating the
# client's types does not need them served: `scripts/openapi.py` writes the schema to a file.
app = FastAPI(
    title="OneLedger",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(RequestValidationError)
async def private_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Validation diagnostics describe the failure, never echo the submitted body."""
    del request
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()
            ]
        },
    )


# The web app is served from :3000 and calls this service on :8000 — a different origin,
# because the port differs. Without this the browser blocks every response, and because
# apiFetch sends credentials the origin must be named explicitly: "*" is rejected outright
# for credentialed requests. WEB_ORIGIN is the same variable the OIDC callback redirects to.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("WEB_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def reject_cross_origin_mutations(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.cookies.get("session"):
        expected = os.environ.get("WEB_ORIGIN", "http://localhost:3000")
        if request.headers.get("origin") != expected:
            return JSONResponse({"detail": "invalid origin"}, status_code=403)
    return await call_next(request)


app.include_router(auth.router)
app.include_router(fx.router)
app.include_router(household.router)
app.include_router(currencies.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(layouts.router)
app.include_router(rules.router)
app.include_router(transfers.router)
app.include_router(ingest.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Public health route; login/callback are public, while financial routes require the owner."""
    return {"status": "ok"}


@app.get("/whoami")
def whoami(owner: Annotated[OwnerSession, Depends(require_owner)]) -> dict[str, str]:
    return {"household_id": str(owner.household_id)}
