import uuid
import enum
from sqlalchemy import BigInteger, ForeignKey, String, Integer, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base_class import Base

class TransactionStatus(enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"

class Wallet(Base):
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), unique=True)
    balance_kobo: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

class WalletLedger(Base):
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallet.id", ondelete="CASCADE"))
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

class EscrowTransaction(Base):
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listing.id", ondelete="SET NULL"), nullable=True)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    
    # Security
    hashed_pin: Mapped[str] = mapped_column(String, nullable=False)
    pin_attempts: Mapped[int] = mapped_column(Integer, default=0)
    
    status: Mapped[TransactionStatus] = mapped_column(Enum(TransactionStatus), default=TransactionStatus.PENDING)
