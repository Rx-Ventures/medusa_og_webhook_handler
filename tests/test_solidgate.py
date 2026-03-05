"""
Unit tests for Solidgate flows: refund and payment initialize.

Covers multiple payloads, validation variants, and response shape so that
changes to routes or schemas are caught before deploy.
"""
import pytest
from unittest.mock import patch, AsyncMock


# ─── Refund: success payloads ────────────────────────────────────────────────


@pytest.mark.parametrize("payload,expected_status", [
    ({"order_id": "ord_123", "amount": 1050, "refund_reason_code": "0022"}, 200),
    ({"order_id": "ord_456", "amount": 500, "refund_reason_code": "0029"}, 200),
])
def test_solidgate_refund_success(client, payload, expected_status):
    """POST /api/v1/solidgate/refund returns 200 when service succeeds."""
    with patch(
        "app.api.v1.endpoints.solidgate.solidgate_service.refund_order",
        new_callable=AsyncMock,
        return_value={"success": True, "data": {"refund_id": "rf_1"}},
    ):
        response = client.post("/api/v1/solidgate/refund", json=payload)
    assert response.status_code == expected_status
    data = response.json()
    assert data["success"] is True
    assert "message" in data
    assert data.get("data", {}).get("refund_id") == "rf_1"


@pytest.mark.parametrize("refund_reason_code", ["0022", "0023", "0024", "0025", "0026", "0027", "0028", "0029"])
def test_solidgate_refund_all_reason_codes(client, refund_reason_code):
    """POST /api/v1/solidgate/refund accepts every valid refund_reason_code."""
    with patch(
        "app.api.v1.endpoints.solidgate.solidgate_service.refund_order",
        new_callable=AsyncMock,
        return_value={"success": True, "data": {}},
    ):
        response = client.post(
            "/api/v1/solidgate/refund",
            json={"order_id": "ord_x", "amount": 100, "refund_reason_code": refund_reason_code},
        )
    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.parametrize("amount", [1, 1050, 999999])
def test_solidgate_refund_amount_variants(client, amount):
    """POST /api/v1/solidgate/refund accepts different amount values (minor units)."""
    with patch(
        "app.api.v1.endpoints.solidgate.solidgate_service.refund_order",
        new_callable=AsyncMock,
        return_value={"success": True, "data": {"refunded": amount}},
    ):
        response = client.post(
            "/api/v1/solidgate/refund",
            json={"order_id": "ord_amt", "amount": amount, "refund_reason_code": "0022"},
        )
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_solidgate_refund_success_response_shape(client):
    """Refund success response has required keys and types."""
    with patch(
        "app.api.v1.endpoints.solidgate.solidgate_service.refund_order",
        new_callable=AsyncMock,
        return_value={"success": True, "data": {"refund_id": "rf_1"}},
    ):
        response = client.post(
            "/api/v1/solidgate/refund",
            json={"order_id": "o", "amount": 100, "refund_reason_code": "0022"},
        )
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) >= {"success", "message", "data"}
    assert data["data"] is None or isinstance(data["data"], dict)


# ─── Refund: validation (422) ───────────────────────────────────────────────


@pytest.mark.parametrize("payload,description", [
    ({}, "empty body"),
    ({"order_id": "x", "amount": 1050}, "missing refund_reason_code"),
    ({"amount": 1050, "refund_reason_code": "0022"}, "missing order_id"),
    ({"order_id": "x", "refund_reason_code": "0022"}, "missing amount"),
    ({"order_id": "x", "amount": 1050, "refund_reason_code": "9999"}, "invalid reason code"),
    ({"order_id": "x", "amount": 1050, "refund_reason_code": "0021"}, "reason code too low"),
    ({"order_id": "x", "amount": 1050, "refund_reason_code": "0030"}, "reason code too high"),
])
def test_solidgate_refund_validation_variants(client, payload, description):
    """POST /api/v1/solidgate/refund returns 422 for invalid or incomplete payloads."""
    response = client.post("/api/v1/solidgate/refund", json=payload)
    assert response.status_code == 422, description


# ─── Refund: service failure and errors ─────────────────────────────────────


