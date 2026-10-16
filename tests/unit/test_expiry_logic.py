"""Unit tests for expiry logic (Req 3.1, 3.2, 3.3, 3.4)."""

import uuid
from datetime import datetime, timedelta, timezone

from app.models.voucher import Voucher
from app.services.voucher_service import (
    compute_pre_activated_expiry,
    set_use_activated_expiry,
)


class _FakeVoucher:
    def __init__(self, duration_minutes: int):
        self.duration_minutes = duration_minutes


def test_compute_pre_activated_expiry():
    """The pre-activated expiry is now + duration (within a small tolerance)."""
    before = datetime.now(timezone.utc)
    expires = compute_pre_activated_expiry(90)
    after = datetime.now(timezone.utc)
    assert before + timedelta(minutes=90) <= expires <= after + timedelta(minutes=90)


def test_set_use_activated_expiry():
    """The use-activated expiry is consumed_at + duration, exactly."""
    voucher = _FakeVoucher(duration_minutes=120)
    consumed_at = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    set_use_activated_expiry(voucher, consumed_at)
    assert voucher.expires_at == consumed_at + timedelta(minutes=120)


def _make_voucher(**kwargs) -> Voucher:
    defaults = dict(
        id=uuid.uuid4(),
        code="AB3CD5EF7GH8",
        status="unused",
        activation_mode="use-activated",
        duration_minutes=60,
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        consumed_at=None,
        revoked_at=None,
        created_by=None,
    )
    defaults.update(kwargs)
    v = Voucher.__new__(Voucher)
    v.__dict__.update(defaults)
    return v


def test_is_expired_true_when_past():
    v = _make_voucher(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert v.is_expired() is True


def test_is_expired_false_when_future():
    v = _make_voucher(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert v.is_expired() is False


def test_is_expired_false_when_none():
    v = _make_voucher(expires_at=None)
    assert v.is_expired() is False


def test_is_expired_handles_naive_timestamps():
    """Naive stored timestamps are treated as UTC (asyncpg/SQLite tolerance)."""
    v = _make_voucher(expires_at=datetime.utcnow() - timedelta(seconds=1))
    assert v.is_expired() is True
