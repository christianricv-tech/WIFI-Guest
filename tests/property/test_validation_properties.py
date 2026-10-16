"""RADIUS validation, revocation, audit logging, and rate limiting.

Feature: aruba-voucher-backend.

- Property 6:  Valid voucher always accepted          (Req 2.2)
- Property 7:  Non-existent code always rejected       (Req 2.3)
- Property 8:  Invalid-state voucher always rejected   (Req 2.4, 2.5, 2.6)
- Property 12: Successful validation sets consumed state (Req 4.1)
- Property 14: Revocation sets revoked state           (Req 5.1)
- Property 16: Audit log written for every validation  (Req 7.1, 7.2)
- Property 17: Audit log filter correctness            (Req 7.4)
- Property 20: Rate limit enforcement                  (Req 11.6)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.voucher import Voucher
from tests.property import _harness as h

CODE = "AB3CD5EF7GH8"
VALIDATE_URL = "/radius/validate"
AUDIT_URL = "/admin/audit-logs"
VALID_MODES = ("use-activated", "pre-activated")


async def _validate(app, code, client_mac=None) -> dict:
    resp = await h.request(
        app,
        "POST",
        VALIDATE_URL,
        json={"username": code, "password": code, "client_mac": client_mac},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Property 6: Valid voucher always accepted (Req 2.2)
# ---------------------------------------------------------------------------


async def _accept_scenario(activation_mode, duration_minutes):
    await h.reset_tables()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        if activation_mode == "pre-activated"
        else None
    )
    await h.insert_voucher(
        code=CODE,
        activation_mode=activation_mode,
        duration_minutes=duration_minutes,
        expires_at=expires_at,
    )
    return await _validate(h.build_app(), CODE)


@given(
    activation_mode=st.sampled_from(VALID_MODES),
    duration_minutes=st.integers(min_value=1, max_value=43200),
)
@settings(max_examples=15, deadline=None)
def test_valid_voucher_always_accepted(activation_mode, duration_minutes):
    result = h.run(_accept_scenario(activation_mode, duration_minutes))
    assert result == {"result": "accept"}


# ---------------------------------------------------------------------------
# Property 7: Non-existent code always rejected (Req 2.3)
# ---------------------------------------------------------------------------


async def _nonexistent_scenario(code):
    await h.reset_tables()
    return await _validate(h.build_app(), code)


@given(
    code=st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    )
)
@settings(max_examples=15, deadline=None)
def test_nonexistent_code_rejected(code):
    result = h.run(_nonexistent_scenario(code))
    assert result == {"result": "reject", "reason": "not_found"}


# ---------------------------------------------------------------------------
# Property 8: Invalid-state voucher always rejected (Req 2.4, 2.5, 2.6)
# ---------------------------------------------------------------------------


async def _invalid_state_scenario(case):
    await h.reset_tables()
    now = datetime.now(timezone.utc)
    if case == "consumed":
        await h.insert_voucher(code=CODE, status="consumed", consumed_at=now)
    elif case == "revoked":
        await h.insert_voucher(code=CODE, status="revoked", revoked_at=now)
    else:  # expired
        await h.insert_voucher(
            code=CODE,
            status="unused",
            activation_mode="pre-activated",
            expires_at=now - timedelta(minutes=1),
        )
    return await _validate(h.build_app(), CODE)


@given(case=st.sampled_from(["consumed", "revoked", "expired"]))
@settings(max_examples=15, deadline=None)
def test_invalid_state_voucher_rejected(case):
    result = h.run(_invalid_state_scenario(case))
    assert result["result"] == "reject"
    assert result["reason"] == case


# ---------------------------------------------------------------------------
# Property 12: Successful validation sets consumed state (Req 4.1)
# ---------------------------------------------------------------------------


async def _consumed_state_scenario(activation_mode, duration_minutes):
    await h.reset_tables()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        if activation_mode == "pre-activated"
        else None
    )
    await h.insert_voucher(
        code=CODE,
        activation_mode=activation_mode,
        duration_minutes=duration_minutes,
        expires_at=expires_at,
    )
    result = await _validate(h.build_app(), CODE)
    assert result == {"result": "accept"}

    async with h.get_session_factory()() as session:
        row = (
            await session.execute(select(Voucher).where(Voucher.code == CODE))
        ).scalar_one()
        return row.status, row.consumed_at


@given(
    activation_mode=st.sampled_from(VALID_MODES),
    duration_minutes=st.integers(min_value=1, max_value=43200),
)
@settings(max_examples=15, deadline=None)
def test_successful_validation_sets_consumed_state(activation_mode, duration_minutes):
    status, consumed_at = h.run(_consumed_state_scenario(activation_mode, duration_minutes))
    assert status == "consumed"
    assert consumed_at is not None


# ---------------------------------------------------------------------------
# Property 14: Revocation sets revoked state (Req 5.1)
# ---------------------------------------------------------------------------


async def _revoke_scenario(duration_minutes, activation_mode):
    await h.reset_tables()
    await h.insert_voucher(
        code=CODE,
        activation_mode=activation_mode,
        duration_minutes=duration_minutes,
    )
    app = h.build_app()
    resp = await h.request(app, "DELETE", f"/admin/vouchers/{CODE}/revoke")
    assert resp.status_code == 200, resp.text

    async with h.get_session_factory()() as session:
        row = (
            await session.execute(select(Voucher).where(Voucher.code == CODE))
        ).scalar_one()
        return row.status, row.revoked_at


@given(
    duration_minutes=st.integers(min_value=1, max_value=43200),
    activation_mode=st.sampled_from(VALID_MODES),
)
@settings(max_examples=15, deadline=None)
def test_revocation_sets_revoked_state(duration_minutes, activation_mode):
    status, revoked_at = h.run(_revoke_scenario(duration_minutes, activation_mode))
    assert status == "revoked"
    assert revoked_at is not None


# ---------------------------------------------------------------------------
# Property 16: Audit log written for every validation (Req 7.1, 7.2)
# ---------------------------------------------------------------------------

_MAC_STRATEGY = st.from_regex(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", fullmatch=True)


async def _audit_scenario(outcome, client_mac):
    await h.reset_tables()
    if outcome == "accepted":
        await h.insert_voucher(code=CODE)

    result = await _validate(h.build_app(), CODE, client_mac)
    expected_result = "accept" if outcome == "accepted" else "reject"
    assert result["result"] == expected_result
    if outcome == "rejected":
        assert result["reason"] == "not_found"

    async with h.get_session_factory()() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.voucher_code == CODE)
            )
        ).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        return {
            "voucher_code": row.voucher_code,
            "client_mac": row.client_mac,
            "outcome": row.outcome,
            "rejection_reason": row.rejection_reason,
            "logged_at": row.logged_at,
        }


@given(outcome=st.sampled_from(["accepted", "rejected"]), client_mac=_MAC_STRATEGY)
@settings(max_examples=15, deadline=None)
def test_audit_log_written_for_every_validation(outcome, client_mac):
    entry = h.run(_audit_scenario(outcome, client_mac))
    assert entry["voucher_code"] == CODE
    assert entry["client_mac"] == client_mac
    assert entry["outcome"] == outcome
    assert entry["logged_at"] is not None
    if outcome == "rejected":
        assert entry["rejection_reason"] == "not_found"


# ---------------------------------------------------------------------------
# Property 17: Audit log filter correctness (Req 7.4)
# ---------------------------------------------------------------------------

_MACS = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", None]
_OUTCOMES = ["accepted", "rejected"]
_DATES = [date(2024, 1, 1), date(2024, 1, 3), date(2024, 1, 5)]


@st.composite
def _entries_and_filters(draw):
    n = draw(st.integers(min_value=1, max_value=30))
    entries = []
    for i in range(n):
        entries.append(
            {
                "voucher_code": f"CODE{i:08d}",
                "client_mac": draw(st.sampled_from(_MACS)),
                "outcome": draw(st.sampled_from(_OUTCOMES)),
                "logged_at": draw(
                    st.datetimes(
                        min_value=datetime(2024, 1, 1),
                        max_value=datetime(2024, 1, 5, 23, 59, 59),
                        timezones=st.just(timezone.utc),
                    )
                ),
            }
        )
    filters = {
        "voucher_code": draw(st.sampled_from([None] + [e["voucher_code"] for e in entries])),
        "client_mac": draw(st.sampled_from([None] + _MACS)),
        "outcome": draw(st.sampled_from([None] + _OUTCOMES)),
        "date_from": draw(st.sampled_from([None] + _DATES)),
        "date_to": draw(st.sampled_from([None] + _DATES)),
    }
    return entries, filters


def _matches(entry, filters) -> bool:
    if filters["voucher_code"] and entry["voucher_code"] != filters["voucher_code"]:
        return False
    if filters["client_mac"] and entry["client_mac"] != filters["client_mac"]:
        return False
    if filters["outcome"] and entry["outcome"] != filters["outcome"]:
        return False
    logged_at = entry["logged_at"]
    if filters["date_from"]:
        start = datetime.combine(filters["date_from"], time.min, tzinfo=timezone.utc)
        if logged_at < start:
            return False
    if filters["date_to"]:
        end = datetime.combine(filters["date_to"], time.max, tzinfo=timezone.utc)
        if logged_at > end:
            return False
    return True


async def _audit_filter_scenario(entries, filters):
    await h.reset_tables()
    async with h.get_session_factory()() as session:
        ids = {}
        for entry in entries:
            row = AuditLog(
                voucher_code=entry["voucher_code"],
                client_mac=entry["client_mac"],
                outcome=entry["outcome"],
                rejection_reason=None if entry["outcome"] == "accepted" else "not_found",
                logged_at=entry["logged_at"],
            )
            session.add(row)
            await session.flush()
            ids[row.id] = entry
        await session.commit()

    params = {k: v for k, v in filters.items() if v is not None}
    resp = await h.request(h.build_app(), "GET", AUDIT_URL, params=params)
    assert resp.status_code == 200, resp.text
    returned_ids = {item["id"] for item in resp.json()["items"]}

    expected_ids = {
        row_id for row_id, entry in ids.items() if _matches(entry, filters)
    }
    return returned_ids, expected_ids, resp.json()["total"]


@given(data=_entries_and_filters())
@settings(max_examples=15, deadline=None)
def test_audit_log_filter_correctness(data):
    entries, filters = data
    returned_ids, expected_ids, total = h.run(_audit_filter_scenario(entries, filters))
    assert returned_ids == expected_ids
    assert total == len(expected_ids)


# ---------------------------------------------------------------------------
# Property 20: Rate limit enforcement (Req 11.6)
# ---------------------------------------------------------------------------

_WINDOW = 60  # The endpoint is limited to 60 requests per minute.

_RATE_COUNTS = st.one_of(
    st.sampled_from([61, 70, 80]), st.integers(min_value=61, max_value=80)
)


async def _rate_limit_scenario(n):
    from app.limiter import limiter

    await h.reset_tables()
    limiter.reset()
    limiter.enabled = True
    try:
        app = h.build_app()
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            for _ in range(_WINDOW):
                resp = await client.post(
                    VALIDATE_URL,
                    json={"username": CODE, "password": CODE},
                )
                assert resp.status_code == 200, resp.text
            throttled = []
            for _ in range(n - _WINDOW):
                resp = await client.post(
                    VALIDATE_URL,
                    json={"username": CODE, "password": CODE},
                )
                throttled.append(resp.status_code)
        return throttled
    finally:
        limiter.enabled = False
        limiter.reset()


@given(n=_RATE_COUNTS)
@settings(max_examples=8, deadline=None)
def test_rate_limit_enforcement(n):
    throttled = h.run(_rate_limit_scenario(n))
    assert len(throttled) == n - _WINDOW
    assert all(status == 429 for status in throttled)

