"""
app/services/auth_service.py — Password hashing and JWT operations.

Provides bcrypt password hashing/verification via passlib and JWT
creation/decoding via python-jose. Used by the admin auth endpoints and
the admin session dependency.

Requirements: 6.1, 6.2, 11.2
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import JWT_EXPIRE_SECONDS, JWT_SECRET_KEY

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

# bcrypt with cost factor 12 — exceeds the minimum of 10 required by Req 11.2.
# deprecated="auto" ensures that if a hash stored with an older cost factor is
# verified, passlib will transparently flag it for rehashing.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)

_JWT_ALGORITHM = "HS256"


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of *plaintext*.

    The returned string begins with ``$2b$`` and encodes the cost factor (12)
    and the salt. Plaintext passwords are never stored.
    """
    return pwd_context.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    """Return ``True`` if *plaintext* matches the stored *hashed* password."""
    return pwd_context.verify(plaintext, hashed)


# ---------------------------------------------------------------------------
# JWT creation and decoding
# ---------------------------------------------------------------------------


def create_access_token(sub: str, username: str) -> str:
    """Create a signed JWT for an admin session.

    The payload contains:
    - ``sub``      — admin UUID (string form)
    - ``username`` — admin username
    - ``iat``      — issued-at timestamp (UTC epoch seconds)
    - ``exp``      — expiry timestamp (``iat`` + ``JWT_EXPIRE_SECONDS``)
    - ``jti``      — unique token ID (UUID4); used for logout blocklisting

    Args:
        sub:      The admin's UUID as a string.
        username: The admin's username.

    Returns:
        A signed HS256 JWT string.
    """
    now = datetime.now(tz=timezone.utc)
    iat = int(now.timestamp())
    exp = iat + JWT_EXPIRE_SECONDS
    jti = str(uuid.uuid4())

    payload: dict = {
        "sub": sub,
        "username": username,
        "iat": iat,
        "exp": exp,
        "jti": jti,
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=_JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT, returning its payload as a plain dict.

    Validates the signature and the ``exp`` claim. Any failure — including an
    expired token, an invalid signature, or a malformed token — raises HTTP 401.

    Args:
        token: The raw JWT string (without the ``Bearer `` prefix).

    Returns:
        The decoded payload dict containing at least ``sub``, ``username``,
        ``iat``, ``exp``, and ``jti``.

    Raises:
        HTTPException: 401 with ``detail="Invalid or expired token"`` on any
            decode or validation failure.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[_JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ---------------------------------------------------------------------------
# OAuth2 scheme
# ---------------------------------------------------------------------------

from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/admin/login")

# ---------------------------------------------------------------------------
# In-memory JTI blocklist and idle-timeout tracker
# ---------------------------------------------------------------------------

# Requirements: 6.10, 6.11

BLOCKLIST: set[str] = set()          # JTI values of invalidated sessions
LAST_SEEN: dict[str, datetime] = {}  # maps JTI → last-activity timestamp (UTC)


def check_session(jti: str, now: datetime) -> None:
    """Validate an active session by its JTI.

    Checks that the session has not been explicitly invalidated (blocklist)
    and that the admin has not been idle longer than ``JWT_EXPIRE_SECONDS``.
    On success, refreshes the ``LAST_SEEN`` timestamp for the JTI.

    Args:
        jti: The JWT ID extracted from the decoded token payload.
        now: The current UTC datetime used for idle-timeout comparison.

    Raises:
        HTTPException: 401 if the session is blocklisted.
        HTTPException: 401 if the session has been idle too long.
    """
    if jti in BLOCKLIST:
        raise HTTPException(status_code=401, detail="Session has been invalidated")
    if jti in LAST_SEEN and (now - LAST_SEEN[jti]).total_seconds() > JWT_EXPIRE_SECONDS:
        raise HTTPException(status_code=401, detail="Session idle timeout")
    LAST_SEEN[jti] = now


def invalidate_session(jti: str) -> None:
    """Invalidate an admin session on logout.

    Adds the JTI to the in-memory blocklist and removes it from the
    idle-timeout tracker so that any subsequent request bearing this
    token is immediately rejected.

    Args:
        jti: The JWT ID to invalidate.
    """
    BLOCKLIST.add(jti)
    LAST_SEEN.pop(jti, None)


async def get_current_admin(token: str = Depends(oauth2_scheme)) -> dict:
    """FastAPI dependency that authenticates and returns the current admin.

    Decodes the Bearer token, verifies it has not been blocklisted or
    idled out, refreshes the last-seen timestamp, and returns the full
    JWT payload so downstream handlers can read ``sub``, ``username``,
    ``jti``, etc.

    Args:
        token: The raw JWT provided by the ``OAuth2PasswordBearer`` scheme.

    Returns:
        The decoded JWT payload dict.

    Raises:
        HTTPException: 401 for invalid/expired tokens or invalidated sessions.
    """
    payload = decode_token(token)
    jti: str = payload.get("jti", "")
    check_session(jti, datetime.now(tz=timezone.utc))
    return payload
