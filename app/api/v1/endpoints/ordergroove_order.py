"""
OrderGroove Recurring Order Placement endpoint.

Receives the XML order request from OrderGroove (recurring subscription orders),
converts it to JSON, persists the full payload in webhook_events, and returns
the expected XML success/error response.

OrderGroove sends the body as application/x-www-form-urlencoded with fields:
  username=<value>&password=<value>&xml=<url-encoded XML>

Reference: https://developer.ordergroove.com/docs/recurring-order-placement
"""

import json
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qs, unquote_plus

from fastapi import APIRouter, Depends, Request, Response

from app.core.dependencies import get_unit_of_work
from app.core.unit_of_work import UnitOfWork
from app.schemas.webhook import WebhookEventCreate
from app.services.idempotency_service import IdempotencyService
from app.services.ordergroove_recurring_service import (
    og_recurring_service,
    RecurringOrderError,
)

logger = logging.getLogger(__name__)

router = APIRouter()

XML_SUCCESS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<order>
  <code>SUCCESS</code>
  <orderId>{order_id}</orderId>
</order>"""

XML_ERROR_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<order>
  <code>ERROR</code>
  <errorCode>{error_code}</errorCode>
  <errorMsg>{error_msg}</errorMsg>
</order>"""


def _element_to_dict(element: ET.Element) -> dict[str, Any] | str:
    """Recursively convert an XML Element into a dict/str."""
    children = list(element)
    if not children:
        return (element.text or "").strip()

    result: dict[str, Any] = {}
    for child in children:
        tag = child.tag
        value = _element_to_dict(child)

        if tag in result:
            existing = result[tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[tag] = [existing, value]
        else:
            result[tag] = value

    return result


def parse_order_xml(xml_string: str) -> dict[str, Any]:
    """Parse the full OrderGroove order XML into a flat JSON-friendly dict."""
    root = ET.fromstring(xml_string)
    return {root.tag: _element_to_dict(root)}


def extract_xml_from_body(raw_body: bytes) -> str:
    """
    OrderGroove posts as form-urlencoded: username=&password=&xml=<encoded XML>.
    Extract and return the decoded XML string.
    Falls back to treating the entire body as raw XML.
    """
    body_str = raw_body.decode("utf-8", errors="replace")

    if "xml=" in body_str:
        parsed = parse_qs(body_str, keep_blank_values=True)
        xml_values = parsed.get("xml", [])
        if xml_values:
            return xml_values[0].strip()

    return body_str.strip()


@router.post("/order-placement")
async def handle_ordergroove_order_placement(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")

    logger.info("=" * 80)
    logger.info("OrderGroove Recurring Order Placement — incoming request")
    logger.info(f"Content-Type: {content_type}")
    logger.info(f"Headers: {dict(request.headers)}")
    logger.info("=" * 80)

    body_str = raw_body.decode("utf-8", errors="replace")
    if "xml=" in body_str:
        form_fields = parse_qs(body_str, keep_blank_values=True)
        logger.info(f"Form username: {form_fields.get('username', [''])[0]}")
        logger.info(f"Form password: {'***' if form_fields.get('password', [''])[0] else '(empty)'}")

    try:
        xml_string = extract_xml_from_body(raw_body)
        payload = parse_order_xml(xml_string)
    except ET.ParseError as exc:
        logger.error(f"Failed to parse OrderGroove XML: {exc}")
        logger.error(f"Raw body: {raw_body.decode('utf-8', errors='replace')}")
        return Response(
            content=XML_ERROR_TEMPLATE.format(
                error_code="020",
                error_msg="Invalid XML received",
            ),
            media_type="application/xml",
            status_code=400,
        )

    logger.info("OrderGroove order payload (JSON):")
    logger.info(json.dumps(payload, indent=2, default=str))

    order_data = payload.get("order", {})
    head = order_data.get("head", {})
    customer = order_data.get("customer", {})
    items = order_data.get("items", {})

    og_order_id = head.get("orderOgId", "")
    og_public_id = head.get("orderPublicId", "")
    merchant_customer_id = customer.get("customerPartnerId", "")
    customer_email = customer.get("customerEmail", "")

    item_list = items.get("item", [])
    if isinstance(item_list, dict):
        item_list = [item_list]

    logger.info(f"OG Order ID: {og_order_id}")
    logger.info(f"OG Public Order ID: {og_public_id}")
    logger.info(f"Customer: {merchant_customer_id} ({customer_email})")
    logger.info(f"Items count: {len(item_list)}")
    for idx, item in enumerate(item_list):
        logger.info(
            f"  Item {idx + 1}: sku={item.get('sku', '')} "
            f"qty={item.get('qty', '')} price={item.get('price', '')} "
            f"finalPrice={item.get('finalPrice', '')}"
        )

    logger.info("-" * 80)
    logger.info(f"Payment method: {head.get('orderPaymentMethod', '')}")
    logger.info(f"CC type: {head.get('orderCcType', '')}")
    logger.info(f"Token ID: {head.get('orderTokenId', '')}")
    logger.info(f"Total: {head.get('orderTotalValue', '')} {head.get('orderCurrency', '')}")
    logger.info("=" * 80)

    event_id = og_public_id or og_order_id or f"og_order_{int(time.time() * 1000)}"

    webhook_data = WebhookEventCreate(
        event_id=event_id,
        psp="ordergroove",
        event_type="recurring_order_placement",
        medusa_order_id=og_order_id,
        payload=payload,
    )

    service = IdempotencyService(uow)
    result = await service.check_and_create_webhook_event(webhook_data)

    if result is None:
        logger.info(f"OrderGroove order already processed: {event_id}")
        return Response(
            content=XML_SUCCESS_TEMPLATE.format(order_id=og_order_id or event_id),
            media_type="application/xml",
            status_code=200,
        )

    # ── Solidgate recurring token: use Token ID from the XML (orderTokenId) for /recurring API ──
    solidgate_recurring_token = (head.get("orderTokenId") or "").strip() or None
    if solidgate_recurring_token:
        logger.info("[og-recurring] Using orderTokenId from XML for Solidgate /recurring")

    # ── Process the recurring order: Solidgate (/recurring) or Netvalve (rebill) ──
    try:
        recurring_result = await og_recurring_service.process_recurring_order(
            og_order_data=order_data,
            solidgate_recurring_token=solidgate_recurring_token,
        )

        new_order_id = recurring_result["new_order_id"]

        logger.info(
            f"[og-recurring] Full flow completed — OG order {og_order_id} → "
            f"Medusa order {new_order_id}, "
            f"rebill/recurring txn={recurring_result.get('rebill_transaction_id') or recurring_result.get('solidgate_transaction_id')}"
        )

        response_xml = XML_SUCCESS_TEMPLATE.format(order_id=new_order_id)
        logger.info(f"Responding to OrderGroove with SUCCESS — orderId={new_order_id}")

        return Response(
            content=response_xml,
            media_type="application/xml",
            status_code=200,
        )

    except RecurringOrderError as exc:
        logger.error(
            f"[og-recurring] Failed at step '{exc.step}': {exc.message}"
        )
        return Response(
            content=XML_ERROR_TEMPLATE.format(
                error_code="010",
                error_msg=f"Order processing failed: {exc.message}",
            ),
            media_type="application/xml",
            status_code=200,
        )

    except Exception as exc:
        logger.exception(f"[og-recurring] Unexpected error processing OG order {og_order_id}")
        return Response(
            content=XML_ERROR_TEMPLATE.format(
                error_code="099",
                error_msg=f"Unexpected error: {str(exc)}",
            ),
            media_type="application/xml",
            status_code=200,
        )
