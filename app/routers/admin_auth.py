"""
app/routers/admin_auth.py — Admin login and logout (JSON API).

Requirements: 6.3, 6.10
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import JWT_EXPIRE_SECONDS
from app.database import get_db
from app.models.admin import Admin
from app.services.auth_service import (
    create_access_token,
    get_current_admin,
    invalidate_session,
    verify_password,
)

router = APIRouter(prefix="/admin", tags=["admin-auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate an admin and return a signed JWT.

    Returns HTTP 401 with a generic message for both unknown usernames and
    incorrect passwords so callers cannot distinguish which field was wrong
    (Req 6.3).
    """
    result = await db.execute(select(Admin).where(Admin.username == body.username))
    admin = result.scalar_one_or_none()

    if admin is None or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(str(admin.id), admin.username)

    admin.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_SECONDS,
    }


@router.post("/logout")
async def logout(admin: dict = Depends(get_current_admin)):
    """Invalidate the current admin session (Req 6.10)."""
    invalidate_session(admin.get("jti", ""))
    return {"detail": "Logged out successfully"}
