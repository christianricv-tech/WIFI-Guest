"""
Pytest configuration shared by all test packages.

Sets the required environment variables before any application module is
imported, so that ``app.config`` (which exits on missing variables) can be
imported during test collection without a real ``.env`` file. These values are
never used to connect to a database by the unit tests — they only satisfy the
start-up validation.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-chars-long")
os.environ.setdefault("RADIUS_SHARED_SECRET", "test-radius-shared-secret")
os.environ.setdefault("APP_ENV", "development")

# Import the models and configure the mappers up front. Mapped attributes keep
# ``InstrumentedAttribute.impl = None`` until ``configure_mappers()`` runs, and
# unit tests build ORM objects directly (via ``__new__``) rather than through a
# session, so nothing else would trigger mapper configuration.
from sqlalchemy.orm import configure_mappers  # noqa: E402

from app.models import admin as _admin  # noqa: E402,F401
from app.models import audit_log as _audit_log  # noqa: E402,F401
from app.models import voucher as _voucher  # noqa: E402,F401

configure_mappers()
