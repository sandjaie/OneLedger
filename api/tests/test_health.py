# api/tests/test_health.py
"""The authorization boundary, as HTTP sees it.

`test_auth.py` covers `resolve_owner` as a function. These cover the same rules through the
app, because a dependency that is correct in isolation and unwired in `main.py` fails exactly
the same way as one that is wrong — and only this test can tell the difference.
"""

import pytest
from fastapi.testclient import TestClient

from oneledger.main import app

client = TestClient(app)


def test_health_needs_no_session() -> None:
    """The one route that is deliberately open. There is no second."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_no_session_is_401_not_403() -> None:
    """Absent credentials and rejected credentials are different answers.

    A service that returns 403 for both tells a caller their account is not permitted when in
    fact they never presented one, which sends them to the wrong fix.
    """
    assert client.get("/whoami").status_code == 401


def test_a_forged_session_is_403() -> None:
    """Google authenticates any Google account. Authentication is not authorization."""
    forged = TestClient(app)
    forged.cookies.set("session", "not-a-real-token")
    assert forged.get("/whoami").status_code == 403


@pytest.mark.parametrize("route", ["/whoami"])
def test_every_route_but_health_requires_the_owner(route: str) -> None:
    """A route added without `Depends(require_owner)` is the failure this catches.

    Extend the parameter list as routes land; a route that answers anonymously will fail here
    rather than in production.
    """
    assert client.get(route).status_code in (401, 403)
