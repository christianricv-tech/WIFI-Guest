"""
app/main.py — FastAPI application factory.

Wires together the routers, rate limiter, startup admin seeding, logging, and
Jinja2 template directory.

Requirements: 8.3, 11.6, 12.1, 12.7
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select

from app.config import ADMIN_PASSWORD, ADMIN_USERNAME, LOG_LEVEL
from app.database import AsyncSessionLocal
from app.limiter import limiter
from app.models.admin import Admin
from app.routers import (
    admin_audit,
    admin_auth,
    admin_ui,
    admin_vouchers,
    radius,
)
from app.services.auth_service import hash_password

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Aruba Voucher Backend", version="1.0.0")

# Rate limiting (Req 11.6)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Routers.
#
# admin_ui is registered before admin_vouchers so the static HTML routes
# GET /admin/vouchers/new and GET /admin/vouchers/download are matched before
# the generic GET /admin/vouchers/{code}. The HTML form endpoints use distinct
# paths (/admin/login/submit, /admin/logout/submit, /admin/audit-logs/view) so
# they never shadow the JSON API endpoints (POST /admin/login, POST
# /admin/logout, GET /admin/audit-logs) defined in admin_auth / admin_audit.
app.include_router(radius.router)
app.include_router(admin_ui.router)
app.include_router(admin_auth.router)
app.include_router(admin_audit.router)
app.include_router(admin_vouchers.router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect the site root to the admin dashboard (Req 6.12)."""
    return RedirectResponse(url="/admin/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Startup — seed the initial admin account
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def seed_admin() -> None:
    """Create the default admin account on first startup.

    If no admin exists, one is created from ``ADMIN_USERNAME`` /
    ``ADMIN_PASSWORD`` with a bcrypt-hashed password. This makes a usable
    account available after a fresh ``alembic upgrade head`` (Req 12.1).
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Admin).limit(1))
        if result.scalars().first() is not None:
            return

        admin = Admin(
            username=ADMIN_USERNAME,
            password_hash=hash_password(ADMIN_PASSWORD),
        )
        db.add(admin)
        await db.commit()
        logger.info("Seeded initial admin account %r", ADMIN_USERNAME)
