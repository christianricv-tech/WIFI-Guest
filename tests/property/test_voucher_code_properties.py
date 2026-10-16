"""Voucher code generation, batch generation, listing, and input validation.

Feature: aruba-voucher-backend.

- Property 1:  Voucher code character set        (Req 1.1)
- Property 2:  Batch quantity boundary           (Req 1.3)
- Property 3:  Custom duration boundary          (Req 1.5)
- Property 4:  Generated vouchers start unused   (Req 1.6)
- Property 5:  Invalid activation mode rejected  (Req 1.9)
- Property 15: Paginated voucher list completeness (Req 6.4)
- Property 19: Input validation returns field-level errors (Req 11.3)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select

from app.models.voucher import Voucher
from app.services.voucher_service import ALLOWED_CHARS, generate_voucher_code
from tests.property import _harness as h

VOUCHERS_URL = "/admin/vouchers/"
VALID_MODES = ("use-activated", "pre-activated")

# Boundaries plus a spread of ordinary values.
_QUANTITIES = st.one_of(
    st.sampled_from([0, 1, 500, 501, -1]), st.integers(min_value=-3, max_value=503)
)
_DURATIONS = st.one_of(
    st.sampled_from([0, 1, 43200, 43201]), st.integers(min_value=-3, max_value=43203)
)


# ---------------------------------------------------------------------------
# Property 1: Voucher code character set (Req 1.1)
# ---------------------------------------------------------------------------


@given(seed=st.integers())
@settings(max_examples=100, deadline=None)
def test_voucher_code_character_set(seed):
    code = generate_voucher_code()
    assert len(code) == 12
    assert all(ch in ALLOWED_CHARS for ch in code)
    assert not (set(code) & set("IO01"))


# ---------------------------------------------------------------------------
# Property 2: Batch quantity boundary (Req 1.3)
# ---------------------------------------------------------------------------


async def _create_scenario(quantity, duration_minutes, activation_mode="use-activated"):
    await h.reset_tables()
    await h.seed_admin()
    app = h.build_app()
    resp = await h.request(
        app,
        "POST",
        VOUCHERS_URL,
        json={
            "quantity": quantity,
            "duration_minutes": duration_minutes,
            "activation_mode": activation_mode,
        },
    )
    async with h.get_session_factory()() as session:
        count = (await session.execute(select(func.count()).select_from(Voucher))).scalar_one()
    return resp, count


@given(quantity=_QUANTITIES)
@settings(max_examples=12, deadline=None)
def test_batch_quantity_boundary(quantity):
    resp, count = h.run(_create_scenario(quantity, 60))
    if 1 <= quantity <= 500:
        assert resp.status_code == 201, resp.text
        assert resp.json()["generated"] == quantity
        assert count == quantity
    else:
        assert resp.status_code == 400
        assert resp.json()["detail"] == "quantity_out_of_range"
        assert count == 0


# ---------------------------------------------------------------------------
# Property 3: Custom duration boundary (Req 1.5)
# ---------------------------------------------------------------------------


@given(duration_minutes=_DURATIONS)
@settings(max_examples=12, deadline=None)
def test_custom_duration_boundary(duration_minutes):
    resp, count = h.run(_create_scenario(1, duration_minutes))
    if 1 <= duration_minutes <= 43200:
        assert resp.status_code == 201, resp.text
        assert count == 1
    else:
        assert resp.status_code == 400
        assert resp.json()["detail"] == "duration_out_of_range"
        assert count == 0


# ---------------------------------------------------------------------------
# Property 4: Generated vouchers start unused (Req 1.6)
# ---------------------------------------------------------------------------


@given(
    quantity=st.integers(min_value=1, max_value=10),
    duration_minutes=st.integers(min_value=1, max_value=43200),
    activation_mode=st.sampled_from(VALID_MODES),
)
@settings(max_examples=12, deadline=None)
def test_generated_vouchers_start_unused(quantity, duration_minutes, activation_mode):
    resp, _ = h.run(_create_scenario(quantity, duration_minutes, activation_mode))
    assert resp.status_code == 201, resp.text
    vouchers = resp.json()["vouchers"]
    assert len(vouchers) == quantity
    assert all(v["status"] == "unused" for v in vouchers)


# ---------------------------------------------------------------------------
# Property 5: Invalid activation mode rejected (Req 1.9)
# ---------------------------------------------------------------------------


@given(mode=st.text(max_size=40).filter(lambda s: s not in VALID_MODES))
@settings(max_examples=50, deadline=None)
def test_invalid_activation_mode_rejected(mode):
    resp, count = h.run(_create_scenario(1, 60, mode))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_activation_mode"
    assert count == 0


# ---------------------------------------------------------------------------
# Property 15: Paginated voucher list completeness (Req 6.4)
# ---------------------------------------------------------------------------

_PAGE_SIZE = 25
_REQUIRED_FIELDS = {
    "code",
    "status",
    "activation_mode",
    "duration_minutes",
    "expires_at",
    "created_at",
    "consumed_at",
    "revoked_at",
}


async def _list_scenario(n):
    await h.reset_tables()

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    async with h.get_session_factory()() as session:
        for i in range(n):
            session.add(
                h.new_voucher(
                    code=f"CODE{i:08d}",
                    created_at=base + timedelta(seconds=i),
                )
            )
        await session.commit()

    app = h.build_app()
    codes: list[str] = []
    fields_ok = True
    pages = (n + _PAGE_SIZE - 1) // _PAGE_SIZE
    total = None
    for page in range(1, pages + 1):
        resp = await h.request(app, "GET", VOUCHERS_URL, params={"page": page})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        total = body["total"]
        assert len(body["items"]) <= _PAGE_SIZE
        for item in body["items"]:
            fields_ok = fields_ok and _REQUIRED_FIELDS.issubset(item.keys())
            codes.append(item["code"])
    return total, codes, fields_ok


@given(n=st.integers(min_value=1, max_value=100))
@settings(max_examples=12, deadline=None)
def test_paginated_voucher_list_completeness(n):
    total, codes, fields_ok = h.run(_list_scenario(n))
    assert total == n
    assert len(codes) == n
    assert len(set(codes)) == n
    assert fields_ok


# ---------------------------------------------------------------------------
# Property 19: Input validation returns field-level errors (Req 11.3)
# ---------------------------------------------------------------------------

# Values that Pydantic cannot coerce to an integer.
_BAD_INT = st.one_of(
    st.text(min_size=1, alphabet=st.characters(whitelist_categories=("L",))),
    st.lists(st.integers()),
    st.dictionaries(st.text(min_size=1), st.integers()),
)
_QUANTITY_FIELD = st.one_of(st.integers(min_value=1, max_value=500), _BAD_INT)
_DURATION_FIELD = st.one_of(st.integers(min_value=1, max_value=43200), _BAD_INT)


async def _validation_scenario(quantity, duration_minutes):
    await h.reset_tables()
    app = h.build_app()
    resp = await h.request(
        app,
        "POST",
        VOUCHERS_URL,
        json={"quantity": quantity, "duration_minutes": duration_minutes},
    )
    return resp.status_code, resp.json()


@given(quantity=_QUANTITY_FIELD, duration_minutes=_DURATION_FIELD)
@settings(max_examples=50, deadline=None)
def test_input_validation_returns_field_level_errors(quantity, duration_minutes):
    quantity_valid = isinstance(quantity, int)
    duration_valid = isinstance(duration_minutes, int)
    if quantity_valid and duration_valid:
        return  # The property only concerns requests with an invalid field.

    status, body = h.run(_validation_scenario(quantity, duration_minutes))
    assert status == 422
    locations = [str(err.get("loc", "")) for err in body["detail"]]
    if not quantity_valid:
        assert any("quantity" in loc for loc in locations)
    if not duration_valid:
        assert any("duration_minutes" in loc for loc in locations)

