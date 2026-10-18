import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs('static/uploads', exist_ok=True)
    try:
        from app.db.base import Base
        from app.db.session import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            try:
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_phone_verified BOOLEAN DEFAULT FALSE;'))
            except Exception:
                try:
                    await conn.execute(text('ALTER TABLE user ADD COLUMN is_phone_verified BOOLEAN DEFAULT 0;'))
                except Exception:
                    pass
            try:
                await conn.execute(text('ALTER TABLE listing ALTER COLUMN image_url TYPE TEXT;'))
                await conn.execute(text('ALTER TABLE listing_image ALTER COLUMN image_url TYPE TEXT;'))
            except Exception:
                pass
    except Exception as e:
        print(f"Database initialization note: {e}")
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    description='A privacy-first, anonymous campus marketplace.',
    version='0.1.0',
    lifespan=lifespan,
)

os.makedirs('static/uploads', exist_ok=True)
app.mount('/static', StaticFiles(directory='static'), name='static')

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173', '*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

@app.get('/health', tags=['Health'])
async def health_check():
    return {'status': 'ok', 'project': settings.PROJECT_NAME}

from app.api.v1 import auth, listings, dropzones, chat, escrow, users, conversations

# Auth routes (mounted at both /api/auth and /api/v1/auth)
app.include_router(auth.router, prefix='/api/auth', tags=['auth'])
app.include_router(auth.router, prefix='/api/v1/auth', tags=['auth'])

# Listings routes (mounted at both /api/listings and /api/v1/listings)
app.include_router(listings.router, prefix='/api/listings', tags=['listings'])
app.include_router(listings.router, prefix='/api/v1/listings', tags=['listings'])

# Conversations routes (mounted at both /api/conversations and /api/v1/conversations)
app.include_router(conversations.router, prefix='/api/conversations', tags=['conversations'])
app.include_router(conversations.router, prefix='/api/v1/conversations', tags=['conversations'])

# Chat WebSocket and routes (WebSocket mounted at /ws/chat/{conversation_id})
app.include_router(chat.router, tags=['chat'])
app.include_router(chat.router, prefix='/api/v1/chat', tags=['chat'])

app.include_router(dropzones.router, prefix='/api/v1/dropzones', tags=['dropzones'])
app.include_router(escrow.router, prefix='/api/v1/escrow', tags=['escrow'])
app.include_router(users.router, prefix='/api/v1/users', tags=['users'])
