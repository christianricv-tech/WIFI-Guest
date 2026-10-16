"""Integration tests for audit logging behaviour.

Run against a live PostgreSQL database (``voucher_it`` schema).

Requirements: 7.1, 7.2, 7.5
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.integration import _harness as h

CODE = "AB3CD5EF7GH8"


async def _audit_rows():
    async with h.get_session_factory()() as session:
        return (
            await session.execute(select(AuditLog).order_by(AuditLog.id))
        ).scalars().all()


def test_every_validate_creates_one_audit_entry():
    async def scenario():
        await h.reset_tables()
        await h.insert_voucher(code=CODE, activation_mode="use-activated", duration_minutes=60)
        app = h.build_app()

        accepted = await h.request(
            app,
            "POST",
            "/radius/validate",
            json={"username": CODE, "password": CODE, "client_mac": "AA:BB:CC:DD:EE:01"},
        )
        rejected = await h.request(
            app,
            "POST",
            "/radius/validate",
            json={"username": "NOSUCHCODE12", "password": "NOSUCHCODE12"},
        )
        assert accepted.json()["result"] == "accept"
        assert rejected.json()["result"] == "reject"

        rows = await _audit_rows()
        assert len(rows) == 2

        by_code = {row.voucher_code: row for row in rows}
        assert by_code[CODE].outcome == "accepted"
        assert by_code[CODE].client_mac == "AA:BB:CC:DD:EE:01"
        assert by_code[CODE].source == "rlm_rest"
        assert by_code["NOSUCHCODE12"].outcome == "rejected"
        assert by_code["NOSUCHCODE12"].rejection_reason == "not_found"

    h.run(scenario())


class _FailingAuditSession:
    """Session proxy that fails any commit which writes an AuditLog row."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def commit(self):
        if any(isinstance(obj, AuditLog) for obj in self._real.new):
            raise RuntimeError("forced audit failure")
        return await self._real.commit()


def test_audit_write_failure_does_not_affect_response(caplog):
    async def scenario():
        await h.reset_tables()
        await h.insert_voucher(code=CODE, activation_mode="use-activated", duration_minutes=60)

        app = h.build_app(
            authenticate=True, wrap_session=lambda session: _FailingAuditSession(session)
        )
        resp = await h.request(
            app, "POST", "/radius/validate", json={"username": CODE, "password": CODE}
        )

        # The validation result is unaffected by the audit failure (Req 7.5).
        assert resp.status_code == 200
        assert resp.json() == {"result": "accept"}

        # ...and nothing was persisted to the audit log.
        assert await _audit_rows() == []

    with caplog.at_level(logging.ERROR, logger="app.services.audit_service"):
        h.run(scenario())

    assert any("Audit log write failed" in rec.message for rec in caplog.records)
