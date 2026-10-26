import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func, update
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_active_user
from app.models.user import User
from app.models.marketplace import Listing
from app.models.chat import Conversation, Message
from app.schemas.chat import (
    MessageRead,
    MessageCreate,
    PaginatedMessagesResponse,
    ConversationCreate,
    ConversationRead,
    ParticipantRead
)
from app.core.limiter import limiter

router = APIRouter()

def format_message_read(msg: Message) -> MessageRead:
    sender_name = None
    sender_email = None
    if msg.sender:
        sender_name = msg.sender.name or msg.sender.email.split("@")[0]
        sender_email = msg.sender.email

    return MessageRead(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        text=msg.text,
        is_read=msg.is_read,
        timestamp=msg.timestamp,
        sender_name=sender_name,
        sender_email=sender_email
    )

def format_conversation_read(conv: Conversation, current_user_id: uuid.UUID, unread_count: int = 0) -> ConversationRead:
    other_user = conv.seller if conv.buyer_id == current_user_id else conv.buyer
    participant = None
    if other_user:
        handle = None
        avatar = None
        if other_user.profile:
            handle = other_user.profile.burner_handle
            avatar = other_user.profile.avatar_slug
        participant = ParticipantRead(
            id=other_user.id,
            email=other_user.email,
            name=other_user.name,
            burner_handle=handle,
            avatar_slug=avatar
        )

    last_msg = None
    last_time = conv.updated_at or conv.created_at
    if conv.messages:
        sorted_msgs = sorted(conv.messages, key=lambda m: m.timestamp)
        last_msg = sorted_msgs[-1].text
        last_time = sorted_msgs[-1].timestamp

    listing_title = None
    if conv.listing:
        listing_title = conv.listing.title
    elif getattr(conv, "item_request", None) and conv.item_request:
        listing_title = f"Wanted: {conv.item_request.title}"

    return ConversationRead(
        id=conv.id,
        listing_id=conv.listing_id,
        request_id=getattr(conv, "request_id", None),
        buyer_id=conv.buyer_id,
        seller_id=conv.seller_id,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        listing_title=listing_title,
        other_user=participant,
        last_message=last_msg,
        last_message_time=last_time,
        unread_count=unread_count
    )


