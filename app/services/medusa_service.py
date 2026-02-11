import asyncio
import logging
from typing import Any

import httpx
from fastapi import status

from app.core.config import settings
from app.core.redis import redis_client
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

        print(f"calling this: {self.base_url}/auth/user/emailpass")

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

        token = await self.authenticate()
        if not token:
            return GenericApiResponse(
                success=False,
                message="Authentication Failed",
                status_code=status.HTTP_400_BAD_REQUEST,
                data=None,
            )

        url = f"{self.base_url}{endpoint}"
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

    async def execute_request_v2(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        include_publishable_api_key: bool = False,
        retry_on_401: bool = True,
    ) -> GenericApiResponse:
        url = f"{self.base_url}{endpoint}"

        final_headers: dict[str, str] = {}
        if headers:
            final_headers.update(headers)

        did_inject_admin_auth = False
        if "Authorization" not in final_headers:
            token = await self.authenticate()
            if not token:
                return GenericApiResponse(
                    success=False,
                    message="Authentication Failed",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    data=None,
                )

            final_headers["Authorization"] = f"Bearer {token}"
            did_inject_admin_auth = True

        if include_publishable_api_key:
            final_headers.setdefault(
                "x-publishable-api-key",
                settings.MEDUSA_PUBLISHABLE_KEY,
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=payload,
                    params=params,
                    headers=final_headers,
                    timeout=30.0,
                )

                if (
                    response.status_code == status.HTTP_401_UNAUTHORIZED
                    and retry_on_401
                    and did_inject_admin_auth
                ):
                    await self._clear_token()
                    logger.warning("Token expired, retrying")
                    return await self.execute_request_v2(
                        endpoint=endpoint,
                        method=method,
                        payload=payload,
                        params=params,
                        headers=headers,
                        include_publishable_api_key=include_publishable_api_key,
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

    async def get_payment_by_order(self, order_id: str) -> dict | None:
        result = await self.execute_request_v2(
            endpoint=f"/store/carts/{order_id}",
            method="GET",
            headers={"Authorization": f"Bearer {settings.MEDUSA_PUBLISHABLE_KEY}"},
            include_publishable_api_key=True,
            params={"fields": "+payment_collection"},
        )

        print(f"get_payment_by_order result: {result}")

        if not result.success:
            logger.error(f"Get order failed: {result.message}")
            return None

        cart = result.data.get("cart", {})
        payment_collection = cart.get("payment_collection", {})

        if payment_collection:
            payment_sessions = payment_collection.get("payment_sessions", [])
            if payment_sessions:
                session = payment_sessions[0]
                return {
                    "payment_session_id": session.get("id"),
                    "payment_collection_id": payment_collection.get("id"),
                    "amount": session.get("amount"),
                    "currency_code": session.get("currency_code"),
                }

        logger.warning(f"No payment found for order: {order_id}")
        return None

    async def capture_payment(self, payment_id: str) -> dict | None:
        result = await self.execute_request(
            endpoint=f"/admin/payments/{payment_id}/capture", method="POST"
        )

        print(f"capture_payment result: {result}")

        if not result.success:
            logger.error(f"Capture failed: {result.message}")
            return None

        logger.info(f"Payment captured: {payment_id}")

        print(f"result.data {result.data.get('payment')}")

        return result.data.get("payment")

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

    async def process_settle_ok(self, order_id: str) -> GenericApiResponse | None:

        print("Processing!!! process_settle_ok")

        payment_info = await self.get_payment_by_order(order_id)

        if not payment_info:
            logger.error("Failed to get payment info from cart")
            return None

        payment_session_id = payment_info.get("payment_session_id")
        payment_id = await self.get_payment_id_by_session(payment_session_id)

        if not payment_id:
            logger.error(
                f"Failed to resolve payment ID from session: {payment_session_id}"
            )
            return None

        capture_payment = await self.capture_payment(payment_id)

        if not capture_payment:
            logger.error("Failed to capture payment")
            return None

        print("axeee process_settle_ok")
        print(order_id)

        return GenericApiResponse(
            success=True,
            message=f"{order_id} successfully settled",
            status_code=status.HTTP_200_OK,
            data=None,
        )


medusa_service = MedusaService()
