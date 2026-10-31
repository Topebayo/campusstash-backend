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
    # Supabase & cloud PostgreSQL optimization:
    # 1. pool_pre_ping: detects dropped connections across cloud firewalls
    # 2. pool_recycle: refreshes connections every 5 minutes
    # 3. statement_cache_size=0: required for Supabase transaction pooler (port 6543 / Supavisor / PgBouncer)
    engine_kwargs.update({
        "pool_pre_ping": True,
        "pool_recycle": 300,
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
