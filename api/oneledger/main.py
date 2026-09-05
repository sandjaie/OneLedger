# api/oneledger/main.py
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import require_owner, session_secret
from .routes import fx

# Fail at startup, not on the first request. A service that boots without the key that signs
# its sessions is broken in a way every caller will experience as an unexplained 500.
session_secret()

# /docs, /redoc and /openapi.json are registered by default and depend on nothing, which
# would make three unauthenticated routes rather than the one the boundary allows —
# /openapi.json alone hands an anonymous caller the entire route surface. Generating the
# client's types does not need them served: `scripts/openapi.py` writes the schema to a file.
app = FastAPI(
    title="OneLedger",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
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


app.include_router(fx.router)


@app.get("/health")
def health() -> dict[str, str]:
    """The only route that does not require authorization."""
    return {"status": "ok"}


@app.get("/whoami")
def whoami(household_id: str = Depends(require_owner)) -> dict[str, str]:
    return {"household_id": household_id}
