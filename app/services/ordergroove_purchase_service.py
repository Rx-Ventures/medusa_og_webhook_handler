"""
OrderGroove Purchase POST service (migrated from Medusa workflow).

Builds payload from order + payment override, encrypts payment fields (AES-256-ECB),
generates HMAC signature, and POSTs to OrderGroove subscription/create.
"""

import base64
import hashlib
import hmac as hmaclib
import json
import logging
import time
import urllib.parse
from typing import Any

import httpx
from Crypto.Cipher import AES  # noqa: I001

from app.core.config import settings
from app.services.medusa_service import medusa_service

logger = logging.getLogger(__name__)

BLOCK_SIZE = 32
PADDING_BYTE = ord("{")


def _pad(s: str) -> bytes:
    buf = s.encode("utf-8")
    padding_length = BLOCK_SIZE - (len(buf) % BLOCK_SIZE)
    return buf + bytes([PADDING_BYTE] * padding_length)


def _encode_aes(key: bytes, s: str) -> str:
    if len(key) != 32:
        raise ValueError("ORDERGROOVE_HASH_KEY must be exactly 32 bytes for AES-256")
    padded = _pad(s)
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted = cipher.encrypt(padded)
    return base64.b64encode(encrypted).decode("ascii")


def _encrypt_payment(payment: dict[str, Any], hash_key: str) -> dict[str, Any]:
    if not hash_key or len(hash_key.encode("utf-8")) != 32:
        logger.warning(
            "[ordergroove] ORDERGROOVE_HASH_KEY not set or not 32 bytes — skipping encryption"
        )
        return payment
    key = hash_key.encode("utf-8")
    out = dict(payment)
    for field in ("cc_number", "cc_holder", "cc_exp_date"):
        if out.get(field):
            out[field] = _encode_aes(key, str(out[field]))
    return out