def test_solidgate_refund_service_failure(client):
    """POST /api/v1/solidgate/refund returns 400 when Solidgate API fails."""
    with patch(
        "app.api.v1.endpoints.solidgate.solidgate_service.refund_order",
        new_callable=AsyncMock,
        return_value={
            "success": False,
            "error": {"error": {"message": "Insufficient capture amount"}},
        },
    ):
        response = client.post(
            "/api/v1/solidgate/refund",
            json={"order_id": "ord_123", "amount": 1050, "refund_reason_code": "0022"},
        )
    assert response.status_code == 400
    data = response.json()
    assert data["detail"]["success"] is False


def test_solidgate_refund_unexpected_error(client):
    """POST /api/v1/solidgate/refund returns 500 on unexpected exception."""
    with patch(
        "app.api.v1.endpoints.solidgate.solidgate_service.refund_order",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Network error"),
    ):
        response = client.post(
            "/api/v1/solidgate/refund",
            json={"order_id": "ord_123", "amount": 1050, "refund_reason_code": "0022"},
        )
    assert response.status_code == 500
    assert "Internal server error" in response.json()["detail"]["message"]


# ─── Payments initialize: success and payload variants ──────────────────────


def test_payments_initialize_success(client):
    """POST /api/v1/payments/initialize returns 200 and payment intent data."""
    with patch(
        "app.api.v1.endpoints.payments.solidgate_service.create_payment_intent",
        return_value={
            "merchant": "merchant_123",
            "signature": "sig_abc",
            "payment_intent": "pi_xyz",
        },
    ):
        response = client.post(
            "/api/v1/payments/initialize",
            json={
                "order_id": "order_1",
                "amount": 9999,
                "currency": "USD",
                "customer_email": "buyer@test.com",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["session_id"] == "order_1"
    assert data["data"]["psp"] == "solidgate"
    assert data["data"]["merchant"] == "merchant_123"
    assert data["data"]["signature"] == "sig_abc"
    assert data["data"]["payment_intent"] == "pi_xyz"


@pytest.mark.parametrize("currency", ["USD", "EUR", "PHP"])
def test_payments_initialize_currencies(client, currency):
    """POST /api/v1/payments/initialize accepts different currencies."""
    with patch(
        "app.api.v1.endpoints.payments.solidgate_service.create_payment_intent",
        return_value={"merchant": "m", "signature": "s", "payment_intent": "pi"},
    ):
        response = client.post(
            "/api/v1/payments/initialize",
            json={
                "order_id": "ord_1",
                "amount": 1000,
                "currency": currency,
                "customer_email": "u@test.com",
            },
        )
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_payments_initialize_response_shape(client):
    """Payment initialize success response has expected structure."""
    with patch(
        "app.api.v1.endpoints.payments.solidgate_service.create_payment_intent",
        return_value={"merchant": "m", "signature": "s", "payment_intent": "pi"},
    ):
        response = client.post(
            "/api/v1/payments/initialize",
            json={"order_id": "o", "amount": 100, "customer_email": "e@t.com"},
        )
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) >= {"success", "message", "data"}
    assert set(data["data"].keys()) >= {"session_id", "psp", "merchant", "signature", "payment_intent"}


# ─── Payments initialize: validation and failure ───────────────────────────


@pytest.mark.parametrize("payload,description", [
    ({}, "empty body"),
    ({"order_id": "o"}, "missing amount and customer_email"),
    ({"amount": 100, "customer_email": "e@t.com"}, "missing order_id"),
    ({"order_id": "o", "customer_email": "e@t.com"}, "missing amount"),
    ({"order_id": "o", "amount": 100}, "missing customer_email"),
])
def test_payments_initialize_validation_variants(client, payload, description):
    """POST /api/v1/payments/initialize returns 422 when required fields missing."""
    response = client.post("/api/v1/payments/initialize", json=payload)
    assert response.status_code == 422, description


def test_payments_initialize_failure(client):
    """POST /api/v1/payments/initialize returns success=False when service raises."""
    with patch(
        "app.api.v1.endpoints.payments.solidgate_service.create_payment_intent",
        side_effect=ValueError("Invalid order"),
    ):
        response = client.post(
            "/api/v1/payments/initialize",
            json={
                "order_id": "order_1",
                "amount": 9999,
                "currency": "USD",
                "customer_email": "buyer@test.com",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Failed to initialize payment" in data["message"]
