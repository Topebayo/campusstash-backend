import uuid
from pydantic import BaseModel, Field
from app.models.financial import TransactionStatus

class EscrowTransactionCreate(BaseModel):
    listing_id: uuid.UUID
    seller_id: uuid.UUID
    amount_kobo: int = Field(..., gt=0)

class EscrowTransactionRead(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID | None
    buyer_id: uuid.UUID | None
    seller_id: uuid.UUID | None
    amount_kobo: int
    status: TransactionStatus
    pin_attempts: int
    plain_pin: str | None = None
    
    class Config:
        from_attributes = True

class HandoverRequest(BaseModel):
    pin: str = Field(..., min_length=6, max_length=6)
