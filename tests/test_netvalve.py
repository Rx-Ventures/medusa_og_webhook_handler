"""
Unit tests for NetValve flows: HPF session, payment, capture, refund, cancel, webhook, status.

Uses mocked services so tests run without real NetValve APIs. Assertions verify
that the HTTP layer and request/response shapes stay correct as the app evolves.
"""
import pytest
from unittest.mock import patch, AsyncMock


# ─── HPF Session ────────────────────────────────────────────────────────────


def test_netvalve_hpf_session_post_success(client):
    """POST /api/v1/netvalve/hpf/session returns 200 with flow and script when service succeeds."""
    mock_response = {
        "provider": "netvalve",
        "flow": "hpf",
        "hpf": {"script_src": "https://cdn.example/hpf.js", "integrity": "sha384-xxx"},
    }
    with patch(
        "app.api.v1.endpoints.netvalve.hpf.netvalve_service.create_hpf_session",
        new_callable=AsyncMock,
        return_value=(200, mock_response),
    ):
        response = client.post(
            "/api/v1/netvalve/hpf/session",
            json={"currency_code": "USD", "amount": 99.99, "cart_id": "cart_1"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["flow"] == "hpf"
    assert data["hpf"]["script_src"] == "https://cdn.example/hpf.js"


@pytest.mark.parametrize("payload", [
    {"currency_code": "USD", "amount": 99.99},
    {"currency_code": "EUR", "amount": 50.0, "cart_id": "cart_2"},
    {"amount": 1.0},
])
def test_netvalve_hpf_session_post_payload_variants(client, payload):
    """POST /api/v1/netvalve/hpf/session accepts various optional payloads."""
    mock_response = {"provider": "netvalve", "flow": "hpf", "hpf": {"script_src": "https://x/hpf.js"}}
    with patch(
        "app.api.v1.endpoints.netvalve.hpf.netvalve_service.create_hpf_session",
        new_callable=AsyncMock,
        return_value=(200, mock_response),
    ):
        response = client.post("/api/v1/netvalve/hpf/session", json=payload)
    assert response.status_code == 200


def test_netvalve_hpf_session_post_failure(client):
    """POST /api/v1/netvalve/hpf/session returns 502 when all init paths fail."""
    with patch(
        "app.api.v1.endpoints.netvalve.hpf.netvalve_service.create_hpf_session",
        new_callable=AsyncMock,
        return_value=(502, {"message": "All initialization paths failed", "diagnostic": "no_hpf"}),
    ):
        response = client.post(
            "/api/v1/netvalve/hpf/session",
            json={"currency_code": "USD", "amount": 99.99},
        )
    assert response.status_code == 502
    assert "message" in response.json()


def test_netvalve_hpf_session_get_delegates_to_post(client):
    """GET /api/v1/netvalve/hpf/session maps query params and returns same as POST."""
    mock_response = {"provider": "netvalve", "flow": "hpp", "hpp": {"redirect_url": "https://hpp.example/order"}}
    with patch(
        "app.api.v1.endpoints.netvalve.hpf.netvalve_service.create_hpf_session",
        new_callable=AsyncMock,
        return_value=(200, mock_response),
    ):
        response = client.get(
            "/api/v1/netvalve/hpf/session",
            params={"currency_code": "EUR", "amount": 50.0, "cart_id": "cart_2"},
        )
    assert response.status_code == 200
    assert response.json()["flow"] == "hpp"


# ─── Payment (authorize/sale) ───────────────────────────────────────────────


def test_netvalve_payment_authorized(client):
    """POST /api/v1/netvalve/payment returns AUTHORIZED when sale succeeds."""
    with patch(
        "app.api.v1.endpoints.netvalve.payment.netvalve_service.authorize_payment",
        new_callable=AsyncMock,
        return_value={
            "status": "AUTHORIZED",
            "data": {"transaction_id": "tx_123", "order_id": "ord_1"},
        },
    ):
        response = client.post(
            "/api/v1/netvalve/payment",
            json={
                "hpf_completed": True,
                "hpf_payment_token": "tok_abc",
                "amount": 100.0,
                "currency_code": "USD",
                "cart_id": "cart_1",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "AUTHORIZED"
    assert data["data"]["transaction_id"] == "tx_123"


def test_netvalve_payment_requires_more(client):
    """POST /api/v1/netvalve/payment returns REQUIRES_MORE when card input needed."""
    with patch(
        "app.api.v1.endpoints.netvalve.payment.netvalve_service.authorize_payment",
        new_callable=AsyncMock,
        return_value={"status": "REQUIRES_MORE", "data": {}},
    ):
        response = client.post(
            "/api/v1/netvalve/payment",
            json={"amount": 50.0, "currency_code": "USD", "cart_id": "cart_1"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "REQUIRES_MORE"


@pytest.mark.parametrize("currency_code", ["USD", "EUR", "PHP"])
def test_netvalve_payment_currency_variants(client, currency_code):
    """POST /api/v1/netvalve/payment accepts different currency_code values."""
    with patch(
        "app.api.v1.endpoints.netvalve.payment.netvalve_service.authorize_payment",
        new_callable=AsyncMock,
        return_value={"status": "AUTHORIZED", "data": {"transaction_id": "tx_1"}},
    ):
        response = client.post(
            "/api/v1/netvalve/payment",
            json={"amount": 10.0, "currency_code": currency_code, "cart_id": "c1"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "AUTHORIZED"


def test_netvalve_payment_response_shape(client):
    """Payment response has status and data keys."""
    with patch(
        "app.api.v1.endpoints.netvalve.payment.netvalve_service.authorize_payment",
        new_callable=AsyncMock,
        return_value={"status": "AUTHORIZED", "data": {}},
    ):
        response = client.post(
            "/api/v1/netvalve/payment",
            json={"amount": 1.0, "currency_code": "USD"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "data" in data
    assert isinstance(data["data"], dict)


# ─── Capture ────────────────────────────────────────────────────────────────


def test_netvalve_capture_success(client):
    """POST /api/v1/netvalve/capture returns 200 with status captured."""
    with patch(
        "app.api.v1.endpoints.netvalve.capture.netvalve_service.capture_payment",
        new_callable=AsyncMock,
        return_value={
            "status": "captured",
            "transaction_id": "tx_456",
            "response_code": "00",
            "response_message": "Approved",
            "data": {},
        },
    ):
        response = client.post(
            "/api/v1/netvalve/capture",
            json={"transaction_id": "tx_456", "amount": 100.0, "already_captured": False},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "captured"
    assert data["transaction_id"] == "tx_456"


@pytest.mark.parametrize("payload,description", [
    ({}, "empty body"),
    ({"transaction_id": "tx_1"}, "missing amount"),
    ({"amount": 100.0}, "missing transaction_id"),
])
def test_netvalve_capture_validation_variants(client, payload, description):
    """POST /api/v1/netvalve/capture returns 422 for invalid or incomplete payloads."""
    response = client.post("/api/v1/netvalve/capture", json=payload)
    assert response.status_code == 422, description


# ─── Refund ────────────────────────────────────────────────────────────────


def test_netvalve_refund_success(client):
    """POST /api/v1/netvalve/refund returns 200 with status refunded."""
    with patch(
        "app.api.v1.endpoints.netvalve.refund.netvalve_service.refund_payment",
        new_callable=AsyncMock,
        return_value={
            "status": "refunded",
            "transaction_id": "tx_789",
            "refunded_amount": 50.0,
            "response_code": "00",
            "response_message": "Refunded",
            "data": {},
        },
    ):
        response = client.post(
            "/api/v1/netvalve/refund",
            json={"transaction_id": "tx_789", "amount": 50.0},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "refunded"
    assert data["refunded_amount"] == 50.0


@pytest.mark.parametrize("payload,description", [
    ({}, "empty body"),
    ({"transaction_id": "tx_1"}, "missing amount"),
    ({"amount": 50.0}, "missing transaction_id"),
])
def test_netvalve_refund_validation_variants(client, payload, description):
    """POST /api/v1/netvalve/refund returns 422 for invalid or incomplete payloads."""
    response = client.post("/api/v1/netvalve/refund", json=payload)
    assert response.status_code == 422, description


# ─── Cancel ─────────────────────────────────────────────────────────────────


def test_netvalve_cancel_success(client):
    """POST /api/v1/netvalve/cancel returns 200 with status canceled."""
    with patch(
        "app.api.v1.endpoints.netvalve.cancel.netvalve_service.cancel_payment",
        new_callable=AsyncMock,
        return_value={
            "status": "canceled",
            "transaction_id": "tx_void",
            "response_code": "00",
            "response_message": "Voided",
            "data": {},
        },
    ):
        response = client.post(
            "/api/v1/netvalve/cancel",
            json={"transaction_id": "tx_void"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


def test_netvalve_cancel_validation(client):
    """POST /api/v1/netvalve/cancel returns 422 when transaction_id missing."""
    response = client.post("/api/v1/netvalve/cancel", json={})
    assert response.status_code == 422


# ─── Webhook ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("webhook_payload,expected_action", [
    ({"type": "authorized", "transaction_id": "tx_w1", "order_id": "ord_1"}, "AUTHORIZED"),
    ({"type": "captured", "transaction_id": "tx_w2"}, "SUCCESSFUL"),
    ({"type": "failed", "transaction_id": "tx_w3"}, "FAILED"),
    ({"type": "canceled", "transaction_id": "tx_w4"}, "CANCELED"),
    ({"type": "pending"}, "PENDING"),
    ({"type": "requires_more"}, "REQUIRES_MORE"),
])
def test_netvalve_webhook_actions(client, webhook_payload, expected_action):
    """POST /api/v1/netvalve/webhook returns correct action for event type."""
    with patch(
        "app.api.v1.endpoints.netvalve.webhook.netvalve_service.process_webhook",
        return_value={"action": expected_action, "data": {}},
    ):
        response = client.post(
            "/api/v1/netvalve/webhook",
            json=webhook_payload,
        )
    assert response.status_code == 200
    assert response.json()["action"] == expected_action


def test_netvalve_webhook_response_shape(client):
    """Webhook response has action and optional data."""
    with patch(
        "app.api.v1.endpoints.netvalve.webhook.netvalve_service.process_webhook",
        return_value={"action": "AUTHORIZED", "data": {"transaction_id": "tx_1"}},
    ):
        response = client.post(
            "/api/v1/netvalve/webhook",
            json={"type": "authorized", "transaction_id": "tx_1"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "action" in data
    assert data.get("data") is None or isinstance(data["data"], dict)


# ─── Status ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status_param", [
    "authorized", "captured", "pending",
    "requires_more", "error", "canceled",
])
def test_netvalve_status_all_valid(client, status_param):
    """GET /api/v1/netvalve/status returns 200 and echoes valid status."""
    response = client.get("/api/v1/netvalve/status", params={"status": status_param})
    assert response.status_code == 200
    assert response.json()["status"] == status_param


def test_netvalve_status_returns_persisted_status(client):
    """GET /api/v1/netvalve/status returns status and optional transaction_id."""
    response = client.get(
        "/api/v1/netvalve/status",
        params={"status": "authorized", "transaction_id": "tx_st"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "authorized"
    assert data["transaction_id"] == "tx_st"


def test_netvalve_status_defaults_to_pending(client):
    """GET /api/v1/netvalve/status defaults status to pending."""
    response = client.get("/api/v1/netvalve/status")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.parametrize("invalid_status", ["invalid", "INVALID", "unknown", ""])
def test_netvalve_status_invalid_normalized_to_pending(client, invalid_status):
    """GET /api/v1/netvalve/status normalizes invalid status to pending."""
    response = client.get("/api/v1/netvalve/status", params={"status": invalid_status})
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_netvalve_status_response_shape(client):
    """Status response has status and optional transaction_id."""
    response = client.get("/api/v1/netvalve/status", params={"transaction_id": "tx_1"})
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data.get("transaction_id") is None or isinstance(data["transaction_id"], str)
