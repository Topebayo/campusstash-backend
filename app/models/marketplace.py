import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List
from sqlalchemy import String, BigInteger, ForeignKey, Text, Boolean, DateTime, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.chat import Conversation

class Listing(Base):
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), 
        index=True, 
        nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="Other", index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", index=True, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Timestamps
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

    # Relationships with cascading deletes
    seller: Mapped["User"] = relationship("User", back_populates="listings", lazy="selectin")
    images: Mapped[List["ListingImage"]] = relationship(
        "ListingImage", 
        back_populates="listing", 
        cascade="all, delete-orphan", 
        passive_deletes=True, 
        lazy="selectin"
    )
    conversations: Mapped[List["Conversation"]] = relationship(
        "Conversation", 
        back_populates="listing", 
        cascade="all, delete-orphan", 
        passive_deletes=True
    )

    # Backward compatibility properties for existing APIs
    @property
    def price_kobo(self) -> int:
        return self.price

    @price_kobo.setter
    def price_kobo(self, value: int) -> None:
        self.price = value

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self.status = "active" if value else "sold"


class ListingImage(Base):
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="CASCADE"), 
        index=True, 
        nullable=False
    )
    image_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    listing: Mapped["Listing"] = relationship("Listing", back_populates="images")


class SafeDropZone(Base):
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location_details: Mapped[str | None] = mapped_column(Text)
