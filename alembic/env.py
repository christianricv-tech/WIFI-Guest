"""
alembic/env.py — Async Alembic environment using the asyncpg engine.

Imports all models so Alembic can detect schema changes automatically,
then configures the async migration pattern required by asyncpg.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Import Base so that target_metadata includes all table definitions
# ---------------------------------------------------------------------------
from app.models.base import Base  # noqa: F401

# Import every model module so their Table objects register on Base.metadata.
# Alembic's autogenerate compares target_metadata against the live DB schema.
from app.models.admin import Admin  # noqa: F401
from app.models.voucher import Voucher  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401

# ---------------------------------------------------------------------------
# Import DATABASE_URL from app config; this overrides the placeholder in
# alembic.ini so the correct connection string is always used.
# ---------------------------------------------------------------------------
from app.config import DATABASE_URL

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------
config = context.config

# Override sqlalchemy.url from app config (beats any value in alembic.ini)
_async_url = DATABASE_URL
if _async_url.startswith("postgresql://"):
    _async_url = _async_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _async_url.startswith("postgres://"):
    _async_url = _async_url.replace("postgres://", "postgresql+asyncpg://", 1)

config.set_main_option("sqlalchemy.url", _async_url)

# Wire up Python logging from alembic.ini if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Target metadata — Alembic compares this against the DB to generate diffs
# ---------------------------------------------------------------------------
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline migrations (--sql mode — generates raw SQL without a live DB)
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations without a live database connection.

    Emits migration SQL to stdout (or a file) rather than executing it.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations — async engine pattern required by asyncpg
# ---------------------------------------------------------------------------

def do_run_migrations(connection: Connection) -> None:
    """Execute migrations against a synchronous connection.

    This is called from within the async context via ``run_sync``.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations inside ``run_sync``.

    asyncpg is a fully async driver; Alembic bridges the gap by calling
    ``run_sync(do_run_migrations)`` which runs the migration logic on the
    event loop thread using a synchronous-compatible connection proxy.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online (live DB) migrations.

    Wraps the async migration coroutine with ``asyncio.run()``.
    """
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
