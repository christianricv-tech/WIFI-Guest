"""Properties 9-11: expiry logic.

Feature: aruba-voucher-backend.

- Property 9:  Pre-activated expiry correctness  (Req 3.1)
- Property 10: Use-activated expiry correctness  (Req 3.2)
- Property 11: Use-activated voucher not pre-expired (Req 3.3)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.voucher import Voucher
from app.services.voucher_service import (
    compute_pre_activated_expiry,
    set_use_activated_expiry,
)

_DURATIONS = st.integers(min_value=1, max_value=43200)


# ---------------------------------------------------------------------------
# Property 9: Pre-activated expiry correctness
# ---------------------------------------------------------------------------


@given(duration_minutes=_DURATIONS)
@settings(max_examples=100, deadline=None)
def test_pre_activated_expiry_correctness(duration_minutes):
    before = datetime.now(timezone.utc)
    expires_at = compute_pre_activated_expiry(duration_minutes)
    after = datetime.now(timezone.utc)

    delta = timedelta(minutes=duration_minutes)
    # expires_at == now() + duration, within the tight [before, after] bracket.
    assert before + delta <= expires_at <= after + delta


# ---------------------------------------------------------------------------
# Property 10: Use-activated expiry correctness
# ---------------------------------------------------------------------------


@given(
    consumed_at=st.datetimes(timezones=st.just(timezone.utc)),
    duration_minutes=_DURATIONS,
)
@settings(max_examples=100, deadline=None)
def test_use_activated_expiry_correctness(consumed_at, duration_minutes):
    voucher = SimpleNamespace(duration_minutes=duration_minutes)
    set_use_activated_expiry(voucher, consumed_at)

    assert voucher.expires_at == consumed_at + timedelta(minutes=duration_minutes)


# ---------------------------------------------------------------------------
# Property 11: Use-activated voucher not pre-expired
# ---------------------------------------------------------------------------


@given(age=st.timedeltas(min_value=timedelta(seconds=0), max_value=timedelta(days=3650)))
@settings(max_examples=100, deadline=None)
def test_use_activated_voucher_not_pre_expired(age):
    # A never-used, use-activated voucher has expires_at = None and must never
    # be treated as expired, no matter how long ago it was created.
    voucher = Voucher(
        code="AB3CD5EF7GH8",
        status="unused",
        activation_mode="use-activated",
        duration_minutes=60,
        created_at=datetime.now(timezone.utc) - age,
        expires_at=None,
    )

    assert voucher.is_expired() is False
