"""Owner-only acquisition and append-only correction routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..auth import OwnerSession, require_owner
from ..investments.commands import (
    InvestmentCommandError,
    correct_acquisition,
    create_acquisition,
)
from ..investments.schemas import (
    AcquisitionCorrectionCreate,
    AcquisitionCreate,
    InvestmentEventRow,
)

router = APIRouter(prefix="/investment-events", tags=["investment events"])


@router.post(
    "/acquisitions",
    response_model=InvestmentEventRow,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": InvestmentEventRow, "description": "Identical retry"}},
)
def append_acquisition(
    body: AcquisitionCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> InvestmentEventRow:
    try:
        result = create_acquisition(owner, body)
    except InvestmentCommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result.event


@router.post(
    "/{event_id}/corrections",
    response_model=InvestmentEventRow,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": InvestmentEventRow, "description": "Identical retry"}},
)
def append_acquisition_correction(
    event_id: UUID,
    body: AcquisitionCorrectionCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> InvestmentEventRow:
    try:
        result = correct_acquisition(owner, event_id, body)
    except InvestmentCommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result.event
