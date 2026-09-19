# api/oneledger/main.py
import os
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, Response

from .auth import OwnerSession, auth_mode, require_owner, session_secret
from .routes import (
    accounts,
    actions,
    auth,
    budgets,
    card_cycles,
    categories,
    classification_decisions,
    classification_review,
    commitment_resolutions,
    coverage,
    currencies,
    dashboard,
    description_reparse,
    doctor,
    documents,
    emergency_reserve,
    fx,
    goals,
    household,
    identity_backfill,
    ingest,
    investment_catalogue,
    investment_events,
    investment_funding,
    investment_imports,
    investment_prices,
    investment_reviews,
    layouts,
    merchant_knowledge,
    monthly_review,
    payslips,
    plans,
    portfolio,
    recurring,
    register,
    rules,
    salary,
    search,
    spending,
    transactions,
    transfers,
    valuations,
    wealth_history,
)

# Fail at startup, not on the first request. A service that boots without the key that signs
# its sessions is broken in a way every caller will experience as an unexplained 500.
session_secret()
auth_mode()

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


def _validation_schema_nodes(
    node: object, document: dict[str, Any], seen: frozenset[str] = frozenset()
) -> Iterator[dict[str, Any]]:
    """Expand local references and wrappers without descending into child locations."""
    if not isinstance(node, dict):
        return
    yield node
    reference = node.get("$ref", "")
    if reference.startswith("#/") and reference not in seen:
        target = document
        for part in reference[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {})
        yield from _validation_schema_nodes(target, document, seen | {reference})
    for wrapper in ("anyOf", "oneOf", "allOf"):
        for child in node.get(wrapper, []):
            yield from _validation_schema_nodes(child, document, seen)


def _safe_validation_location(request: Request, error: dict[str, Any]) -> list[str | int]:
    """Trust a property only at its position in the matched route's request schema."""
    document = app.openapi()
    route = request.scope.get("route")
    operation = (
        document.get("paths", {}).get(route.path_format, {}).get(request.method.lower(), {})
        if isinstance(route, APIRoute)
        else {}
    )
    locations = {
        source: {
            "properties": {
                parameter["name"]: parameter.get("schema", {})
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == source
            }
        }
        for source in ("path", "query", "header", "cookie")
    }
    content = operation.get("requestBody", {}).get("content", {})
    media_type = request.headers.get("content-type", "application/json").split(";", 1)[0]
    locations["body"] = content.get(media_type, content.get("application/json", {})).get(
        "schema", {}
    )
    nodes: list[dict[str, Any]] = []
    result: list[str | int] = []
    for index, item in enumerate(error["loc"]):
        children: list[object] = []
        declared = False
        dynamic = False
        if index == 0 and item in locations:
            declared = True
            children.append(locations[item])
        else:
            for node in nodes:
                if isinstance(item, int) and "items" in node:
                    children.append(node["items"])
                elif item in node.get("properties", {}):
                    declared = True
                    children.append(node["properties"][item])
                elif node.get("additionalProperties", False) is not False:
                    dynamic = True
                    children.append(node["additionalProperties"])
        extra = error["type"] == "extra_forbidden" and index == len(error["loc"]) - 1
        result.append(
            item if isinstance(item, int) or (declared and not dynamic and not extra) else "field"
        )
        nodes = [
            variant for child in children for variant in _validation_schema_nodes(child, document)
        ]
    return result


@app.exception_handler(RequestValidationError)
async def private_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Validation diagnostics describe the failure, never echo the submitted body."""
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "type": error["type"],
                    "loc": _safe_validation_location(request, error),
                    "msg": error["msg"],
                }
                for error in exc.errors()
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


app.include_router(actions.router)
app.include_router(auth.router)
app.include_router(fx.router)
app.include_router(household.router)
app.include_router(currencies.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(classification_decisions.router)
app.include_router(classification_review.router)
app.include_router(layouts.router)
app.include_router(merchant_knowledge.router)
app.include_router(rules.router)
app.include_router(transfers.router)
app.include_router(dashboard.router)
app.include_router(card_cycles.router)
app.include_router(coverage.router)
app.include_router(wealth_history.router)
app.include_router(description_reparse.router)
app.include_router(documents.router)
app.include_router(identity_backfill.router)
app.include_router(investment_catalogue.router)
app.include_router(investment_events.router)
app.include_router(investment_funding.router)
app.include_router(investment_imports.router)
app.include_router(investment_prices.router)
app.include_router(investment_reviews.router)
app.include_router(payslips.router)
app.include_router(plans.router)
app.include_router(goals.router)
app.include_router(emergency_reserve.router)
app.include_router(register.router)
app.include_router(search.router)
app.include_router(monthly_review.router)
app.include_router(commitment_resolutions.router)
app.include_router(portfolio.router)
app.include_router(spending.router)
app.include_router(budgets.router)
app.include_router(recurring.router)
app.include_router(salary.router)
app.include_router(valuations.router)
app.include_router(ingest.router)
app.include_router(doctor.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Public health route; login/callback are public, while financial routes require the owner."""
    return {"status": "ok"}


@app.get("/whoami")
def whoami(owner: Annotated[OwnerSession, Depends(require_owner)]) -> dict[str, str]:
    return {"household_id": str(owner.household_id)}
