import uuid
import secrets
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.financial import EscrowTransaction, TransactionStatus
from app.models.user import User

async def create_escrow(db: AsyncSession, listing_id: uuid.UUID, buyer_id: uuid.UUID, seller_id: uuid.UUID, amount_kobo: int) -> tuple[EscrowTransaction, str]:
    # Generate 6 digit pin
    pin = ''.join(secrets.choice('0123456789') for _ in range(6))
    
    # Hash using Postgres pgcrypto via raw SQL
    result = await db.execute(
        text("SELECT crypt(:pin, gen_salt('bf'))"),
        {"pin": pin}
    )
    hashed_pin = result.scalar()
    
    escrow = EscrowTransaction(
        listing_id=listing_id,
        buyer_id=buyer_id,
        seller_id=seller_id,
        amount_kobo=amount_kobo,
        hashed_pin=hashed_pin,
        status=TransactionStatus.PENDING
    )
    db.add(escrow)
    await db.commit()
    await db.refresh(escrow)
    
    return escrow, pin

async def confirm_handover(db: AsyncSession, escrow_id: uuid.UUID, submitted_pin: str) -> EscrowTransaction:
    result = await db.execute(select(EscrowTransaction).where(EscrowTransaction.id == escrow_id))
    escrow = result.scalar_one_or_none()
    
    if not escrow:
        raise ValueError("Escrow not found")
        
    if escrow.status != TransactionStatus.PENDING:
        raise ValueError(f"Escrow is not in PENDING state. Current state: {escrow.status.value}")
        
    if escrow.pin_attempts >= 3:
        raise ValueError("Maximum PIN attempts reached")
        
    # Verify PIN via Postgres
    result = await db.execute(
        text("SELECT crypt(:pin, :hashed_pin) = :hashed_pin"),
        {"pin": submitted_pin, "hashed_pin": escrow.hashed_pin}
    )
    is_valid = result.scalar()
    
    if is_valid:
        escrow.status = TransactionStatus.COMPLETED
        await db.commit()
        await db.refresh(escrow)
        # TODO: Move funds from escrow to seller wallet ledger
    else:
        escrow.pin_attempts += 1
        if escrow.pin_attempts >= 3:
            escrow.status = TransactionStatus.DISPUTED
        await db.commit()
        await db.refresh(escrow)
        raise ValueError("Invalid PIN")
        
    return escrow
