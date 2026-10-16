"""
app/routers/admin_vouchers.py — Admin voucher management API.

Endpoints (all require a valid admin JWT):
    POST   /admin/vouchers             generate a batch (Req 1.x, 6.6)
    GET    /admin/vouchers             paginated list, 25/page (Req 6.4, 6.5)
    GET    /admin/vouchers/{code}      single lookup (Req 6.4)
    DELETE /admin/vouchers/{code}/revoke   revoke (Req 5.1, 5.3, 5.4, 6.7, 6.8)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.voucher import Voucher
from app.services.auth_service import get_current_admin
from app.services.voucher_service import generate_vouchers_batch

router = APIRouter(prefix="/admin/vouchers", tags=["admin-vouchers"])

PAGE_SIZE = 25

# Preset label → duration in minutes (Req 1.4)
DURATION_PRESETS: dict[str, int] = {
    "1h": 60,
    "4h": 240,
    "8h": 480,
    "24h": 1440,
    "7d": 10080,
    "30d": 43200,
}

VALID_ACTIVATION_MODES = ("use-activated", "pre-activated")


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class VoucherGenerateRequest(BaseModel):
    quantity: int = 1
    duration_minutes: int | None = None
    duration_preset: str | None = None
    activation_mode: str = "use-activated"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def serialize_voucher(voucher: Voucher) -> dict:
    """Return the API representation of a voucher row."""
    return {
        "code": voucher.code,
        "status": voucher.status,
        "activation_mode": voucher.activation_mode,
        "duration_minutes": voucher.duration_minutes,
        "expires_at": voucher.expires_at,
        "created_at": voucher.created_at,
        "consumed_at": voucher.consumed_at,
        "revoked_at": voucher.revoked_at,
    }


def _resolve_duration(body: VoucherGenerateRequest) -> int:
    """Validate quantity/duration inputs and return the resolved minutes.

    Raises HTTP 400 with a machine-readable detail for any constraint
    violation (Req 1.3, 1.5).
    """
    if body.quantity < 1 or body.quantity > 500:
        raise HTTPException(status_code=400, detail="quantity_out_of_range")

    has_minutes = body.duration_minutes is not None
    has_preset = body.duration_preset is not None

    if has_minutes == has_preset:
        # Exactly one of duration_minutes / duration_preset must be supplied.
        raise HTTPException(status_code=400, detail="duration_required")

    if has_preset:
        preset = body.duration_preset
        if preset not in DURATION_PRESETS:
            raise HTTPException(status_code=400, detail="invalid_duration_preset")
        return DURATION_PRESETS[preset]

    assert body.duration_minutes is not None
    if body.duration_minutes < 1 or body.duration_minutes > 43200:
        raise HTTPException(status_code=400, detail="duration_out_of_range")
    return body.duration_minutes


# ---------------------------------------------------------------------------
# POST /admin/vouchers
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
@router.post("/", status_code=201, include_in_schema=False)
async def create_vouchers(
    body: VoucherGenerateRequest,
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Generate a batch of vouchers (Req 1.1–1.9, 6.6)."""
    if body.activation_mode not in VALID_ACTIVATION_MODES:
        raise HTTPException(status_code=400, detail="invalid_activation_mode")

    duration_minutes = _resolve_duration(body)

    vouchers = await generate_vouchers_batch(
        quantity=body.quantity,
        duration_minutes=duration_minutes,
        activation_mode=body.activation_mode,
        admin_id=admin.get("sub"),
        db=db,
    )

    return {
        "generated": len(vouchers),
        "vouchers": [serialize_voucher(v) for v in vouchers],
    }


# ---------------------------------------------------------------------------
# GET /admin/vouchers
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/", include_in_schema=False)
async def list_vouchers(
    page: int = Query(1, ge=1),
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return a paginated voucher list with an optional status filter.

    ``status='expired'`` is computed dynamically: ``status='unused'`` vouchers
    whose ``expires_at`` is in the past (Req 6.4, 6.5).
    """
    base = select(Voucher)
    count_q = select(func.count()).select_from(Voucher)

    if status == "expired":
        condition = (Voucher.status == "unused") & (Voucher.expires_at < func.now())
        base = base.where(condition)
        count_q = count_q.where(condition)
    elif status:
        base = base.where(Voucher.status == status)
        count_q = count_q.where(Voucher.status == status)

    total = (await db.execute(count_q)).scalar_one()

    base = (
        base.order_by(Voucher.created_at.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    )
    rows = (await db.execute(base)).scalars().all()

    return {
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total,
        "items": [serialize_voucher(v) for v in rows],
    }


# ---------------------------------------------------------------------------
# GET /admin/vouchers/{code}
# ---------------------------------------------------------------------------


@router.get("/{code}")
async def get_voucher(
    code: str,
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Retrieve a single voucher by code (case-insensitive) (Req 6.4)."""
    result = await db.execute(
        select(Voucher).where(func.upper(Voucher.code) == code.upper())
    )
    voucher = result.scalar_one_or_none()
    if voucher is None:
        raise HTTPException(status_code=404, detail="voucher_not_found")
    return serialize_voucher(voucher)


# ---------------------------------------------------------------------------
# DELETE /admin/vouchers/{code}/revoke
# ---------------------------------------------------------------------------


@router.delete("/{code}/revoke")
async def revoke_voucher(
    code: str,
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Revoke an unused voucher (Req 5.1, 5.3, 5.4, 6.7, 6.8)."""
    result = await db.execute(
        select(Voucher).where(func.upper(Voucher.code) == code.upper())
    )
    voucher = result.scalar_one_or_none()

    if voucher is None:
        raise HTTPException(status_code=404, detail="voucher_not_found")

    if voucher.status in ("consumed", "revoked"):
        raise HTTPException(
            status_code=409,
            detail=f"Voucher cannot be revoked: current status is '{voucher.status}'",
        )

    voucher.status = "revoked"
    voucher.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(voucher)

    return serialize_voucher(voucher)
