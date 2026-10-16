"""Unit tests for voucher generation input validation (Req 1.3, 1.5, 1.9, 11.3).

A minimal FastAPI app with only the admin_vouchers router is used so the tests
do not require the full application or a live database. The DB and auth
dependencies are overridden, and voucher persistence is monkeypatched.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.routers import admin_vouchers
from app.services.auth_service import get_current_admin

URL = "/admin/vouchers/"


def _fake_voucher() -> SimpleNamespace:
    return SimpleNamespace(
        code="AB3CD5EF7GH8",
        status="unused",
        activation_mode="use-activated",
        duration_minutes=60,
        expires_at=None,
        created_at=None,
        consumed_at=None,
        revoked_at=None,
    )


@pytest.fixture
def client(monkeypatch):
    """A TestClient with auth/DB overridden and batch generation monkeypatched."""

    async def _fake_batch(quantity, duration_minutes, activation_mode, admin_id, db):
        return [_fake_voucher() for _ in range(quantity)]

    async def _fake_admin():
        return {"sub": "00000000-0000-0000-0000-000000000000", "username": "test", "jti": "t"}

    async def _fake_db():
        yield None

    monkeypatch.setattr(admin_vouchers, "generate_vouchers_batch", _fake_batch)

    app = FastAPI()
    app.include_router(admin_vouchers.router)
    app.dependency_overrides[get_current_admin] = _fake_admin
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Quantity boundary (Req 1.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quantity", [0, 501, -1])
def test_quantity_out_of_range_returns_400(client, quantity):
    resp = client.post(URL, json={"quantity": quantity, "duration_minutes": 60})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "quantity_out_of_range"


@pytest.mark.parametrize("quantity", [1, 500])
def test_quantity_in_range_returns_201(client, quantity):
    resp = client.post(URL, json={"quantity": quantity, "duration_minutes": 60})
    assert resp.status_code == 201
    assert resp.json()["generated"] == quantity


# ---------------------------------------------------------------------------
# Duration boundary (Req 1.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("duration", [0, 43201])
def test_duration_out_of_range_returns_400(client, duration):
    resp = client.post(URL, json={"quantity": 1, "duration_minutes": duration})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "duration_out_of_range"


@pytest.mark.parametrize("duration", [1, 43200])
def test_duration_in_range_returns_201(client, duration):
    resp = client.post(URL, json={"quantity": 1, "duration_minutes": duration})
    assert resp.status_code == 201


def test_preset_duration_is_resolved(client):
    resp = client.post(URL, json={"quantity": 1, "duration_preset": "8h"})
    assert resp.status_code == 201


def test_unknown_preset_returns_400(client):
    resp = client.post(URL, json={"quantity": 1, "duration_preset": "3h"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_duration_preset"


def test_both_duration_forms_rejected(client):
    resp = client.post(
        URL, json={"quantity": 1, "duration_minutes": 60, "duration_preset": "1h"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "duration_required"


def test_neither_duration_form_rejected(client):
    resp = client.post(URL, json={"quantity": 1})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "duration_required"


# ---------------------------------------------------------------------------
# Activation mode (Req 1.9)
# ---------------------------------------------------------------------------


def test_invalid_activation_mode_returns_400(client):
    resp = client.post(
        URL, json={"quantity": 1, "duration_minutes": 60, "activation_mode": "bogus"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_activation_mode"


# ---------------------------------------------------------------------------
# Schema validation (Req 11.3)
# ---------------------------------------------------------------------------


def test_malformed_field_returns_422_with_field_name(client):
    resp = client.post(URL, json={"quantity": "not-a-number", "duration_minutes": 60})
    assert resp.status_code == 422
    locations = [str(err.get("loc")) for err in resp.json()["detail"]]
    assert any("quantity" in loc for loc in locations)
