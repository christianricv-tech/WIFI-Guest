"""Unit tests for the Voucher ORM model helper methods."""

import uuid
from datetime import datetime, timedelta

import pytest

from app.models.voucher import Voucher


def _make_voucher(**kwargs) -> Voucher:
    """Return a Voucher instance with sensible defaults, overriding via kwargs."""
    defaults = dict(
        id=uuid.uuid4(),
        code="AB3CD5EF7GH8",
        status="unused",
        activation_mode="use-activated",
        duration_minutes=60,
        created_at=datetime.utcnow(),
        expires_at=None,
        consumed_at=None,
        revoked_at=None,
        created_by=None,
    )
    defaults.update(kwargs)
    v = Voucher.__new__(Voucher)
    v.__dict__.update(defaults)
    return v


class TestIsExpired:
    """Tests for Voucher.is_expired()."""

    def test_none_expires_at_returns_false(self):
        """A use-activated voucher with no expiry set is never expired."""
        v = _make_voucher(expires_at=None)
        assert v.is_expired() is False

    def test_future_expires_at_returns_false(self):
        """A voucher that expires in the future is not expired."""
        v = _make_voucher(expires_at=datetime.utcnow() + timedelta(hours=1))
        assert v.is_expired() is False

    def test_past_expires_at_returns_true(self):
        """A voucher whose expiry time has already passed is expired."""
        v = _make_voucher(expires_at=datetime.utcnow() - timedelta(seconds=1))
        assert v.is_expired() is True

    def test_well_past_expires_at_returns_true(self):
        """A voucher that expired long ago is also expired."""
        v = _make_voucher(expires_at=datetime.utcnow() - timedelta(days=7))
        assert v.is_expired() is True

    def test_pre_activated_future_returns_false(self):
        """A pre-activated voucher with a future expiry is not expired."""
        v = _make_voucher(
            activation_mode="pre-activated",
            expires_at=datetime.utcnow() + timedelta(minutes=30),
        )
        assert v.is_expired() is False

    def test_pre_activated_past_returns_true(self):
        """A pre-activated voucher past its expiry is expired."""
        v = _make_voucher(
            activation_mode="pre-activated",
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        assert v.is_expired() is True
