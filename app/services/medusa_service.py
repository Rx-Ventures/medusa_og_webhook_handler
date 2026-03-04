import logging
import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.core.redis import redis_client
from app.core.exceptions import WebhookProcessingError
from fastapi import status
from app.schemas.common import GenericApiResponse

logger = logging.getLogger(__name__)

MEDUSA_TOKEN_KEY = "medusa:admin_token"


class MedusaService:
    def __init__(self):
        self.base_url = settings.MEDUSA_BASE_URL
        self.email = settings.MEDUSA_ADMIN_EMAIL
        self.password = settings.MEDUSA_ADMIN_PASSWORD
        self.token_ttl = settings.MEDUSA_TOKEN_CACHE_TTL

    async def _get_cached_token(self) -> str | None:
        return await redis_client.get(MEDUSA_TOKEN_KEY)

    async def _cache_token(self, token: str) -> None:
        await redis_client.set(MEDUSA_TOKEN_KEY, token, ttl=self.token_ttl)

    async def _clear_token(self) -> None:
        await redis_client.delete(MEDUSA_TOKEN_KEY)

    async def authenticate(self, max_retries: int = 3) -> str | None:
        cached_token = await self._get_cached_token()
        if cached_token:
            logger.info("Using cached medusa token")
            return cached_token

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/auth/user/emailpass",
                        json={"email": self.email, "password": self.password},
                        timeout=30.0,
                    )

                    if response.status_code == status.HTTP_200_OK:
                        data = response.json()
                        token = data.get("token")

                        if token:
                            await self._cache_token(token)
                            logger.info("Medusa token cached")
                            return token

                    logger.warning(
                        f"Medusa auth attempt {attempt + 1}/{max_retries} failed: {response.status_code}"
                    )

            except Exception as e:
                logger.warning(
                    f"Medusa auth attempt {attempt + 1}/{max_retries} error: {e}"
                )

            if attempt < max_retries - 1:
                wait_time = 2**attempt
                logger.info(f"Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)

        logger.error(f"Medusa auth failed after {max_retries} attempts")
        return None

    async def execute_request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_on_401: bool = True,
    ) -> GenericApiResponse:
        url = f"{self.base_url}{endpoint}"
        is_store_api = "/store/" in endpoint

        if is_store_api:
            headers = {"x-publishable-api-key": settings.MEDUSA_PUBLISHABLE_KEY}
        else:
            token = await self.authenticate()
            if not token:
                return GenericApiResponse(
                    success=False,
                    message="Authentication Failed",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    data=None,
                )
            headers = {"Authorization": f"Bearer {token}"}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=payload,
                    params=params,
                    headers=headers,
                    timeout=30.0,
                )

                if (
                    response.status_code == status.HTTP_401_UNAUTHORIZED
                    and retry_on_401
                    and not is_store_api
                ):
                    await self._clear_token()
                    logger.warning("Token expired, retrying")
                    return await self.execute_request(
                        endpoint=endpoint,
                        method=method,
                        payload=payload,
                        params=params,
                        retry_on_401=False,
                    )

                if response.status_code in [
                    status.HTTP_201_CREATED,
                    status.HTTP_200_OK,
                    status.HTTP_204_NO_CONTENT,
                ]:
                    data = {}
                    if (
                        response.status_code != status.HTTP_204_NO_CONTENT
                        and response.text.strip()
                    ):
                        data = response.json()

                    return GenericApiResponse(
                        success=True,
                        message=f"Calling {endpoint} successful",
                        status_code=response.status_code,
                        data=data,
                    )

                error_data = {}
                if response.text.strip():
                    try:
                        error_data = response.json()
                    except Exception:
                        error_data = {"message": response.text}

                return GenericApiResponse(
                    success=False,
                    message=f"Request to {endpoint} failed",
                    status_code=response.status_code,
                    data=error_data,
                )

        except Exception as e:
            logger.error(f"Request error: {e}")
            return GenericApiResponse(
                success=False,
                message=f"Request to {endpoint} failed: {str(e)}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                data=None,
            )

    async def complete_cart(self, cart_id: str) -> dict | None:
        result = await self.execute_request(
            endpoint=f"/store/carts/{cart_id}/complete",
            method="POST",
        )

        if not result.success:
            logger.error(
                f"Complete cart failed: {result.message} — "
                f"status={result.status_code}, data={result.data}"
            )
            return None

        if not result.data or result.data.get("type") != "order":
            logger.warning(
                f"Cart not ready for completion: {cart_id} — "
                f"type={result.data.get('type') if result.data else 'N/A'}, "
                f"data={result.data}"
            )
            return None

        order = result.data.get("order", {})
        order_id = order.get("id")

        logger.info(f"Cart completed, order created: {order_id}")
        return {"order_id": order_id}

    async def get_payment_session_id_from_cart(self, cart_id: str) -> str | None:
        result = await self.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="GET",
            params={"fields": "+payment_collection.payment_sessions"},
        )

        if not result.success:
            logger.error(f"Get cart failed: {result.message}")
            return None

        cart = result.data.get("cart", {})
        payment_collection = cart.get("payment_collection", {})
        payment_sessions = payment_collection.get("payment_sessions", [])

        if payment_sessions:
            return payment_sessions[0].get("id")

        logger.warning(f"No payment session found for cart: {cart_id}")
        return None

    async def get_payment_id_by_session(self, payment_session_id: str) -> str | None:
        result = await self.execute_request(
            endpoint="/admin/payments",
            method="GET",
            params={"payment_session_id": payment_session_id},
        )

        if not result.success:
            logger.error(f"Failed to look up payment for session: {payment_session_id}")
            return None

        payments = result.data.get("payments", [])
        if payments:
            return payments[0].get("id")

        logger.warning(f"No payment found for session: {payment_session_id}")
        return None

    async def capture_payment(self, payment_id: str) -> dict | None:
        result = await self.execute_request(
            endpoint=f"/admin/payments/{payment_id}/capture",
            method="POST",
        )

        if not result.success:
            logger.error(f"Capture failed: {result.message}")
            return None

        logger.info(f"Payment captured: {payment_id}")
        return result.data.get("payment")

    async def get_order_by_cart_id(self, cart_id: str) -> str | None:
        """Look up the Medusa order ID from a cart that was already completed."""
        result = await self.execute_request(
            endpoint="/admin/orders",
            method="GET",
            params={"cart_id": cart_id, "fields": "id"},
        )

        if not result.success:
            logger.warning(f"Order lookup by cart_id failed: {result.message}")
            return None

        orders = result.data.get("orders", [])
        if orders:
            return orders[0].get("id")

        logger.warning(f"No order found for cart: {cart_id}")
        return None

    async def process_settle_ok(self, cart_id: str) -> GenericApiResponse:
        """
        Process a Solidgate settle_ok webhook.

        Handles two scenarios:
          A) Cart not yet completed → complete it, then capture.
          B) Cart already completed by FE → look up existing order, then capture.

        After capture, writes payment_capture metadata. OrderGroove Purchase POST
        for Solidgate is triggered by the webhook handler with the payment token,
        not via enroll.
        """

        order_id: str | None = None

        # Step 1: Try to complete cart (idempotent — may already be completed)
        cart_result = await self.complete_cart(cart_id)

        if cart_result:
            order_id = cart_result["order_id"]
            logger.info(f"[settle_ok] Cart completed → order {order_id}")
        else:
            # Cart was likely already completed by the FE — look up the order
            logger.info(
                f"[settle_ok] Cart {cart_id} already completed — looking up order"
            )
            order_id = await self.get_order_by_cart_id(cart_id)
            if not order_id:
                raise WebhookProcessingError(
                    message=f"Failed to complete cart and no existing order found: {cart_id}",
                    details={
                        "step": "complete_cart",
                        "cart_id": cart_id,
                    },
                )
            logger.info(
                f"[settle_ok] Found existing order {order_id} for cart {cart_id}"
            )

        # Step 2: Get payment session
        payment_session_id = await self.get_payment_session_id_from_cart(cart_id)
        if not payment_session_id:
            raise WebhookProcessingError(
                message=f"No payment session found for cart: {cart_id}",
                details={
                    "step": "get_payment_session",
                    "cart_id": cart_id,
                    "order_id": order_id,
                },
            )

        # Step 3: Look up payment by session
        payment_id = await self.get_payment_id_by_session(payment_session_id)
        if not payment_id:
            raise WebhookProcessingError(
                message=f"No payment found for session: {payment_session_id}",
                details={
                    "step": "get_payment_id",
                    "cart_id": cart_id,
                    "order_id": order_id,
                    "payment_session_id": payment_session_id,
                },
            )

        # Step 4: Capture payment (skip if already captured)
        payment_detail = await self._get_payment_detail(payment_id)
        already_captured = bool(payment_detail and payment_detail.get("captured_at"))

        if already_captured:
            logger.info(
                f"[settle_ok] Payment {payment_id} already captured — skipping capture"
            )
        else:
            capture_result = await self.capture_payment(payment_id)
            if not capture_result:
                raise WebhookProcessingError(
                    message=f"Failed to capture payment: {payment_id}",
                    details={
                        "step": "capture_payment",
                        "cart_id": cart_id,
                        "order_id": order_id,
                        "payment_id": payment_id,
                    },
                )
            logger.info(f"[settle_ok] Payment {payment_id} captured")

        # Step 5: Write payment_capture metadata to order
        try:
            await self._write_solidgate_capture_metadata(
                order_id=order_id,
                cart_id=cart_id,
                payment_id=payment_id,
            )
        except Exception as meta_err:
            logger.warning(
                f"[settle_ok] Failed to write capture metadata for order {order_id}: {meta_err}"
            )

        # OrderGroove Purchase POST for Solidgate is triggered by the webhook handler
        # (trigger_ordergroove_purchase_post with token from settle_ok payload), not
        # via enroll. Enroll is for Netvalve (called from Medusa order-placed flow).
        # Do not call /admin/ordergroove/enroll here to avoid duplicate Purchase POST.

        logger.info("Successfully settled order: %s", order_id)
        return GenericApiResponse(
            success=True,
            message=f"{order_id} successfully settled",
            status_code=status.HTTP_200_OK,
            data={"order_id": order_id, "payment_id": payment_id, "cart_id": cart_id},
        )

    async def _get_payment_detail(self, payment_id: str) -> dict | None:
        """Fetch a single payment record to check captured_at status."""
        result = await self.execute_request(
            endpoint=f"/admin/payments/{payment_id}",
            method="GET",
        )
        if not result.success:
            return None
        return result.data.get("payment")

    async def _write_solidgate_capture_metadata(
        self,
        order_id: str,
        cart_id: str,
        payment_id: str,
    ) -> None:
        """Write payment_capture metadata for Solidgate to the Medusa order."""
        existing = await self.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="GET",
            params={"fields": "id,metadata"},
        )

        existing_metadata = {}
        if existing.success:
            existing_metadata = existing.data.get("order", {}).get("metadata", {}) or {}

        payment_capture = {
            "provider_id": "pp_solidgate_solidgate",
            "payment_label": "Solidgate",
            "payment_id": payment_id,
            "cart_id": cart_id,
            "captured_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "captured_via": "solidgate_webhook_settle_ok",
        }

        merged_metadata = {
            **existing_metadata,
            "payment_capture": payment_capture,
        }

        update_result = await self.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="POST",
            payload={"metadata": merged_metadata},
        )

        if update_result.success:
            logger.info(
                f"[settle_ok] Order {order_id} metadata updated with payment_capture"
            )
        else:
            logger.warning(
                f"[settle_ok] Failed to update order {order_id} metadata: {update_result.message}"
            )

    async def get_cart_metadata(self, cart_id: str) -> dict:
        """Get cart metadata (e.g. to check recurring_order). Uses store API."""
        result = await self.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="GET",
            params={"fields": "metadata"},
        )
        if not result.success:
            return {}
        cart = result.data.get("cart", {})
        return cart.get("metadata") or {}


medusa_service = MedusaService()
