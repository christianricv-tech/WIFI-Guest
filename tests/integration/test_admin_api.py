"""Integration tests for the admin API and session lifecycle.

Run against a live PostgreSQL database (``voucher_it`` schema) using the *real*
JWT authentication dependency (no auth override), so the login → generate →
list → revoke → validate path and the idle-timeout behaviour are exercised
end-to-end.

Requirements: 6.1, 6.3, 6.10, 6.11
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import JWT_EXPIRE_SECONDS
from app.services.auth_service import decode_token, hash_password
from tests.integration import _harness as h

PASSWORD = "s3cret-admin-pw"


async def _login(app):
    resp = await h.request(
        app, "POST", "/admin/login", json={"username": "admin", "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_login_generate_list_revoke_then_validate_rejected():
    async def scenario():
        await h.reset_tables()
        await h.seed_admin(username="admin", password_hash=hash_password(PASSWORD))
        app = h.build_app(authenticate=False)

        token = await _login(app)
        headers = {"Authorization": f"Bearer {token}"}

        created = await h.request(
            app,
            "POST",
            "/admin/vouchers/",
            json={"quantity": 1, "duration_minutes": 60},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        code = created.json()["vouchers"][0]["code"]

        listed = await h.request(app, "GET", "/admin/vouchers/", headers=headers)
        assert listed.status_code == 200
        assert any(item["code"] == code for item in listed.json()["items"])

        revoked = await h.request(
            app, "DELETE", f"/admin/vouchers/{code}/revoke", headers=headers
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["status"] == "revoked"

        rejected = await h.request(
            app, "POST", "/radius/validate", json={"username": code, "password": code}
        )
        assert rejected.json() == {"result": "reject", "reason": "revoked"}

    h.run(scenario())


def test_login_with_wrong_password_is_rejected():
    async def scenario():
        await h.reset_tables()
        await h.seed_admin(username="admin", password_hash=hash_password(PASSWORD))
        app = h.build_app(authenticate=False)

        resp = await h.request(
            app, "POST", "/admin/login", json={"username": "admin", "password": "wrong"}
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    h.run(scenario())


def test_idle_session_timeout_end_to_end():
    async def scenario():
        from app.services import auth_service

        await h.reset_tables()
        await h.seed_admin(username="admin", password_hash=hash_password(PASSWORD))
        app = h.build_app(authenticate=False)

        token = await _login(app)
        jti = decode_token(token)["jti"]

        # Simulate an idle session by backdating the tracked last-seen time
        # beyond the configured idle window.
        auth_service.LAST_SEEN[jti] = datetime.now(timezone.utc) - timedelta(
            seconds=JWT_EXPIRE_SECONDS + 60
        )
        try:
            resp = await h.request(
                app,
                "GET",
                "/admin/vouchers/",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"] == "Session idle timeout"
        finally:
            auth_service.LAST_SEEN.pop(jti, None)
            auth_service.BLOCKLIST.discard(jti)

    h.run(scenario())


def test_logout_invalidates_session():
    async def scenario():
        await h.reset_tables()
        await h.seed_admin(username="admin", password_hash=hash_password(PASSWORD))
        app = h.build_app(authenticate=False)

        token = await _login(app)
        headers = {"Authorization": f"Bearer {token}"}

        out = await h.request(app, "POST", "/admin/logout", headers=headers)
        assert out.status_code == 200

        after = await h.request(app, "GET", "/admin/vouchers/", headers=headers)
        assert after.status_code == 401

    h.run(scenario())