def _generate_hmac(customer_id: str, hash_key: str) -> tuple[str, str]:
    if not hash_key:
        return "", ""
    timestamp = str(int(time.time()))
    data = f"{customer_id}|{timestamp}"
    sig = hmaclib.new(
        hash_key.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return urllib.parse.quote(sig, safe=""), timestamp


def _str_or_empty(v: Any) -> str:
    """Coerce to string; never send null to OrderGroove."""
    if v is None:
        return ""
    return str(v).strip() if isinstance(v, str) else str(v)


def _build_user_and_products(order: dict[str, Any]) -> tuple[dict, list]:
    shipping = order.get("shipping_address") or {}
    billing = order.get("billing_address") or shipping

    user = {
        "user_id": order.get("customer_id") or order.get("email") or f"user_{order.get('id')}",
        "first_name": _str_or_empty(shipping.get("first_name") or billing.get("first_name")),
        "last_name": _str_or_empty(shipping.get("last_name") or billing.get("last_name")),
        "email": _str_or_empty(order.get("email")),
        "phone_number": shipping.get("phone") or billing.get("phone") or None,
        "extra_data": {},
        "shipping_address": {
            "first_name": _str_or_empty(shipping.get("first_name")),
            "last_name": _str_or_empty(shipping.get("last_name")),
            "label": "Home",
            "address": _str_or_empty(shipping.get("address_1")),
            "address2": _str_or_empty(shipping.get("address_2")),
            "city": _str_or_empty(shipping.get("city")),
            "state_province_code": _str_or_empty(shipping.get("province")),
            "zip_postal_code": _str_or_empty(shipping.get("postal_code")),
            "country_code": _str_or_empty(shipping.get("country_code")),
            "phone": _str_or_empty(shipping.get("phone")),
        },
        "billing_address": {
            "first_name": _str_or_empty(billing.get("first_name")),
            "last_name": _str_or_empty(billing.get("last_name")),
            "label": "Home",
            "address": _str_or_empty(billing.get("address_1")),
            "address2": _str_or_empty(billing.get("address_2")),
            "city": _str_or_empty(billing.get("city")),
            "state_province_code": _str_or_empty(billing.get("province")),
            "zip_postal_code": _str_or_empty(billing.get("postal_code")),
            "country_code": _str_or_empty(billing.get("country_code")),
            "phone": _str_or_empty(billing.get("phone")),
        },
    }

    items = order.get("items") or []
    products = []
    for item in items:
        product_id = item.get("variant_id") or item.get("product_id") or item.get("id")
        price = str(item.get("unit_price", 0) or 0)
        qty = item.get("quantity", 1)
        total = str((item.get("unit_price") or 0) * qty)
        products.append({
            "product": product_id,
            "sku": item.get("variant_sku") or item.get("sku") or product_id,
            "subscription_info": {
                "price": price,
                "quantity": qty,
                "tracking_override": {"product": product_id, "every": 1, "every_period": 2},
                "subscription_type": "prepaid",
                "prepaid_orders_per_billing": 1,
                "renewal_behavior": "autorenew",
                "rotation_ordinal": 1,
            },
            "purchase_info": {"quantity": qty, "price": price, "total": total},
        })
    return user, products


async def trigger_purchase_post(
    order_id: str,
    payment_override: dict[str, Any],
) -> dict[str, Any]:
    """
    Fetch order from Medusa, build OrderGroove payload, encrypt, sign, POST to OG.
    payment_override must have token_id; may have cc_number, cc_holder, cc_exp_date, cc_type, label.
    Returns {success: bool, data?: any, error?: str, status_code?: int}.
    """
    merchant_id = settings.ORDERGROOVE_MERCHANT_ID or ""
    hash_key = settings.ORDERGROOVE_HASH_KEY or ""
    api_url = settings.ORDERGROOVE_PURCHASE_API_URL or "https://staging.sc.ordergroove.com/subscription/create"
    api_key = settings.ORDERGROOVE_PURCHASE_API_KEY or settings.ORDERGROOVE_API_KEY or ""

    result = await medusa_service.execute_request(
        endpoint=f"/admin/orders/{order_id}",
        method="GET",
        params={
            "fields": "id,display_id,email,customer_id,currency_code,total,items.*,shipping_address.*,billing_address.*",
        },
    )
    if not result.success:
        return {"success": False, "error": result.message or "Failed to fetch order"}

    order = result.data.get("order", {})
    if not order:
        return {"success": False, "error": f"Order not found: {order_id}"}

    user, products = _build_user_and_products(order)
    customer_name = f"{user['first_name']} {user['last_name']}".strip() or "Test Customer"

    payment = {
        "cc_number": payment_override.get("cc_number") or "4111111111111111",
        "cc_holder": payment_override.get("cc_holder") or customer_name,
        "cc_exp_date": payment_override.get("cc_exp_date") or "12/2030",
        "cc_type": payment_override.get("cc_type") or "",
        "payment_method": "credit card",
        "token_id": payment_override.get("token_id") or "",
        "label": payment_override.get("label") or "solidgate",
    }

    payload = {
        "merchant_id": merchant_id,
        "og_cart_tracking": True,
        "session_id": order.get("id", order_id),
        "merchant_order_id": order_id,
        "user": user,
        "payment": payment,
        "products": products,
    }

    payment_encrypted = _encrypt_payment(payload["payment"], hash_key)
    payload["payment"] = payment_encrypted

    signature, timestamp = _generate_hmac(user["user_id"], hash_key)
    logger.info(
        "[ordergroove] Payload built — merchant=%s, order=%s, products=%s, token_id=%s",
        merchant_id,
        order_id,
        len(products),
        "present" if payment.get("token_id") else "MISSING",
    )
    logger.info("[ordergroove] Payment data encrypted successfully")
    logger.info("[ordergroove] HMAC signature generated — customer=%s, timestamp=%s", user["user_id"], timestamp)

    body_data = json.dumps(payload)
    body_encoded = urllib.parse.quote(body_data)
    body = f"create_request={body_encoded}"

    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "x-api-key": api_key,
    }
    if signature:
        headers["X-Signature"] = signature
        headers["X-Timestamp"] = timestamp

    logger.info("[ordergroove] Sending Purchase POST — url=%s, order=%s, products=%s", api_url, order_id, len(products))
    logger.info("[ordergroove] Purchase POST payload: %s", body_data)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(api_url, content=body, headers=headers, timeout=30.0)
    except Exception as e:
        logger.exception("[ordergroove] Purchase POST request error")
        return {"success": False, "error": str(e)}

    try:
        response_data = response.json()
    except Exception:
        response_data = response.text

    if response.status_code < 200 or response.status_code >= 300:
        logger.error("[ordergroove] Purchase POST failed — HTTP %s: %s", response.status_code, response.text)
        return {
            "success": False,
            "error": response.text,
            "status_code": response.status_code,
            "data": response_data,
        }

    logger.info("[ordergroove] Purchase POST success — HTTP %s", response.status_code)
    logger.info("[ordergroove] Purchase POST response: %s", json.dumps(response_data, indent=2))
    return {"success": True, "data": response_data, "status_code": response.status_code}