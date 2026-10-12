import asyncio
import json
import uuid
from typing import Dict, Set, Any, Optional
from fastapi import WebSocket
from redis import asyncio as aioredis

from app.core.config import settings

class ConnectionManager:
    """
    Manages authenticated WebSocket connections, mapping authenticated user IDs
    to active WebSocket connections, with automatic fallback to in-memory pub/sub
    if Redis is not running locally.
    """
    def __init__(self):
        # Maps user_id -> Set[WebSocket] (supports multiple tabs / devices per user)
        self.user_connections: Dict[uuid.UUID, Set[WebSocket]] = {}
        
        # Maps conversation_id -> Set[user_id]
        self.conversation_users: Dict[uuid.UUID, Set[uuid.UUID]] = {}
        
        # Maps conversation_id -> asyncio.Task running Redis listener
        self.listener_tasks: Dict[uuid.UUID, asyncio.Task] = {}
        
        # None = uninitialized, False = unavailable, Redis instance = connected
        self._redis: Any = None

    async def connect_redis(self) -> Optional[aioredis.Redis]:
        """Ensure connection to Redis server with graceful fallback if unavailable."""
        if self._redis is False:
            return None
            
        if self._redis is None:
            try:
                r = aioredis.from_url(
                    settings.REDIS_URL, 
                    decode_responses=True,
                    max_connections=50,
                    socket_connect_timeout=1.5,
                    socket_timeout=1.5
                )
                await r.ping()
                self._redis = r
                print(f"[WebSocket] Connected to Redis Pub/Sub at {settings.REDIS_URL}")
            except Exception as e:
                print(f"[WebSocket] Redis server at {settings.REDIS_URL} unavailable ({e}). Using in-memory Pub/Sub fallback.")
                self._redis = False
                return None
                
        return self._redis if self._redis is not False else None

    async def connect(self, websocket: WebSocket, conversation_id: uuid.UUID, user_id: uuid.UUID):
        """
        Accept an authenticated WebSocket connection, index it by user_id,
        track room occupancy, and start listener if Redis is available.
        """
        await websocket.accept()

        # Map user_id -> active WebSockets
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(websocket)

        # Track user in conversation room
        if conversation_id not in self.conversation_users:
            self.conversation_users[conversation_id] = set()
        self.conversation_users[conversation_id].add(user_id)

        # Start Redis subscriber task if Redis is available
        redis_client = await self.connect_redis()
        if redis_client:
            if conversation_id not in self.listener_tasks or self.listener_tasks[conversation_id].done():
                self.listener_tasks[conversation_id] = asyncio.create_task(
                    self.listen_to_redis(conversation_id)
                )

    def disconnect(self, websocket: WebSocket, conversation_id: uuid.UUID, user_id: uuid.UUID):
        """
        Clean up disconnected socket from user mapping.
        """
        if user_id in self.user_connections:
            self.user_connections[user_id].discard(websocket)
            if not self.user_connections[user_id]:
                del self.user_connections[user_id]

        user_still_has_sockets = (
            user_id in self.user_connections and 
            len(self.user_connections[user_id]) > 0
        )

        if not user_still_has_sockets and conversation_id in self.conversation_users:
            self.conversation_users[conversation_id].discard(user_id)

        # Clean up empty conversation room
        if conversation_id in self.conversation_users and not self.conversation_users[conversation_id]:
            del self.conversation_users[conversation_id]
            if conversation_id in self.listener_tasks:
                task = self.listener_tasks.pop(conversation_id)
                task.cancel()

    async def publish_to_redis(self, conversation_id: uuid.UUID, message_payload: Dict[str, Any]):
        """
        Publish chat message to Redis channel chat:{conversation_id}
        or directly broadcast to in-memory WebSockets if Redis is offline.
        """
        raw_msg = json.dumps(message_payload)
        redis_client = await self.connect_redis()

        if redis_client:
            try:
                channel = f"chat:{conversation_id}"
                await redis_client.publish(channel, raw_msg)
                return
            except Exception as e:
                print(f"[WebSocket] Redis publish failed: {e}. Falling back to in-memory broadcast.")

        # In-Memory Broadcast Fallback
        participant_ids = list(self.conversation_users.get(conversation_id, set()))
        for participant_id in participant_ids:
            sockets = list(self.user_connections.get(participant_id, set()))
            for ws in sockets:
                try:
                    await ws.send_text(raw_msg)
                except Exception:
                    pass

    async def listen_to_redis(self, conversation_id: uuid.UUID):
        """
        Listen to Redis pub/sub channel for this conversation and dispatch
        incoming messages directly to connected participants.
        """
        redis_client = await self.connect_redis()
        if not redis_client:
            return

        pubsub = redis_client.pubsub()
        channel = f"chat:{conversation_id}"
        
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    payload_raw = message["data"]
                    participant_ids = list(self.conversation_users.get(conversation_id, set()))
                    for participant_id in participant_ids:
                        sockets = list(self.user_connections.get(participant_id, set()))
                        for ws in sockets:
                            try:
                                await ws.send_text(payload_raw)
                            except Exception:
                                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[ConnectionManager] Redis listener error on {channel}: {e}")
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass

    async def send_personal_message(self, user_id: uuid.UUID, message_payload: Dict[str, Any]):
        """
        Send a direct message to all active WebSocket connections for a user.
        """
        if user_id in self.user_connections:
            raw = json.dumps(message_payload)
            for ws in list(self.user_connections[user_id]):
                try:
                    await ws.send_text(raw)
                except Exception:
                    pass

    def is_user_online(self, user_id: uuid.UUID) -> bool:
        """Check if a user currently has any active WebSocket connections."""
        return user_id in self.user_connections and len(self.user_connections[user_id]) > 0

    def get_online_users_in_conversation(self, conversation_id: uuid.UUID) -> Set[uuid.UUID]:
        """Return the set of user IDs active in a conversation room."""
        return self.conversation_users.get(conversation_id, set())

manager = ConnectionManager()
