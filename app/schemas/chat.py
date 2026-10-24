import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

class MessageCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="Message text content")

class MessageRead(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    text: str
    is_read: bool = False
    timestamp: datetime
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class PaginatedMessagesResponse(BaseModel):
    items: List[MessageRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    conversation_id: uuid.UUID

class ConversationCreate(BaseModel):
    listing_id: uuid.UUID
    initial_message: Optional[str] = None

class ParticipantRead(BaseModel):
    id: uuid.UUID
    name: Optional[str] = None
    email: str
    burner_handle: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ConversationRead(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    buyer_id: uuid.UUID
    seller_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    listing_title: Optional[str] = None
    other_user: Optional[ParticipantRead] = None
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: int = 0

    model_config = ConfigDict(from_attributes=True)

# Backward compatibility schemas
class ChatMessageRead(BaseModel):
    id: uuid.UUID
    sender_id: uuid.UUID
    receiver_id: uuid.UUID
    listing_id: uuid.UUID
    content: str
    sender_email: Optional[str] = None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)

class InboxThread(BaseModel):
    listing_id: uuid.UUID
    listing_title: str
    other_user_id: uuid.UUID
    other_user_email: str
    last_message: str
    last_message_time: datetime
