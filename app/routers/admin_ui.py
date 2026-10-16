"""
app/routers/admin_ui.py — Server-rendered admin pages (Jinja2).

Auth is cookie-based: the ``access_token`` JWT cookie set on login is read on
each page request. Any missing/invalid session redirects to the login page
(Req 6.1). API-style 401s are not used for HTML routes.

Requirements: 6.1, 6.2, 6.9, 6.10, 6.11, 6.12
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import APP_ENV, JWT_EXPIRE_SECONDS
from app.database import AsyncSessionLocal
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.voucher import Voucher
from app.routers.admin_audit import PAGE_SIZE as AUDIT_PAGE_SIZE
from app.routers.admin_vouchers import (
    DURATION_PRESETS,
    PAGE_SIZE as VOUCHER_PAGE_SIZE,
    VoucherGenerateRequest,
    _resolve_duration,
    serialize_voucher,
)
from app.services.auth_service import (
    check_session,
    create_access_token,
    decode_token,
    invalidate_session,
    verify_password,
)
from app.services.voucher_service import generate_vouchers_batch

router = APIRouter(tags=["admin-ui"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_COOKIE_NAME = "access_token"
_SECURE_COOKIE = APP_ENV == "production"


# ---------------------------------------------------------------------------
# Cookie auth helpers
# ---------------------------------------------------------------------------


def _current_admin(request: Request) -> dict | None:
    """Return the decoded admin payload from the cookie, or ``None``.

    Any decode error, blocklisted JTI, or idle timeout yields ``None`` so the
    caller can redirect to the login page.
    """
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_token(token)
        check_session(payload.get("jti", ""), datetime.now(timezone.utc))
        return payload
    except Exception:
        return None


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/login", status_code=302)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@router.get("/admin/login", include_in_schema=False)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"error": None}
    )


@router.post("/admin/login/submit", include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Admin).where(Admin.username == username))
        admin = result.scalar_one_or_none()

        if admin is None or not verify_password(password, admin.password_hash):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Invalid credentials"},
                status_code=401,
            )

        token = create_access_token(str(admin.id), admin.username)
        admin.last_login_at = datetime.now(timezone.utc)
        await db.commit()

    response = RedirectResponse(url="/admin/dashboard", status_code=302)
    response.set_cookie(
        _COOKIE_NAME,
        token,
        max_age=JWT_EXPIRE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_SECURE_COOKIE,
    )
    return response


@router.post("/admin/logout/submit", include_in_schema=False)
async def logout(request: Request):
    admin = _current_admin(request)
    if admin is not None:
        invalidate_session(admin.get("jti", ""))
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(_COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/admin/dashboard", include_in_schema=False)
async def dashboard(request: Request, page: int = 1, status: str | None = None):
    if _current_admin(request) is None:
        return _login_redirect()

    async with AsyncSessionLocal() as db:
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
            .limit(VOUCHER_PAGE_SIZE)
            .offset((page - 1) * VOUCHER_PAGE_SIZE)
        )
        rows = (await db.execute(base)).scalars().all()
        items = [serialize_voucher(v) for v in rows]

    total_pages = max(1, (total + VOUCHER_PAGE_SIZE - 1) // VOUCHER_PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "items": items,
            "page": page,
            "total": total,
            "total_pages": total_pages,
            "status": status or "",
        },
    )


# ---------------------------------------------------------------------------
# Voucher generation
# ---------------------------------------------------------------------------


@router.get("/admin/vouchers/new", include_in_schema=False)
async def vouchers_new_page(request: Request, error: str | None = None):
    if _current_admin(request) is None:
        return _login_redirect()
    return templates.TemplateResponse(
        request,
        "vouchers_new.html",
        {"presets": list(DURATION_PRESETS.keys()), "error": error, "codes": None},
    )


@router.post("/admin/vouchers/new", include_in_schema=False)
async def vouchers_new_submit(
    request: Request,
    quantity: int = Form(1),
    duration_preset: str = Form(""),
    duration_minutes: str = Form(""),
    activation_mode: str = Form("use-activated"),
):
    admin = _current_admin(request)
    if admin is None:
        return _login_redirect()

    # Resolve duration: custom minutes takes precedence when provided.
    body = VoucherGenerateRequest(
        quantity=quantity,
        duration_minutes=int(duration_minutes) if duration_minutes.strip() else None,
        duration_preset=duration_preset or None,
        activation_mode=activation_mode,
    )

    try:
        duration = _resolve_duration(body)
        if body.activation_mode not in ("use-activated", "pre-activated"):
            raise ValueError("invalid_activation_mode")
    except Exception as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        return templates.TemplateResponse(
            request,
            "vouchers_new.html",
            {
                "presets": list(DURATION_PRESETS.keys()),
                "error": detail,
                "codes": None,
            },
            status_code=400,
        )

    async with AsyncSessionLocal() as db:
        vouchers = await generate_vouchers_batch(
            quantity=body.quantity,
            duration_minutes=duration,
            activation_mode=body.activation_mode,
            admin_id=admin.get("sub"),
            db=db,
        )
        codes = [v.code for v in vouchers]

    return templates.TemplateResponse(
        request,
        "vouchers_new.html",
        {
            "presets": list(DURATION_PRESETS.keys()),
            "error": None,
            "codes": codes,
        },
    )


@router.post("/admin/vouchers/{code}/revoke", include_in_schema=False)
async def revoke_from_ui(request: Request, code: str):
    if _current_admin(request) is None:
        return _login_redirect()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Voucher).where(func.upper(Voucher.code) == code.upper())
        )
        voucher = result.scalar_one_or_none()
        if voucher is not None and voucher.status not in ("consumed", "revoked"):
            voucher.status = "revoked"
            voucher.revoked_at = datetime.now(timezone.utc)
            await db.commit()

    return RedirectResponse(url="/admin/dashboard", status_code=302)


@router.get("/admin/vouchers/download", include_in_schema=False)
async def download_codes(request: Request, codes: str = ""):
    if _current_admin(request) is None:
        return _login_redirect()

    body = "\n".join(c for c in codes.split(",") if c)
    filename = f"vouchers-{datetime.now(timezone.utc):%Y%m%d%H%M%S}.txt"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(body, media_type="text/plain", headers=headers)


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------


@router.get("/admin/audit-logs/view", include_in_schema=False)
async def audit_logs_page(
    request: Request,
    page: int = 1,
    voucher_code: str = "",
    client_mac: str = "",
    outcome: str = "",
    date_from: str = "",
    date_to: str = "",
):
    if _current_admin(request) is None:
        return _login_redirect()

    filters = []
    if voucher_code:
        filters.append(AuditLog.voucher_code == voucher_code)
    if client_mac:
        filters.append(AuditLog.client_mac == client_mac)
    if outcome:
        filters.append(AuditLog.outcome == outcome)
    if date_from:
        start = datetime.combine(date.fromisoformat(date_from), time.min, tzinfo=timezone.utc)
        filters.append(AuditLog.logged_at >= start)
    if date_to:
        end = datetime.combine(date.fromisoformat(date_to), time.max, tzinfo=timezone.utc)
        filters.append(AuditLog.logged_at <= end)

    async with AsyncSessionLocal() as db:
        base = select(AuditLog)
        count_q = select(func.count()).select_from(AuditLog)
        if filters:
            base = base.where(*filters)
            count_q = count_q.where(*filters)
        total = (await db.execute(count_q)).scalar_one()
        base = (
            base.order_by(AuditLog.logged_at.desc())
            .limit(AUDIT_PAGE_SIZE)
            .offset((page - 1) * AUDIT_PAGE_SIZE)
        )
        rows = (await db.execute(base)).scalars().all()
        items = [
            {
                "logged_at": r.logged_at,
                "voucher_code": r.voucher_code,
                "client_mac": r.client_mac,
                "outcome": r.outcome,
                "rejection_reason": r.rejection_reason,
                "source": r.source,
            }
            for r in rows
        ]

    total_pages = max(1, (total + AUDIT_PAGE_SIZE - 1) // AUDIT_PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "audit_logs.html",
        {
            "items": items,
            "page": page,
            "total": total,
            "total_pages": total_pages,
            "voucher_code": voucher_code,
            "client_mac": client_mac,
            "outcome": outcome,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
