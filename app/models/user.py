import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List
from sqlalchemy import String, Boolean, ForeignKey, DateTime, func, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.marketplace import Listing
    from app.models.chat import Conversation, Message
    from app.models.campus import Campus

class User(Base):
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), unique=True, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    campus_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campus.id", ondelete="SET NULL"), 
        nullable=True, 
        index=True
    )
    
    # Account type & Store business details
    account_type: Mapped[str] = mapped_column(String(50), default="individual", nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_services: Mapped[str | None] = mapped_column(Text, nullable=True)  # Services rendered (e.g. Lash Tech, Nail Tech, Makeup)
    business_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    business_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_whatsapp: Mapped[str | None] = mapped_column(String(50), nullable=True)
    business_instagram: Mapped[str | None] = mapped_column(String(100), nullable=True)
    
    # Store Owner KYC Verification
    kyc_status: Mapped[str] = mapped_column(String(50), default="unverified", nullable=False)  # 'unverified', 'pending', 'verified'
    kyc_user_type: Mapped[str] = mapped_column(String(50), default="student", nullable=False)  # 'student' | 'non_student'
    kyc_id_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 'student_id', 'nin', 'voters_card', 'drivers_license', 'passport'
    kyc_id_number: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Student Matric or Gov ID Number
    kyc_student_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Student Matric / ID Number
    kyc_id_card_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # Photo / Document proof (Student ID or Gov ID)
    kyc_selfie_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # Biometric Face verification selfie photo
    kyc_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kyc_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
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
    campus: Mapped["Campus | None"] = relationship("Campus", lazy="selectin")
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
