import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List
from sqlalchemy import String, Boolean, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.marketplace import Listing
    from app.models.chat import Conversation, Message

class User(Base):
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), unique=True, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
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

    # Relationships with cascading deletes and passive_deletes for PostgreSQL FK efficiency
    profile: Mapped["PublicProfile"] = relationship(
        "PublicProfile", 
        back_populates="user", 
        uselist=False, 
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    listings: Mapped[List["Listing"]] = relationship(
        "Listing", 
        back_populates="seller", 
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    buyer_conversations: Mapped[List["Conversation"]] = relationship(
        "Conversation", 
        foreign_keys="[Conversation.buyer_id]", 
        back_populates="buyer", 
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    seller_conversations: Mapped[List["Conversation"]] = relationship(
        "Conversation", 
        foreign_keys="[Conversation.seller_id]", 
        back_populates="seller", 
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    sent_messages: Mapped[List["Message"]] = relationship(
        "Message", 
        foreign_keys="[Message.sender_id]", 
        back_populates="sender", 
        cascade="all, delete-orphan",
        passive_deletes=True
    )

class PublicProfile(Base):
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), 
        unique=True, 
        index=True,
        nullable=False
    )
    burner_handle: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    avatar_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="profile", lazy="selectin")
