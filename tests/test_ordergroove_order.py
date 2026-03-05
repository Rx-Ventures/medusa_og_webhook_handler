"""
Unit tests for OrderGroove order placement endpoint:
  POST /api/v1/ordergroove/order-placement
  POST /api/v1/ordergroove/trigger-purchase-post
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<order>
  <head>
    <orderOgId>og_123</orderOgId>
    <orderPublicId>pub_123</orderPublicId>
    <orderPaymentMethod>CC</orderPaymentMethod>
    <orderCcType>visa</orderCcType>
    <orderTokenId>tok_abc</orderTokenId>
    <orderTotalValue>29.99</orderTotalValue>
    <orderCurrency>USD</orderCurrency>
  </head>
  <customer>
    <customerPartnerId>cust_1</customerPartnerId>
    <customerEmail>buyer@test.com</customerEmail>
  </customer>
  <items>
    <item>
      <sku>SKU-001</sku>
      <qty>1</qty>
      <price>29.99</price>
      <finalPrice>29.99</finalPrice>
    </item>
  </items>
</order>"""


@pytest.fixture
def mock_uow(client):
    uow = MagicMock()
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    uow.session = MagicMock()
    uow.session.add = MagicMock()
    repo = MagicMock()
    repo.mark_as_processed = AsyncMock()
    repo.mark_as_failed = AsyncMock()
    uow.webhook_events = repo

    from app.core.dependencies import get_unit_of_work
    from app.main import app

    async def fake_uow():
        yield uow

    app.dependency_overrides[get_unit_of_work] = fake_uow
    yield uow
    app.dependency_overrides.pop(get_unit_of_work, None)


# ─── XML helpers ────────────────────────────────────────────────────────────


def test_parse_order_xml():
    """parse_order_xml turns valid XML into a dict with order.head/customer/items."""
    from app.api.v1.endpoints.ordergroove_order import parse_order_xml

    result = parse_order_xml(SAMPLE_XML)
    assert "order" in result
    assert result["order"]["head"]["orderOgId"] == "og_123"
    assert result["order"]["customer"]["customerEmail"] == "buyer@test.com"
    items = result["order"]["items"]["item"]
    if isinstance(items, list):
        assert items[0]["sku"] == "SKU-001"
    else:
        assert items["sku"] == "SKU-001"


def test_extract_xml_from_body_form():
    """extract_xml_from_body extracts XML from form-urlencoded body."""
    from app.api.v1.endpoints.ordergroove_order import extract_xml_from_body
    from urllib.parse import quote_plus

    encoded = f"username=test&password=pass&xml={quote_plus(SAMPLE_XML)}"
    result = extract_xml_from_body(encoded.encode("utf-8"))
    assert "orderOgId" in result


def test_extract_xml_from_body_raw():
    """extract_xml_from_body falls back to raw XML when no xml= key."""
    from app.api.v1.endpoints.ordergroove_order import extract_xml_from_body

    result = extract_xml_from_body(SAMPLE_XML.encode("utf-8"))
    assert "<order>" in result


# ─── Order placement endpoint ───────────────────────────────────────────────


