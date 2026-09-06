"""The authorization boundary, as HTTP sees it."""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from oneledger.auth import require_owner
from oneledger.main import app

client = TestClient(app)
PUBLIC_ROUTES = {("/health", "GET"), ("/auth/login", "GET"), ("/auth/callback", "GET")}


def current_api_routes() -> list[APIRoute]:
    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
        elif hasattr(route, "original_router"):
            routes.extend(r for r in route.original_router.routes if isinstance(r, APIRoute))
    return routes


def test_health_needs_no_session() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_no_session_is_401_not_403() -> None:
    assert client.get("/whoami").status_code == 401


def test_a_forged_session_is_403() -> None:
    forged = TestClient(app)
    forged.cookies.set("session", "not-a-real-token")
    assert forged.get("/whoami").status_code == 403


@pytest.mark.parametrize("route", ["/whoami", "/household"])
def test_protected_gets_require_the_owner(route: str) -> None:
    assert client.get(route).status_code in (401, 403)


def test_every_current_non_bootstrap_route_depends_on_the_owner() -> None:
    routes = current_api_routes()
    assert {route.path for route in routes} >= {"/health", "/auth/logout", "/household"}
    for route in routes:
        methods = set(route.methods or ())
        public_methods = {method for method in methods if (route.path, method) in PUBLIC_ROUTES}
        protected_methods = methods - public_methods - {"HEAD", "OPTIONS"}
        if protected_methods:
            assert any(d.call is require_owner for d in route.dependant.dependencies), route.path
