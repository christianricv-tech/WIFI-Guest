"""Pytest fixtures for the integration test suite.

Creates the dedicated ``voucher_it`` schema (tables + the migration-defined
``radius_voucher_view``) once per session, and truncates the tables plus resets
the rate limiter before each test. Skips cleanly when PostgreSQL is unreachable.
"""

from __future__ import annotations

import pytest

from tests.integration import _harness as h


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
