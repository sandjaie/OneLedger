from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import OwnerSession, require_owner
from ..investments.funding import (
    FundingConfirmationRequest,
    FundingConfirmationResponse,
    FundingError,
    FundingProposalRequest,
    FundingProposalResponse,
    FundingUnlinkResponse,
    confirm_funding,
    propose_funding,
    unlink_funding,
)

router = APIRouter(prefix="/investment-funding", tags=["investment funding"])


@router.post("/proposals", response_model=FundingProposalResponse)
def propose_purchase_funding(
    body: FundingProposalRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> FundingProposalResponse:
    try:
        return propose_funding(owner, body)
    except FundingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post(
    "/confirmations",
    response_model=FundingConfirmationResponse,
    status_code=status.HTTP_201_CREATED,
)
def confirm_purchase_funding(
    body: FundingConfirmationRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> FundingConfirmationResponse:
    try:
        return confirm_funding(owner, body)
    except FundingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.delete("/cash-transactions/{cash_transaction_id}", response_model=FundingUnlinkResponse)
def unlink_purchase_funding(
    cash_transaction_id: UUID,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> FundingUnlinkResponse:
    try:
        return unlink_funding(owner, cash_transaction_id)
    except FundingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
