import os
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from .. import auth
from ..auth import OwnerSession

router = APIRouter(prefix="/auth", tags=["auth"])


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
