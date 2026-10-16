"""Pytest fixtures for the property-based test suite.

The session-scoped fixture creates the isolated PostgreSQL test schema and its
tables. The function-scoped fixture truncates the tables and resets the slowapi
rate limiter before every test so that tests never observe each other's state.

If PostgreSQL is unreachable the whole suite is skipped rather than erroring, so
a checkout without a database still collects cleanly.
"""

from __future__ import annotations

import pytest

from tests.property import _harness as h


@pytest.fixture(scope="session", autouse=True)
def _database():
    try:
        h.run(h.ensure_schema())
        h.run(h.create_all())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL test schema unavailable: {exc}")
    yield
    h.run(h.dispose_engine())


@pytest.fixture(autouse=True)
def _clean_state(_database):
    from app.limiter import limiter

    h.run(h.reset_tables())
    limiter.reset()
    limiter.enabled = False
    yield
    limiter.enabled = True
