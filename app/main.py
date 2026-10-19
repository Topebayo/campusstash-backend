import os
import time
import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.limiter import limiter, rate_limit_exceeded_handler

# Set up structured logger
logger = logging.getLogger("campusstash")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs('static/uploads', exist_ok=True)
    try:
        from app.db.base import Base
        from app.db.session import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            # Enable pgcrypto for password/PIN hashing in Postgres / Supabase
            try:
                await conn.execute(text('CREATE EXTENSION IF NOT EXISTS pgcrypto;'))
            except Exception:
                pass

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
        logger.info("Database schemas verified and synchronized successfully.")
    except Exception as e:
        logger.warning(f"Database initialization note: {e}")
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    description='A privacy-first, anonymous campus marketplace.',
    version='1.0.0',
    lifespan=lifespan,
)

# Connect SlowAPI Limiter state & error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Custom Security Headers Middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Prevent iframe clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Cross-Site Scripting protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions Policy
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Enforce HTTPS strictly in non-development environments
        if settings.ENVIRONMENT != "development":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Configure CORS with strict origins whitelist (No wildcard '*' with credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"]
)

# Global unhandled exception shielding & monitoring
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Log complete traceback on server for monitoring, but hide internal details from client
    logger.error(
        f"[SERVER ERROR] {request.method} {request.url.path}: {exc}\n"
        f"{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected server error occurred. Incident has been logged for monitoring.",
            "error": "internal_server_error"
        }
    )

os.makedirs('static/uploads', exist_ok=True)
app.mount('/static', StaticFiles(directory='static'), name='static')

@app.get('/health', tags=['Health'])
async def health_check():
    db_status = "healthy"
    try:
        from app.db.session import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)[:60]}"

    return {
        'status': 'ok' if db_status == 'healthy' else 'degraded',
        'project': settings.PROJECT_NAME,
        'environment': settings.ENVIRONMENT,
        'database': db_status,
        'timestamp': time.time()
    }

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
