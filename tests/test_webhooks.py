"""
Unit tests for the webhook endpoints:
  POST /api/v1/webhooks/solidgate  — Solidgate settle_ok webhook
  POST /api/v1/webhooks/ordergroove — OrderGroove generic webhook
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ─── Helper: mock UnitOfWork dependency ─────────────────────────────────────


def _mock_uow():
    """Build a mock UnitOfWork with async commit/rollback and webhook_events repo."""
    uow = MagicMock()
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    uow.session = MagicMock()
    uow.session.add = MagicMock()
    repo = MagicMock()
    repo.mark_as_processed = AsyncMock()
    repo.mark_as_failed = AsyncMock()
    uow.webhook_events = repo
    return uow


def _override_uow(client, uow):
    """Override the get_unit_of_work dependency to return our mock."""
    from app.core.dependencies import get_unit_of_work
    from app.main import app

    async def fake_uow():
        yield uow

    app.dependency_overrides[get_unit_of_work] = fake_uow
    yield
    app.dependency_overrides.pop(get_unit_of_work, None)


@pytest.fixture
def mock_uow(client):
    uow = _mock_uow()
    from app.core.dependencies import get_unit_of_work
    from app.main import app

    async def fake_uow():
        yield uow

    app.dependency_overrides[get_unit_of_work] = fake_uow
    yield uow
    app.dependency_overrides.pop(get_unit_of_work, None)


# ─── Solidgate webhook ──────────────────────────────────────────────────────


def test_solidgate_webhook_missing_headers(client, mock_uow):
    """POST /api/v1/webhooks/solidgate returns success=False when headers missing."""
    response = client.post(
        "/api/v1/webhooks/solidgate",
        json={"order": {"order_id": "cart_1", "status": "settle_ok"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Missing required headers" in data["message"]


def test_solidgate_webhook_duplicate_event(client, mock_uow):
    """POST /api/v1/webhooks/solidgate returns 'already processed' for duplicate event."""
    with patch(
        "app.api.v1.endpoints.webhooks.IdempotencyService"
    ) as MockService:
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=None)

        response = client.post(
            "/api/v1/webhooks/solidgate",
            json={"order": {"order_id": "cart_1", "status": "settle_ok"}},
            headers={
                "solidgate-event-id": "evt_dup",
                "solidgate-event-type": "payment.settle_ok",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "already processed" in data["message"].lower()


def test_solidgate_webhook_non_settle_ok(client, mock_uow):
    """POST /api/v1/webhooks/solidgate processes non-settle_ok events and returns success."""
    fake_event = MagicMock()
    fake_event.id = "wh_1"
    with patch(
        "app.api.v1.endpoints.webhooks.IdempotencyService"
    ) as MockService:
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=fake_event)

        response = client.post(
            "/api/v1/webhooks/solidgate",
            json={"order": {"order_id": "cart_1", "status": "auth_ok"}},
            headers={
                "solidgate-event-id": "evt_1",
                "solidgate-event-type": "payment.auth_ok",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "processed" in data["message"].lower()


def test_solidgate_webhook_settle_ok_missing_cart_id(client, mock_uow):
    """POST /api/v1/webhooks/solidgate returns 400 if settle_ok has no cart_id."""
    fake_event = MagicMock()
    fake_event.id = "wh_2"
    with (
        patch("app.api.v1.endpoints.webhooks.IdempotencyService") as MockService,
        patch("app.api.v1.endpoints.webhooks.slack_service.send_critical_alert", new_callable=AsyncMock),
    ):
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=fake_event)

        response = client.post(
            "/api/v1/webhooks/solidgate",
            json={"order": {"status": "settle_ok"}},
            headers={
                "solidgate-event-id": "evt_2",
                "solidgate-event-type": "payment.settle_ok",
            },
        )
    assert response.status_code == 400
    assert "cart_id" in response.json()["detail"].lower()


def test_solidgate_webhook_settle_ok_success(client, mock_uow):
    """POST /api/v1/webhooks/solidgate processes settle_ok and returns Medusa result."""
    fake_event = MagicMock()
    fake_event.id = "wh_3"
    mock_result = MagicMock()
    mock_result.data = {"order_id": "order_123"}

    with (
        patch("app.api.v1.endpoints.webhooks.IdempotencyService") as MockService,
        patch(
            "app.api.v1.endpoints.webhooks.medusa_service.process_settle_ok",
            new_callable=AsyncMock,
            return_value=mock_result,
        ),
        patch(
            "app.api.v1.endpoints.webhooks.medusa_service.get_cart_metadata",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=fake_event)

        response = client.post(
            "/api/v1/webhooks/solidgate",
            json={
                "order": {"order_id": "cart_1", "status": "settle_ok"},
                "transactions": {},
            },
            headers={
                "solidgate-event-id": "evt_3",
                "solidgate-event-type": "payment.settle_ok",
            },
        )
    assert response.status_code == 200


# ─── OrderGroove generic webhook ────────────────────────────────────────────


def test_ordergroove_webhook_duplicate(client, mock_uow):
    """POST /api/v1/webhooks/ordergroove returns 200 for duplicate event (idempotent)."""
    with patch("app.api.v1.endpoints.webhooks.IdempotencyService") as MockService:
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=None)

        response = client.post(
            "/api/v1/webhooks/ordergroove",
            json={"type": "subscription.created", "event_id": "og_dup", "order_id": "ord_1"},
        )
    assert response.status_code == 200


def test_ordergroove_webhook_success(client, mock_uow):
    """POST /api/v1/webhooks/ordergroove creates event and returns it."""
    fake_event = MagicMock()
    fake_event.id = "wh_og_1"
    fake_event.event_id = "og_1"
    fake_event.psp = "ordergroove"
    fake_event.event_type = "subscription.created"
    fake_event.medusa_order_id = "ord_1"
    fake_event.processed = False
    fake_event.error_message = None
    fake_event.created_at = None
    fake_event.updated_at = None

    with patch("app.api.v1.endpoints.webhooks.IdempotencyService") as MockService:
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=fake_event)

        response = client.post(
            "/api/v1/webhooks/ordergroove",
            json={"type": "subscription.created", "event_id": "og_1", "order_id": "ord_1"},
        )
    assert response.status_code == 200
