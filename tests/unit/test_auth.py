"""Unit tests for auth: hashing, JWT, and session management (Req 6.10, 6.11, 11.2)."""

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import JWT_EXPIRE_SECONDS, JWT_SECRET_KEY
from app.services.auth_service import (
    BLOCKLIST,
    LAST_SEEN,
    check_session,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# Password hashing (Req 11.2)
# ---------------------------------------------------------------------------


def test_hash_and_verify_roundtrip():
    hashed = hash_password("s3cret-password")
    assert verify_password("s3cret-password", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_hash_is_bcrypt_with_cost_at_least_12():
    hashed = hash_password("another-password")
    assert hashed.startswith("$2b$")
    # Format: $2b$<cost>$<salt+hash>
    cost = int(hashed.split("$")[2])
    assert cost >= 12


def test_plaintext_never_appears_in_hash():
    plaintext = "unique-plaintext-value"
    assert plaintext not in hash_password(plaintext)


# ---------------------------------------------------------------------------
# JWT creation / decoding (Req 6.1, 6.2)
# ---------------------------------------------------------------------------


def test_access_token_roundtrip():
    token = create_access_token("admin-uuid", "admin")
    payload = decode_token(token)
    assert payload["sub"] == "admin-uuid"
    assert payload["username"] == "admin"
    assert "jti" in payload
    assert payload["exp"] - payload["iat"] == JWT_EXPIRE_SECONDS


def test_expired_token_raises_401():
    now = int(time.time())
    expired = jwt.encode(
        {
            "sub": "uuid",
            "username": "admin",
            "iat": now - 7200,
            "exp": now - 3600,
            "jti": "expired-jti",
        },
        JWT_SECRET_KEY,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        decode_token(expired)
    assert exc.value.status_code == 401


def test_tampered_token_raises_401():
    token = create_access_token("uuid", "admin")
    with pytest.raises(HTTPException) as exc:
        decode_token(token + "tampered")
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Session management (Req 6.10, 6.11)
# ---------------------------------------------------------------------------


def test_blocklisted_jti_raises_401():
    jti = "unit-test-blocklisted-jti"
    BLOCKLIST.add(jti)
    try:
        with pytest.raises(HTTPException) as exc:
            check_session(jti, datetime.now(timezone.utc))
        assert exc.value.status_code == 401
    finally:
        BLOCKLIST.discard(jti)
        LAST_SEEN.pop(jti, None)


def test_idle_timeout_raises_401():
    jti = "unit-test-idle-jti"
    LAST_SEEN[jti] = datetime.now(timezone.utc) - timedelta(seconds=JWT_EXPIRE_SECONDS + 5)
    try:
        with pytest.raises(HTTPException) as exc:
            check_session(jti, datetime.now(timezone.utc))
        assert exc.value.status_code == 401
    finally:
        LAST_SEEN.pop(jti, None)


def test_active_session_refreshes_last_seen():
    jti = "unit-test-active-jti"
    LAST_SEEN.pop(jti, None)
    now = datetime.now(timezone.utc)
    try:
        check_session(jti, now)  # should not raise
        assert LAST_SEEN[jti] == now
    finally:
        LAST_SEEN.pop(jti, None)


def test_invalidate_session_blocks_and_clears():
    jti = "unit-test-invalidate-jti"
    LAST_SEEN[jti] = datetime.now(timezone.utc)
    try:
        from app.services.auth_service import invalidate_session

        invalidate_session(jti)
        assert jti in BLOCKLIST
        assert jti not in LAST_SEEN
    finally:
        BLOCKLIST.discard(jti)
        LAST_SEEN.pop(jti, None)
