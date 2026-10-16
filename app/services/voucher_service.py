"""Voucher service — code generation, batch creation, and expiry helpers.

Tasks implemented:
  4.1  generate_voucher_code()
  4.3  generate_vouchers_batch()
  4.8  set_use_activated_expiry(), compute_pre_activated_expiry()
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voucher import Voucher

# ---------------------------------------------------------------------------
# 4.1 — Character set and code generation
# ---------------------------------------------------------------------------

# Uppercase A–Z minus ambiguous I and O, plus digits 2–9 (no 0 or 1).
# Total: 24 letters + 8 digits = 32 characters.
ALLOWED_CHARS: str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

assert len(ALLOWED_CHARS) == 32, "Character set must contain exactly 32 characters"  # noqa: S101


def generate_voucher_code() -> str:
    """Return a cryptographically random 12-character voucher code.

    Uses :func:`secrets.choice` to sample from :data:`ALLOWED_CHARS` so that
    every character is chosen with uniform probability from a
    cryptographically secure source of randomness.

    Requirements: 1.1
    """
    return "".join(secrets.choice(ALLOWED_CHARS) for _ in range(12))


# ---------------------------------------------------------------------------
# 4.3 — Batch generation with collision retry
# ---------------------------------------------------------------------------

_MAX_COLLISION_RETRIES: int = 5


async def generate_vouchers_batch(
    quantity: int,
    duration_minutes: int,
    activation_mode: str,
    admin_id,
    db: AsyncSession,
) -> List[Voucher]:
    """Generate *quantity* vouchers and persist them in a single commit.

    Collision handling
    ------------------
    For each voucher the function attempts to find a unique code.  If the
    first attempt produces a code that already exists in the database it
    retries up to :data:`_MAX_COLLISION_RETRIES` times.  If no unique code
    is obtained within those attempts an :class:`~fastapi.HTTPException` with
    status 500 is raised immediately without persisting *any* vouchers created
    so far (the caller's transaction is not committed).

    Activation modes
    ----------------
    * ``"pre-activated"`` — ``expires_at`` is set to ``now() + duration``
      at generation time.
    * ``"use-activated"`` — ``expires_at`` is left as ``None``; it is
      populated when the voucher is first consumed.

    Requirements: 1.2, 1.3, 1.6, 1.7, 1.8, 3.1
    """
    # The admin id arrives as a string from the JWT "sub" claim; the FK column
    # is UUID(as_uuid=True), which expects a uuid.UUID instance.
    if isinstance(admin_id, str):
        admin_id = uuid.UUID(admin_id)

    vouchers: List[Voucher] = []

    for _ in range(quantity):
        code = await _generate_unique_code(db)

        # Determine expiry at creation for pre-activated vouchers
        expires_at: datetime | None = None
        if activation_mode == "pre-activated":
            expires_at = compute_pre_activated_expiry(duration_minutes)

        voucher = Voucher(
            code=code,
            status="unused",
            activation_mode=activation_mode,
            duration_minutes=duration_minutes,
            expires_at=expires_at,
            created_by=admin_id,
        )
        db.add(voucher)
        vouchers.append(voucher)

    await db.commit()

    # Refresh all objects so callers get populated server-default fields
    for voucher in vouchers:
        await db.refresh(voucher)

    return vouchers


async def _generate_unique_code(db: AsyncSession) -> str:
    """Attempt to produce a code that does not already exist in the DB.

    Raises
    ------
    HTTPException
        HTTP 500 with detail ``"code_generation_failed"`` when all
        :data:`_MAX_COLLISION_RETRIES` attempts result in a collision.
    """
    for _ in range(_MAX_COLLISION_RETRIES):
        candidate = generate_voucher_code()
        result = await db.execute(
            select(Voucher).where(Voucher.code == candidate)
        )
        if result.scalars().first() is None:
            return candidate

    raise HTTPException(
        status_code=500,
        detail="code_generation_failed",
    )


# ---------------------------------------------------------------------------
# 4.8 — Expiry logic helpers
# ---------------------------------------------------------------------------


def set_use_activated_expiry(voucher: Voucher, consumed_at: datetime) -> None:
    """Set ``voucher.expires_at`` based on *consumed_at* and the voucher's
    configured duration.

    Called inside the RADIUS validate transaction immediately after marking a
    ``use-activated`` voucher as consumed so that the expiry clock starts from
    the moment of first use.

    Requirements: 3.2, 3.5
    """
    voucher.expires_at = consumed_at + timedelta(minutes=voucher.duration_minutes)


def compute_pre_activated_expiry(duration_minutes: int) -> datetime:
    """Return the expiry timestamp for a ``pre-activated`` voucher.

    The expiry is calculated as the current UTC time plus *duration_minutes*.
    Called at generation time so the clock starts from the moment the voucher
    is created.

    Returns a timezone-aware UTC datetime so it can be bound to the
    ``TIMESTAMPTZ`` columns (Req 3.5).

    Requirements: 3.1, 3.5
    """
    return datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
