from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings

db_url = settings.DATABASE_URL.strip()

# Normalize connection protocol for async SQLAlchemy (asyncpg)
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+asyncpg://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Configure engine arguments based on driver
engine_kwargs = {
    "echo": False,
    "future": True,
}

if "postgresql+asyncpg" in db_url:
    # High-Performance Supabase & Cloud PostgreSQL Configuration:
    # 1. pool_size=20, max_overflow=20: Prevents connection starvation during concurrent requests
    # 2. pool_recycle=1800: Keeps warm connections alive for 30 minutes, avoiding frequent 2.5s reconnects
    # 3. pool_pre_ping=False: Eliminates the 250ms roundtrip SELECT 1 ping before every transaction
    # 4. statement_cache_size=0: Required for Supabase transaction pooler (port 6543 / Supavisor)
    engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 20,
        "pool_timeout": 30,
        "pool_pre_ping": False,
        "pool_recycle": 1800,
        "connect_args": {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        }
    })

# Async engine setup
engine = create_async_engine(db_url, **engine_kwargs)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

async def get_db():
    """
    Dependency for FastAPI that yields a database session.
    """
    async with AsyncSessionLocal() as session:
        yield session
