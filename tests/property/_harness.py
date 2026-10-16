"""Shared harness for the property-based and integration test suites.

The tests exercise the *real* application code against a real PostgreSQL
database. To avoid touching development data, everything lives in a dedicated
schema inside the configured ``DATABASE_URL`` database. A schema is created and
its tables (re)created once per test session; individual scenarios truncate the
tables so each test/example starts from a clean slate.

A single event loop is reused for the whole session so that the async
SQLAlchemy engine and httpx client can be driven from synchronous test bodies
(Hypothesis does not await coroutines).

A :class:`Harness` owns one schema-specific engine/session factory. The
property tests use the module-level default (``voucher_test``); the integration
tests instantiate their own (``voucher_it``).
"""

from __future__ import annotations

import asyncio
import os
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.models import admin as _admin  # noqa: F401
from app.models import audit_log as _audit_log  # noqa: F401
from app.models import voucher as _voucher  # noqa: F401
from app.models.admin import Admin
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.base import Base
from app.models.voucher import Voucher  # noqa: F401

# Directory that contains the ``app`` package (used by the config property test).
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# The isolated schema used by every property test.
TEST_SCHEMA = "voucher_test"

# A fixed admin UUID that the admin JWT override pretends to be. Rows written by
# voucher generation reference it via the created_by FK, so tests that hit
# POST /admin/vouchers must seed a matching admins row first.
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# DDL for the rlm_sql view. Kept byte-for-byte in step with Alembic migration
# 004_create_radius_view (the view is not derived from Base.metadata, so
# create_all() alone does not produce it).
RADIUS_VIEW_SQL = """
CREATE OR REPLACE VIEW radius_voucher_view AS
SELECT
    code AS username,
    code AS password,
    CASE
        WHEN status = 'unused'
             AND (
                 (activation_mode = 'pre-activated' AND expires_at > NOW())
                 OR (activation_mode = 'use-activated' AND expires_at IS NULL)
                 OR (activation_mode = 'use-activated' AND expires_at > NOW())
             )
        THEN 1
        ELSE 0
    END AS is_valid
FROM vouchers;
"""


def _database_url() -> str:
    # tests/conftest.py sets a placeholder DATABASE_URL via os.environ.setdefault
    # before app.config loads .env (which does not override existing variables).
    # Prefer the real value from the project .env file when it is present.
    from dotenv import dotenv_values

    env_file = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
    url_str = env_file.get("DATABASE_URL") or os.environ["DATABASE_URL"]
    url = make_url(url_str)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")
    elif url.drivername == "postgres":
        url = url.set(drivername="postgresql+asyncpg")
    return url.render_as_string(hide_password=False)


DATABASE_URL = _database_url()

_loop: asyncio.AbstractEventLoop | None = None


def run(coro):
    """Run *coro* on the process-wide event loop shared by all tests."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


def new_voucher(**kwargs) -> Voucher:
    """Build a :class:`Voucher` ORM object with sensible defaults."""
    defaults = dict(
        code="AB3CD5EF7GH8",
        status="unused",
        activation_mode="use-activated",
        duration_minutes=60,
        expires_at=None,
        consumed_at=None,
        revoked_at=None,
        created_by=None,
    )
    defaults.update(kwargs)
    return Voucher(**defaults)


class Harness:
    """Schema-scoped engine/session factory plus HTTP helpers."""

    def __init__(self, schema: str):
        self.schema = schema
        self._engine = None
        self._session_factory = None

    # -- engine / sessions --------------------------------------------------

    def get_engine(self):
        if self._engine is None:
            self._engine = create_async_engine(
                DATABASE_URL,
                poolclass=NullPool,
                connect_args={"server_settings": {"search_path": self.schema}},
            )
        return self._engine

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                self.get_engine(),
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._session_factory

    async def dispose_engine(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    # -- schema management --------------------------------------------------

    async def ensure_schema(self) -> None:
        engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self.schema}"))
        finally:
            await engine.dispose()

    async def create_all(self, *, with_view: bool = False) -> None:
        """(Re)create every table (and optionally the rlm_sql view)."""
        engine = self.get_engine()
        async with engine.begin() as conn:
            if with_view:
                await conn.execute(text("DROP VIEW IF EXISTS radius_voucher_view CASCADE"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            if with_view:
                await conn.execute(text(RADIUS_VIEW_SQL))

    async def reset_tables(self) -> None:
        engine = self.get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"TRUNCATE TABLE {self.schema}.audit_logs, "
                    f"{self.schema}.vouchers, {self.schema}.admins "
                    "RESTART IDENTITY CASCADE"
                )
            )

    # -- data helpers -------------------------------------------------------

    async def seed_admin(
        self,
        username: str = "admin",
        password_hash: str = "x",
        admin_id: uuid.UUID = ADMIN_ID,
    ) -> None:
        """Insert an admin row (FK target for generated vouchers, login target)."""
        async with self.get_session_factory()() as session:
            session.add(Admin(id=admin_id, username=username, password_hash=password_hash))
            await session.commit()

    async def insert_voucher(self, **kwargs) -> str:
        async with self.get_session_factory()() as session:
            voucher = new_voucher(**kwargs)
            session.add(voucher)
            await session.commit()
            return voucher.code

    # -- HTTP helpers -------------------------------------------------------

    def build_app(self, *, authenticate: bool = True, wrap_session=None) -> FastAPI:
        """Build a FastAPI app wired to this harness's database.

        ``get_db`` yields sessions bound to the isolated schema. When
        *authenticate* is true, admin auth is stubbed with a fixed identity
        (used by the schema-focused property tests); when false, the real JWT
        dependency is used (used by the integration tests). *wrap_session* lets
        a test proxy each session (e.g. to inject faults).
        """
        from app.database import get_db
        from app.limiter import limiter
        from app.routers import admin_audit, admin_auth, admin_vouchers, radius
        from app.services.auth_service import get_current_admin

        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.include_router(radius.router)
        app.include_router(admin_vouchers.router)
        app.include_router(admin_audit.router)
        app.include_router(admin_auth.router)

        async def _override_db():
            async with self.get_session_factory()() as session:
                yield wrap_session(session) if wrap_session is not None else session

        app.dependency_overrides[get_db] = _override_db

        if authenticate:

            async def _override_admin():
                return {
                    "sub": str(ADMIN_ID),
                    "username": "admin",
                    "jti": "property-test-jti",
                }

            app.dependency_overrides[get_current_admin] = _override_admin

        return app

    async def request(self, app: FastAPI, method: str, path: str, **kwargs):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# Module-level default harness (backwards-compatible helpers for the property
# tests). The integration tests build their own Harness with another schema.
# ---------------------------------------------------------------------------

_default = Harness(TEST_SCHEMA)


def get_engine():
    return _default.get_engine()


def get_session_factory():
    return _default.get_session_factory()


async def dispose_engine() -> None:
    await _default.dispose_engine()


async def ensure_schema() -> None:
    await _default.ensure_schema()


async def create_all() -> None:
    await _default.create_all()


async def reset_tables() -> None:
    await _default.reset_tables()


async def seed_admin(**kwargs) -> None:
    await _default.seed_admin(**kwargs)


async def insert_voucher(**kwargs) -> str:
    return await _default.insert_voucher(**kwargs)


def build_app(**kwargs) -> FastAPI:
    return _default.build_app(**kwargs)


async def request(app: FastAPI, method: str, path: str, **kwargs):
    return await _default.request(app, method, path, **kwargs)