def test_order_placement_invalid_xml(client, mock_uow):
    """POST /api/v1/ordergroove/order-placement returns XML error for invalid XML."""
    response = client.post(
        "/api/v1/ordergroove/order-placement",
        content=b"xml=not-valid-xml<<<",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
    assert b"ERROR" in response.content
    assert b"020" in response.content


def test_order_placement_duplicate(client, mock_uow):
    """POST /api/v1/ordergroove/order-placement returns SUCCESS for duplicate."""
    from urllib.parse import quote_plus

    with patch(
        "app.api.v1.endpoints.ordergroove_order.IdempotencyService"
    ) as MockService:
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=None)

        response = client.post(
            "/api/v1/ordergroove/order-placement",
            content=f"xml={quote_plus(SAMPLE_XML)}".encode("utf-8"),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    assert b"SUCCESS" in response.content


def test_order_placement_success(client, mock_uow):
    """POST /api/v1/ordergroove/order-placement processes order and returns XML SUCCESS."""
    from urllib.parse import quote_plus

    fake_event = MagicMock()
    fake_event.id = "wh_og_1"

    with (
        patch("app.api.v1.endpoints.ordergroove_order.IdempotencyService") as MockService,
        patch(
            "app.api.v1.endpoints.ordergroove_order.og_recurring_service.process_recurring_order",
            new_callable=AsyncMock,
            return_value={"new_order_id": "order_new_1", "rebill_transaction_id": "tx_rb"},
        ),
    ):
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=fake_event)

        response = client.post(
            "/api/v1/ordergroove/order-placement",
            content=f"xml={quote_plus(SAMPLE_XML)}".encode("utf-8"),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    assert b"SUCCESS" in response.content
    assert b"order_new_1" in response.content


def test_order_placement_recurring_error(client, mock_uow):
    """POST /api/v1/ordergroove/order-placement returns XML ERROR on RecurringOrderError."""
    from urllib.parse import quote_plus
    from app.services.ordergroove_recurring_service import RecurringOrderError

    fake_event = MagicMock()
    fake_event.id = "wh_og_2"

    with (
        patch("app.api.v1.endpoints.ordergroove_order.IdempotencyService") as MockService,
        patch(
            "app.api.v1.endpoints.ordergroove_order.og_recurring_service.process_recurring_order",
            new_callable=AsyncMock,
            side_effect=RecurringOrderError("Payment failed", step="charge"),
        ),
    ):
        MockService.return_value.check_and_create_webhook_event = AsyncMock(return_value=fake_event)

        response = client.post(
            "/api/v1/ordergroove/order-placement",
            content=f"xml={quote_plus(SAMPLE_XML)}".encode("utf-8"),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    assert b"ERROR" in response.content
    assert b"010" in response.content


# ─── Trigger Purchase POST ──────────────────────────────────────────────────


def test_trigger_purchase_post_missing_order_id(client, mock_uow):
    """POST /api/v1/ordergroove/trigger-purchase-post returns 400 if no order_id."""
    response = client.post(
        "/api/v1/ordergroove/trigger-purchase-post",
        json={"payment_override": {"token_id": "tok_1"}},
    )
    assert response.status_code == 400
    assert "order_id" in response.json()["message"].lower()


def test_trigger_purchase_post_missing_token(client, mock_uow):
    """POST /api/v1/ordergroove/trigger-purchase-post returns 400 if no token_id."""
    response = client.post(
        "/api/v1/ordergroove/trigger-purchase-post",
        json={"order_id": "ord_1", "payment_override": {}},
    )
    assert response.status_code == 400
    assert "token_id" in response.json()["message"].lower()


def test_trigger_purchase_post_disabled(client, mock_uow):
    """POST /api/v1/ordergroove/trigger-purchase-post returns 200 with success False when ORDERGROOVE_PURCHASE_ENABLED is false."""
    with patch("app.api.v1.endpoints.ordergroove_order.settings") as mock_settings:
        mock_settings.ORDERGROOVE_PURCHASE_ENABLED = False
        response = client.post(
            "/api/v1/ordergroove/trigger-purchase-post",
            json={"order_id": "ord_1", "payment_override": {"token_id": "tok_1"}},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "disabled" in data["message"].lower()


def test_trigger_purchase_post_success(client, mock_uow):
    """POST /api/v1/ordergroove/trigger-purchase-post returns success when enabled."""
    with (
        patch("app.api.v1.endpoints.ordergroove_order.settings") as mock_settings,
        patch(
            "app.api.v1.endpoints.ordergroove_order.trigger_purchase_post",
            new_callable=AsyncMock,
            return_value={"success": True, "data": {"subscription_id": "sub_1"}},
        ),
    ):
        mock_settings.ORDERGROOVE_PURCHASE_ENABLED = True
        response = client.post(
            "/api/v1/ordergroove/trigger-purchase-post",
            json={"order_id": "ord_1", "payment_override": {"token_id": "tok_1"}},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["subscription_id"] == "sub_1"


def test_trigger_purchase_post_failure(client, mock_uow):
    """POST /api/v1/ordergroove/trigger-purchase-post returns error on OG failure when enabled."""
    with (
        patch("app.api.v1.endpoints.ordergroove_order.settings") as mock_settings,
        patch(
            "app.api.v1.endpoints.ordergroove_order.trigger_purchase_post",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "OG API rejected", "status_code": 400},
        ),
    ):
        mock_settings.ORDERGROOVE_PURCHASE_ENABLED = True
        response = client.post(
            "/api/v1/ordergroove/trigger-purchase-post",
            json={"order_id": "ord_1", "payment_override": {"token_id": "tok_1"}},
        )
    assert response.status_code == 502
    data = response.json()
    assert data["success"] is False
