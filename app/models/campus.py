import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import String, BigInteger, ForeignKey, Text, Boolean, DateTime, func, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.marketplace import Listing

class Campus(Base):
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    acronym: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    popular_hostels: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    listings: Mapped[List["Listing"]] = relationship("Listing", back_populates="campus")
    requests: Mapped[List["ItemRequest"]] = relationship("ItemRequest", back_populates="campus")

class ItemRequest(Base):
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), 
        index=True, 
        nullable=False
    )
    campus_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("campus.id", ondelete="SET NULL"), 
        index=True, 
        nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    budget_kobo: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_fulfilled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    campus: Mapped[Optional["Campus"]] = relationship("Campus", back_populates="requests", lazy="selectin")
