"""The authorization boundary, as HTTP sees it."""

import pytest
from fastapi.testclient import TestClient

from oneledger.main import app

client = TestClient(app)


def test_health_needs_no_session() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_no_session_is_401_not_403() -> None:
    assert client.get("/whoami").status_code == 401


def test_a_forged_session_is_403() -> None:
    forged = TestClient(app)
    forged.cookies.set("session", "not-a-real-token")
    assert forged.get("/whoami").status_code == 403


@pytest.mark.parametrize("route", ["/whoami"])
def test_whoami_boundary_smoke_requires_the_owner(route: str) -> None:
    assert client.get(route).status_code in (401, 403)
