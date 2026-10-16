"""
app/database.py — Async SQLAlchemy engine and session factory.

Creates an async engine using the asyncpg dialect, exposes an
AsyncSessionLocal factory, and provides a get_db() FastAPI dependency
that yields a session and closes it after each request.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import DATABASE_URL

# ---------------------------------------------------------------------------
# Async engine
# ---------------------------------------------------------------------------
# SQLAlchemy expects the asyncpg dialect via the "postgresql+asyncpg://" scheme.
# If DATABASE_URL is provided as "postgresql://...", swap the scheme so that
# the correct driver is used without requiring the caller to know the dialect.
_async_url = DATABASE_URL
if _async_url.startswith("postgresql://"):
    _async_url = _async_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _async_url.startswith("postgres://"):
    # Heroku-style shorthand
    _async_url = _async_url.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    _async_url,
    # Echo SQL statements in development; silenced at runtime by LOG_LEVEL.
    echo=False,
    # Keep a small connection pool appropriate for a single-process app.
    pool_size=5,
    max_overflow=10,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_db():
    """Yield an AsyncSession and close it after the request completes.

    Usage::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        yield session
