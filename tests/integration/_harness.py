"""Integration-test harness.

Reuses the property-test :class:`~tests.property._harness.Harness` machinery but
points at a distinct schema (``voucher_it``) so its tables — including the
migration-defined ``radius_voucher_view`` — never clash with the property
suite's ``voucher_test`` schema.

The event loop is shared with the property harness (``run``) so that both
suites can run in a single pytest process.
"""

from __future__ import annotations

from tests.property._harness import (  # noqa: F401  (re-exported)
    ADMIN_ID,
    DATABASE_URL,
    Harness,
    new_voucher,
    run,
)

SCHEMA = "voucher_it"

_harness = Harness(SCHEMA)


def get_engine():
    return _harness.get_engine()


def get_session_factory():
    return _harness.get_session_factory()


async def dispose_engine() -> None:
    await _harness.dispose_engine()


async def ensure_schema() -> None:
    await _harness.ensure_schema()


async def create_all() -> None:
    # Include the rlm_sql view so the view-query integration path is testable.
    await _harness.create_all(with_view=True)


async def reset_tables() -> None:
    await _harness.reset_tables()


async def seed_admin(**kwargs) -> None:
    await _harness.seed_admin(**kwargs)


async def insert_voucher(**kwargs) -> str:
    return await _harness.insert_voucher(**kwargs)


def build_app(**kwargs):
    return _harness.build_app(**kwargs)


async def request(app, method: str, path: str, **kwargs):
    return await _harness.request(app, method, path, **kwargs)
