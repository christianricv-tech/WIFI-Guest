"""
app/routers/radius.py — RADIUS-facing endpoints.

Two endpoints are exposed for FreeRADIUS integration:

* ``POST /radius/validate``  — the rlm_rest path. Performs atomic, single-use
  voucher validation using ``SELECT ... FOR UPDATE`` and returns an explicit
  ``accept``/``reject`` result. Always returns HTTP 200 (see design decision).
* ``POST /radius/post-auth`` — the rlm_sql path. Marks a voucher consumed
  after FreeRADIUS has already granted access via the DB view.

Requirements: 2.1–2.8, 4.1–4.4, 7.1, 9.3, 9.5, 10.5, 11.6

Note: ``from __future__ import annotations`` is intentionally omitted. The
slowapi ``@limiter.limit`` decorator wraps the endpoint, and FastAPI resolves
string annotations using the wrapper's ``__globals__`` (slowapi's module), so
deferred annotations would make the pydantic body model unresolvable and
FastAPI would treat it as a query parameter.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.voucher import Voucher
from app.services.audit_service import write_audit_log
from app.services.voucher_service import set_use_activated_expiry

router = APIRouter(prefix="/radius", tags=["radius"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RadiusValidateRequest(BaseModel):
    """Body POSTed by FreeRADIUS rlm_rest for an Access-Request."""

    username: str
    password: str
    client_mac: str | None = None


class RadiusPostAuthRequest(BaseModel):
    """Body POSTed by FreeRADIUS rlm_sql after a successful Access-Accept."""

    username: str
    client_mac: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_mac(client_mac: str | None) -> str | None:
    """Treat empty/whitespace MAC strings as ``None``.

    FreeRADIUS renders an absent ``Calling-Station-Id`` as an empty string in
    the JSON template; the audit_logs.client_mac column is nullable and should
    store NULL in that case.
    """
    if client_mac is None:
        return None
    stripped = client_mac.strip()
    return stripped or None


# ---------------------------------------------------------------------------
# POST /radius/validate  (rlm_rest)
# ---------------------------------------------------------------------------


@router.post("/validate")
@limiter.limit("60/minute")
async def validate_voucher(
    request: Request,
    body: RadiusValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Atomically validate a voucher and mark it consumed on success.

    The row is locked with ``SELECT ... FOR UPDATE`` inside the implicit
    transaction so that concurrent requests for the same code are serialised:
    exactly one observes ``status='unused'`` (Req 4.3, 4.4).

    Validation order (Req 2.3–2.6):
        1. not found        → reject ``not_found``
        2. status consumed  → reject ``consumed``
        3. status revoked   → reject ``revoked``
        4. expired          → reject ``expired``
        5. otherwise        → accept

    Always returns HTTP 200; the JSON ``result`` field drives the FreeRADIUS
    outcome (Req 2.8, 9.5).
    """
    now = datetime.now(timezone.utc)
    client_mac = _normalize_mac(body.client_mac)

    # Acquire the row lock (case-insensitive code match — Req 9.3).
    result = await db.execute(
        select(Voucher)
        .where(func.upper(Voucher.code) == body.username.upper())
        .with_for_update()
    )
    voucher = result.scalar_one_or_none()

    reason: str | None
    if voucher is None:
        reason = "not_found"
    elif voucher.status == "consumed":
        reason = "consumed"
    elif voucher.status == "revoked":
        reason = "revoked"
    elif voucher.is_expired():
        reason = "expired"
    else:
        reason = None

    if reason is None:
        # Accept path — mutate within the same locked transaction and commit.
        voucher.status = "consumed"
        voucher.consumed_at = now
        if voucher.activation_mode == "use-activated":
            set_use_activated_expiry(voucher, now)
        await db.commit()
        await write_audit_log(db, body.username, client_mac, "accepted", None)
        return {"result": "accept"}

    # Reject path — release the lock; no voucher state changes (Req 2.3–2.6).
    await db.rollback()
    await write_audit_log(db, body.username, client_mac, "rejected", reason)
    return {"result": "reject", "reason": reason}


# ---------------------------------------------------------------------------
# POST /radius/post-auth  (rlm_sql)
# ---------------------------------------------------------------------------


@router.post("/post-auth")
async def post_auth(
    body: RadiusPostAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark a voucher consumed after FreeRADIUS granted access (rlm_sql path).

    The DB view already determined validity, so this endpoint only records the
    consumption. It is idempotent: a voucher that is already consumed returns
    HTTP 200 without an error. A voucher that does not exist returns HTTP 404.

    Requirements: 10.5
    """
    now = datetime.now(timezone.utc)
    client_mac = _normalize_mac(body.client_mac)

    result = await db.execute(
        select(Voucher)
        .where(func.upper(Voucher.code) == body.username.upper())
        .with_for_update()
    )
    voucher = result.scalar_one_or_none()

    if voucher is None:
        await db.rollback()
        await write_audit_log(db, body.username, client_mac, "rejected", "not_found", source="rlm_sql")
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="voucher_not_found")

    if voucher.status == "consumed":
        # Idempotent: already consumed, nothing to do.
        await db.rollback()
        return {"detail": "Voucher marked as consumed"}

    voucher.status = "consumed"
    voucher.consumed_at = now
    if voucher.activation_mode == "use-activated":
        set_use_activated_expiry(voucher, now)
    await db.commit()

    await write_audit_log(db, body.username, client_mac, "accepted", None, source="rlm_sql")
    return {"detail": "Voucher marked as consumed"}
