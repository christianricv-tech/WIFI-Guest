"""Integration tests for the FreeRADIUS validation flows.

These run against a live PostgreSQL database using the ``voucher_it`` schema,
which includes the migration-defined ``radius_voucher_view``. They exercise the
real FastAPI endpoints (as rlm_rest would) and the real view SQL (as rlm_sql
would). The genuinely external FreeRADIUS process is not required — the tests
cover the database and HTTP contracts FreeRADIUS depends on.

Requirements: 4.3, 4.4, 10.1, 10.3, 10.4, 10.5
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.integration import _harness as h

CODE = "AB3CD5EF7GH8"


async def _view_is_valid(code: str):
    async with h.get_session_factory()() as session:
        return (
            await session.execute(
                text("SELECT is_valid FROM radius_voucher_view WHERE username = :c"),
                {"c": code},
            )
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# rlm_rest: POST /radius/validate
# ---------------------------------------------------------------------------


def test_rlm_rest_flow_accept_then_consumed():
    async def scenario():
        await h.reset_tables()
        await h.insert_voucher(code=CODE, activation_mode="use-activated", duration_minutes=60)
        app = h.build_app()

        first = await h.request(
            app, "POST", "/radius/validate", json={"username": CODE, "password": CODE}
        )
        assert first.status_code == 200
        assert first.json() == {"result": "accept"}

        second = await h.request(
            app, "POST", "/radius/validate", json={"username": CODE, "password": CODE}
        )
        assert second.status_code == 200
        assert second.json() == {"result": "reject", "reason": "consumed"}

    h.run(scenario())


def test_rlm_rest_rejects_non_existent_code():
    async def scenario():
        await h.reset_tables()
        resp = await h.request(
            h.build_app(),
            "POST",
            "/radius/validate",
            json={"username": "ZZZZZZZZZZZZ", "password": "ZZZZZZZZZZZZ"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"result": "reject", "reason": "not_found"}

    h.run(scenario())


# ---------------------------------------------------------------------------
# rlm_sql: radius_voucher_view + POST /radius/post-auth
# ---------------------------------------------------------------------------


def test_rlm_sql_view_query_and_post_auth():
    async def scenario():
        await h.reset_tables()
        await h.insert_voucher(code=CODE, activation_mode="use-activated", duration_minutes=60)

        # The view (exactly as migration 004 defines it) reports the voucher valid.
        assert await _view_is_valid(CODE) == 1

        app = h.build_app()
        resp = await h.request(app, "POST", "/radius/post-auth", json={"username": CODE})
        assert resp.status_code == 200

        # After post-auth the row is consumed and the view reports it invalid.
        assert await _view_is_valid(CODE) == 0
        async with h.get_session_factory()() as session:
            status = (
                await session.execute(
                    text("SELECT status FROM vouchers WHERE code = :c"), {"c": CODE}
                )
            ).scalar_one()
        assert status == "consumed"

        # post-auth is idempotent for an already-consumed voucher.
        again = await h.request(app, "POST", "/radius/post-auth", json={"username": CODE})
        assert again.status_code == 200

    h.run(scenario())


def test_view_marks_expired_and_revoked_invalid():
    async def scenario():
        await h.reset_tables()
        now = datetime.now(timezone.utc)
        await h.insert_voucher(
            code="EXPIRED00001",
            activation_mode="pre-activated",
            duration_minutes=60,
            expires_at=now - timedelta(minutes=5),
        )
        await h.insert_voucher(
            code="REVOKED00001",
            status="revoked",
            revoked_at=now,
        )
        # Both variants must be reported invalid by the view.
        assert await _view_is_valid("EXPIRED00001") == 0
        assert await _view_is_valid("REVOKED00001") == 0

    h.run(scenario())


# ---------------------------------------------------------------------------
# Concurrency: exactly one accept (Req 4.3, 4.4)
# ---------------------------------------------------------------------------


def test_concurrent_validation_is_atomic():
    async def scenario():
        await h.reset_tables()
        await h.insert_voucher(code=CODE, activation_mode="use-activated", duration_minutes=60)

        app = h.build_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:

            async def one():
                r = await client.post(
                    "/radius/validate", json={"username": CODE, "password": CODE}
                )
                assert r.status_code == 200
                return r.json()

            results = await asyncio.gather(*[one() for _ in range(8)])

        accepts = [r for r in results if r["result"] == "accept"]
        rejects = [r for r in results if r["result"] == "reject"]
        assert len(accepts) == 1
        assert len(rejects) == 7
        assert all(r["reason"] == "consumed" for r in rejects)

    h.run(scenario())