@router.get("/{conversation_id}/messages", response_model=PaginatedMessagesResponse)
@limiter.limit("60/minute")
async def get_conversation_messages(
    request: Request,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    limit: int = Query(50, ge=1, le=100, description="Page limit"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    before: Optional[datetime] = Query(None, description="Cursor timestamp for pagination")
) -> Any:
    """
    Retrieve historical messages for a 1-on-1 conversation with pagination.
    Verifies caller is an active participant in the conversation.
    Marks retrieved unread messages as read.
    """
    # 1. Row-Level Security: Verify conversation exists and user is participant
    conv_res = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_res.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user.id not in (conversation.buyer_id, conversation.seller_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Forbidden: Not authorized to access messages in this conversation"
        )

    # 2. Count total messages
    count_stmt = select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    if before:
        count_stmt = count_stmt.where(Message.timestamp < before)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    # 3. Query paginated messages
    query = (
        select(Message)
        .options(selectinload(Message.sender))
        .where(Message.conversation_id == conversation_id)
    )
    if before:
        query = query.where(Message.timestamp < before)

    query = query.order_by(Message.timestamp.asc()).offset(offset).limit(limit)
    result = await db.execute(query)
    messages = result.scalars().all()

    # 4. Mark messages sent by the counterparty as read
    await db.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_id != current_user.id,
            Message.is_read == False
        )
        .values(is_read=True)
    )
    await db.commit()

    items = [format_message_read(m) for m in messages]
    has_more = (offset + limit) < total

    return PaginatedMessagesResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
        conversation_id=conversation_id
    )


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def create_or_get_conversation(
    request: Request,
    conv_in: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Start or retrieve an existing 1-on-1 conversation for a marketplace listing.
    """
    # Support item_request (Wanted board) direct chat
    if conv_in.request_id:
        from app.models.campus import ItemRequest
        req_res = await db.execute(
            select(ItemRequest)
            .options(selectinload(ItemRequest.user).selectinload(User.profile))
            .where(ItemRequest.id == conv_in.request_id)
        )
        item_req = req_res.scalar_one_or_none()
        if not item_req:
            raise HTTPException(status_code=404, detail="Item request not found")

        if item_req.user_id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Cannot start a conversation with yourself for your own request"
            )

        res = await db.execute(
            select(Conversation)
            .options(
                selectinload(Conversation.listing),
            selectinload(Conversation.item_request),
                selectinload(Conversation.item_request),
                selectinload(Conversation.buyer).selectinload(User.profile),
                selectinload(Conversation.seller).selectinload(User.profile),
                selectinload(Conversation.messages)
            )
            .where(
                Conversation.request_id == conv_in.request_id,
                Conversation.seller_id == current_user.id
            )
        )
        existing_conv = res.scalar_one_or_none()
        if existing_conv:
            return format_conversation_read(existing_conv, current_user.id)

        new_conv = Conversation(
            request_id=item_req.id,
            listing_id=None,
            buyer_id=item_req.user_id,
            seller_id=current_user.id
        )
        db.add(new_conv)
        await db.commit()
        await db.refresh(new_conv)

        if conv_in.initial_message and conv_in.initial_message.strip():
            first_msg = Message(
                conversation_id=new_conv.id,
                sender_id=current_user.id,
                text=conv_in.initial_message.strip(),
                is_read=False
            )
            db.add(first_msg)
            await db.commit()

        res_loaded = await db.execute(
            select(Conversation)
            .options(
                selectinload(Conversation.listing),
            selectinload(Conversation.item_request),
                selectinload(Conversation.item_request),
                selectinload(Conversation.buyer).selectinload(User.profile),
                selectinload(Conversation.seller).selectinload(User.profile),
                selectinload(Conversation.messages)
            )
            .where(Conversation.id == new_conv.id)
        )
        fresh_conv = res_loaded.scalar_one()
        return format_conversation_read(fresh_conv, current_user.id)

    if not conv_in.listing_id:
        raise HTTPException(status_code=400, detail="Either listing_id or request_id is required")

    # Verify listing exists
    list_res = await db.execute(
        select(Listing)
        .options(selectinload(Listing.seller).selectinload(User.profile))
        .where(Listing.id == conv_in.listing_id)
    )
    listing = list_res.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing.seller_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Cannot start a conversation with yourself for your own listing"
        )

    # Check if conversation already exists between this buyer and listing
    res = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.listing),
            selectinload(Conversation.item_request),
            selectinload(Conversation.buyer).selectinload(User.profile),
            selectinload(Conversation.seller).selectinload(User.profile),
            selectinload(Conversation.messages)
        )
        .where(
            Conversation.listing_id == conv_in.listing_id,
            Conversation.buyer_id == current_user.id
        )
    )
    existing_conv = res.scalar_one_or_none()
    if existing_conv:
        return format_conversation_read(existing_conv, current_user.id)

    # Create new conversation
    new_conv = Conversation(
        listing_id=listing.id,
        buyer_id=current_user.id,
        seller_id=listing.seller_id
    )
    db.add(new_conv)
    await db.commit()
    await db.refresh(new_conv)

    # Optional initial message
    if conv_in.initial_message and conv_in.initial_message.strip():
        first_msg = Message(
            conversation_id=new_conv.id,
            sender_id=current_user.id,
            text=conv_in.initial_message.strip(),
            is_read=False
        )
        db.add(first_msg)
        await db.commit()

    # Reload relationships
    res_loaded = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.listing),
            selectinload(Conversation.item_request),
            selectinload(Conversation.buyer).selectinload(User.profile),
            selectinload(Conversation.seller).selectinload(User.profile),
            selectinload(Conversation.messages)
        )
        .where(Conversation.id == new_conv.id)
    )
    fresh_conv = res_loaded.scalar_one()
    return format_conversation_read(fresh_conv, current_user.id)


@router.get("", response_model=List[ConversationRead])
@router.get("/", response_model=List[ConversationRead])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    List all active 1-on-1 conversations for the current user (as buyer or seller).
    Strict Row-Level Security: Only returns conversations involving current_user.
    """
    result = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.listing),
            selectinload(Conversation.item_request),
            selectinload(Conversation.buyer).selectinload(User.profile),
            selectinload(Conversation.seller).selectinload(User.profile),
            selectinload(Conversation.messages)
        )
        .where(
            or_(
                Conversation.buyer_id == current_user.id,
                Conversation.seller_id == current_user.id
            )
        )
        .order_by(Conversation.updated_at.desc())
    )
    conversations = result.scalars().all()
    
    output = []
    for conv in conversations:
        # Count unread messages for current user
        unread = sum(1 for m in conv.messages if m.sender_id != current_user.id and not m.is_read)
        output.append(format_conversation_read(conv, current_user.id, unread_count=unread))
    return output


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Get conversation details by ID.
    Strict Row-Level Security: Non-participants receive 403 Forbidden.
    """
    result = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.listing),
            selectinload(Conversation.item_request),
            selectinload(Conversation.buyer).selectinload(User.profile),
            selectinload(Conversation.seller).selectinload(User.profile),
            selectinload(Conversation.messages)
        )
        .where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if current_user.id not in (conv.buyer_id, conv.seller_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: You are not a participant in this conversation")

    unread = sum(1 for m in conv.messages if m.sender_id != current_user.id and not m.is_read)
    return format_conversation_read(conv, current_user.id, unread_count=unread)
