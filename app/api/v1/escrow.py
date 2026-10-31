import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db, get_current_active_user
from app.models.user import User
from app.models.financial import EscrowTransaction
from app.schemas.financial import EscrowTransactionCreate, EscrowTransactionRead, HandoverRequest
from app.services.escrow_service import create_escrow, confirm_handover
from app.core.limiter import limiter

router = APIRouter()

@router.post("/", response_model=EscrowTransactionRead)
@limiter.limit("10/minute")
async def initialize_escrow(
    request: Request,
    escrow_in: EscrowTransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Initialize a secure escrow deposit.
    Enforces user isolation: buyer cannot escrow with themselves.
    """
    if escrow_in.seller_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initiate an escrow transaction with yourself."
        )

    escrow, plain_pin = await create_escrow(
        db=db,
        listing_id=escrow_in.listing_id,
        buyer_id=current_user.id,
        seller_id=escrow_in.seller_id,
        amount_kobo=escrow_in.amount_kobo
    )
    escrow_dict = {
        "id": escrow.id,
        "listing_id": escrow.listing_id,
        "buyer_id": escrow.buyer_id,
        "seller_id": escrow.seller_id,
        "amount_kobo": escrow.amount_kobo,
        "status": escrow.status,
        "pin_attempts": escrow.pin_attempts,
        "plain_pin": plain_pin  # Only the buyer who created the escrow receives the handover PIN
    }
    return escrow_dict

@router.get("/{escrow_id}", response_model=EscrowTransactionRead)
async def get_escrow(
    escrow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Fetch an escrow transaction by ID.
    Row-level security: only buyer and seller can view their own transaction.
    """
    result = await db.execute(select(EscrowTransaction).where(EscrowTransaction.id == escrow_id))
    escrow = result.scalar_one_or_none()
    if not escrow:
        raise HTTPException(status_code=404, detail="Escrow transaction not found")

    if current_user.id not in (escrow.buyer_id, escrow.seller_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this transaction."
        )

    return {
        "id": escrow.id,
        "listing_id": escrow.listing_id,
        "buyer_id": escrow.buyer_id,
        "seller_id": escrow.seller_id,
        "amount_kobo": escrow.amount_kobo,
        "status": escrow.status,
        "pin_attempts": escrow.pin_attempts,
        "plain_pin": None  # Never expose hashed PIN to query endpoints
    }

@router.post("/{escrow_id}/settle", response_model=EscrowTransactionRead)
@limiter.limit("5/minute")
async def settle_escrow(
    request: Request,
    escrow_id: uuid.UUID,
    handover_in: HandoverRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Settle escrow upon product inspection and handover.
    Strict Row-Level Security: ONLY the designated seller can submit the PIN and settle funds.
    Rate limited to 5 attempts per minute to prevent PIN brute-forcing.
    """
    # 1. Row-level ownership check
    result = await db.execute(select(EscrowTransaction).where(EscrowTransaction.id == escrow_id))
    escrow = result.scalar_one_or_none()
    if not escrow:
        raise HTTPException(status_code=404, detail="Escrow transaction not found")

    if current_user.id != escrow.seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only the seller for this transaction can verify the PIN and settle funds."
        )

    try:
        updated_escrow = await confirm_handover(db=db, escrow_id=escrow_id, submitted_pin=handover_in.pin)
        return updated_escrow
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
