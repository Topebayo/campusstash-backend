import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_active_user
from app.models.user import User
from app.schemas.financial import EscrowTransactionCreate, EscrowTransactionRead, HandoverRequest
from app.services.escrow_service import create_escrow, confirm_handover

router = APIRouter()

@router.post("/", response_model=EscrowTransactionRead)
async def initialize_escrow(
    *,
    db: AsyncSession = Depends(get_db),
    escrow_in: EscrowTransactionCreate,
    current_user: User = Depends(get_current_active_user)
) -> Any:
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
        "plain_pin": plain_pin 
    }
    return escrow_dict

@router.post("/{escrow_id}/settle", response_model=EscrowTransactionRead)
async def settle_escrow(
    *,
    db: AsyncSession = Depends(get_db),
    escrow_id: uuid.UUID,
    handover_in: HandoverRequest,
    current_user: User = Depends(get_current_active_user)
) -> Any:
    try:
        escrow = await confirm_handover(db=db, escrow_id=escrow_id, submitted_pin=handover_in.pin)
        return escrow
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
