import uuid
import json
from datetime import datetime, timezone
from typing import List, Optional, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func, update
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_active_user
from app.db.session import AsyncSessionLocal
from app.services.chat_manager import manager
from app.core import security
from app.core.config import settings
from app.models.user import User
from app.models.chat import Conversation, Message, MaskedChatMessage
from app.schemas.chat import ChatMessageRead, InboxThread, MessageRead

router = APIRouter()

async def authenticate_websocket(websocket: WebSocket) -> Optional[User]:
    """
    Extract and validate JWT token from query parameters, Authorization header,
    or Sec-WebSocket-Protocol header.
    """
    token: Optional[str] = None
    
    # 1. Check query parameter (?token=... or ?access_token=...)
    token = websocket.query_params.get("token") or websocket.query_params.get("access_token")

    # 2. Check Authorization header
    if not token:
        auth_header = websocket.headers.get("Authorization") or websocket.headers.get("authorization")
        if auth_header:
            parts = auth_header.strip().split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]

    # 3. Check Sec-WebSocket-Protocol
    if not token and "sec-websocket-protocol" in websocket.headers:
        proto = websocket.headers["sec-websocket-protocol"]
        for p in [x.strip() for x in proto.split(",")]:
            if len(p) > 20:
                token = p
                break

    if not token:
        return None

    try:
        payload = security.decode_token(token)
        user_id_str = payload.get("sub")
        token_type = payload.get("type", "access")
        if not user_id_str or token_type != "access":
            return None
        user_id = uuid.UUID(user_id_str)
    except Exception:
        return None

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.id == user_id)
        )
        user = res.scalar_one_or_none()
        if user and user.is_active:
            return user
    return None


@router.websocket("/ws/chat/{conversation_id}")
@router.websocket("/ws/{conversation_id}")
async def chat_websocket_endpoint(
    websocket: WebSocket,
    conversation_id: str
):
    """
    Real-time 1-on-1 chat WebSocket endpoint.
    - Validates JWT from query param or Authorization header.
    - Ensures user is an authorized participant (buyer or seller).
    - Maps user ID to active WebSocket in ConnectionManager.
    - Enforces message size limits to mitigate DoS / buffer overflow.
    - Persists incoming messages to PostgreSQL with authenticated sender ID.
    - Publishes to Redis channel chat:{conversation_id}.
    - Delivers to recipient in real-time.
    """
    # 1. Authenticate user from query param or header
    user = await authenticate_websocket(websocket)
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized: invalid or missing token")
        return

    # 2. Validate conversation ID
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid conversation ID")
        return

    # 3. Verify conversation existence & 1-on-1 participant authorization (Strict Row-Level Isolation)
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Conversation).where(Conversation.id == conv_uuid)
        )
        conversation = res.scalar_one_or_none()
        if not conversation:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Conversation not found")
            return

        if user.id not in (conversation.buyer_id, conversation.seller_id):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Forbidden: not a participant")
            return

        recipient_id = conversation.seller_id if user.id == conversation.buyer_id else conversation.buyer_id

    # 4. Connect to ConnectionManager
    await manager.connect(websocket, conv_uuid, user.id)

    try:
        while True:
            raw_text = await websocket.receive_text()
            if not raw_text or not raw_text.strip():
                continue

            try:
                data = json.loads(raw_text)
            except Exception:
                data = {"text": raw_text}

            text = data.get("text") or data.get("content") or data.get("message")
            if not text or not str(text).strip():
                continue

            cleaned_text = str(text).strip()
            # Enforce max length of 3000 chars per message to prevent memory exhaustion
            if len(cleaned_text) > 3000:
                cleaned_text = cleaned_text[:3000]

            # 5. Persist to PostgreSQL messages table with verified user.id as sender
            async with AsyncSessionLocal() as session:
                msg = Message(
                    conversation_id=conv_uuid,
                    sender_id=user.id,
                    text=cleaned_text,
                    is_read=False,
                    timestamp=datetime.now(timezone.utc)
                )
                session.add(msg)
                # Touch conversation timestamp
                await session.execute(
                    update(Conversation).where(Conversation.id == conv_uuid).values(updated_at=func.now())
                )
                await session.commit()
                await session.refresh(msg)

                msg_payload = {
                    "id": str(msg.id),
                    "conversation_id": str(conv_uuid),
                    "sender_id": str(user.id),
                    "sender_name": user.name or user.email.split("@")[0],
                    "sender_email": user.email,
                    "text": msg.text,
                    "is_read": msg.is_read,
                    "timestamp": msg.timestamp.isoformat()
                }

            # 6. Publish payload to Redis channel chat:{conversation_id}
            await manager.publish_to_redis(conv_uuid, msg_payload)

            # 7. Deliver directly to recipient if online outside this room
            if manager.is_user_online(recipient_id) and recipient_id not in manager.get_online_users_in_conversation(conv_uuid):
                await manager.send_personal_message(recipient_id, msg_payload)

    except WebSocketDisconnect:
        manager.disconnect(websocket, conv_uuid, user.id)
    except Exception as e:
        print(f"[WebSocket Exception] conv={conv_uuid} user={user.id}: {e}")
        manager.disconnect(websocket, conv_uuid, user.id)


# Legacy inbox and history routes for backward compatibility
@router.get('/inbox/me', response_model=List[InboxThread])
async def get_inbox(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    from app.models.marketplace import Listing
    # Fetch from Conversation model with strict participant filtering
    res = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.listing),
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
    convs = res.scalars().all()
    threads = []
    for c in convs:
        other = c.seller if c.buyer_id == current_user.id else c.buyer
        last_msg = ""
        last_time = c.updated_at or c.created_at
        if c.messages:
            sorted_msgs = sorted(c.messages, key=lambda m: m.timestamp)
            last_msg = sorted_msgs[-1].text
            last_time = sorted_msgs[-1].timestamp
            
        threads.append(InboxThread(
            listing_id=c.listing_id,
            listing_title=c.listing.title if c.listing else "Campus Item",
            other_user_id=other.id if other else current_user.id,
            other_user_email=other.email if other else "peer",
            last_message=last_msg,
            last_message_time=last_time
        ))
    return threads
