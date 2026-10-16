"""
app/routers/admin_audit.py — Admin audit log API.

Endpoint (requires a valid admin JWT):
    GET /admin/audit-logs   filterable, paginated audit log, 50/page

Requirements: 7.3, 7.4
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_log import AuditLog
from app.services.auth_service import get_current_admin

router = APIRouter(prefix="/admin", tags=["admin-audit"])

PAGE_SIZE = 50


def _serialize(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "logged_at": entry.logged_at,
        "voucher_code": entry.voucher_code,
        "client_mac": entry.client_mac,
        "outcome": entry.outcome,
        "rejection_reason": entry.rejection_reason,
        "source": entry.source,
    }


@router.get("/audit-logs")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    voucher_code: str | None = None,
    client_mac: str | None = None,
    outcome: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return audit log entries matching all supplied filters.

    Filters: ``voucher_code`` (exact), ``client_mac`` (exact), ``outcome``
    (accepted/rejected), and an inclusive UTC date range. Ordered newest-first,
    50 entries per page (Req 7.3, 7.4).
    """
    filters = []
    if voucher_code:
        filters.append(AuditLog.voucher_code == voucher_code)
    if client_mac:
        filters.append(AuditLog.client_mac == client_mac)
    if outcome:
        filters.append(AuditLog.outcome == outcome)
    if date_from is not None:
        start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        filters.append(AuditLog.logged_at >= start)
    if date_to is not None:
        end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
        filters.append(AuditLog.logged_at <= end)

    base = select(AuditLog)
    count_q = select(func.count()).select_from(AuditLog)
    if filters:
        base = base.where(*filters)
        count_q = count_q.where(*filters)

    total = (await db.execute(count_q)).scalar_one()

    base = (
        base.order_by(AuditLog.logged_at.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    )
    rows = (await db.execute(base)).scalars().all()

    return {
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total,
        "items": [_serialize(r) for r in rows],
    }
