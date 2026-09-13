"""Owner-only investment event and append-only correction routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..auth import OwnerSession, require_owner
from ..investments.cash_lifecycle import (
    correct_cash_lifecycle_event,
    create_cash_lifecycle_event,
)
from ..investments.commands import (
    InvestmentCommandError,
    correct_acquisition,
    create_acquisition,
)
from ..investments.schemas import (
    AcquisitionCorrectionCreate,
    AcquisitionCreate,
    CashLifecycleCorrectionCreate,
    CashLifecycleCreate,
    CashLifecycleEventRow,
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


@router.post(
    "/cash-lifecycle",
    response_model=CashLifecycleEventRow,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": CashLifecycleEventRow, "description": "Identical retry"}},
)
def append_cash_lifecycle_event(
    body: CashLifecycleCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> CashLifecycleEventRow:
    try:
        result = create_cash_lifecycle_event(owner, body)
    except InvestmentCommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result.event


@router.post(
    "/{event_id}/cash-lifecycle-corrections",
    response_model=CashLifecycleEventRow,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": CashLifecycleEventRow, "description": "Identical retry"}},
)
def append_cash_lifecycle_correction(
    event_id: UUID,
    body: CashLifecycleCorrectionCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> CashLifecycleEventRow:
    try:
        result = correct_cash_lifecycle_event(owner, event_id, body)
    except InvestmentCommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result.event
