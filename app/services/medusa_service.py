import logging
import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.core.redis import redis_client
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

        print(f"calling this: {self.base_url}/auth/user/emailpass")

        for attempt in range(max_retries):        
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/auth/user/emailpass",
                        json={
                            "email": self.email,
                            "password": self.password
                        },
                        timeout=30.0
                    )

                    if response.status_code == status.HTTP_200_OK:
                        data = response.json()
                        token = data.get("token")

                        if token:
                            await self._cache_token(token)
                            logger.info("Medusa token cached")
                            return token
                        
                    logger.warning(f"Medusa auth attempt {attempt + 1}/{max_retries} failed: {response.status_code}")

            except Exception as e:
                logger.warning(f"Medusa auth attempt {attempt + 1}/{max_retries} error: {e}")
        
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
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
                data=None
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
                    timeout=30.0
                )

                if response.status_code == status.HTTP_401_UNAUTHORIZED and retry_on_401:
                    await self._clear_token()
                    logger.warning("Token expired, retrying")
                    return await self.execute_request(
                        endpoint=endpoint,
                        method=method,
                        payload=payload,
                        params=params,
                        retry_on_401=False
                    )
                
                if response.status_code in [status.HTTP_201_CREATED, status.HTTP_200_OK, status.HTTP_204_NO_CONTENT]:
                    data = {}
                    if response.status_code != status.HTTP_204_NO_CONTENT and response.text.strip():
                        data = response.json()

                    return GenericApiResponse(
                        success=True,
                        message=f"Calling {endpoint} successful",
                        status_code=response.status_code,
                        data=data
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
                    data=error_data
                )

        except Exception as e:
            logger.error(f"Request error: {e}")
            return GenericApiResponse(
                success=False,
                message=f"Request to {endpoint} failed: {str(e)}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                data=None
            )

    async def get_payment_by_order(self, order_id: str) -> dict | None:
        result = await self.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="GET",
            params={"fields": "+payment_collection"}
        )

        # print(f"get_payment_by_order result: {result}")

        if not result.success:
            logger.error(f"Get order failed: {result.message}")
            return None
        
        order = result.data.get("order", {})
        payment_collections = order.get("payment_collections", [])

        if payment_collections:
            payments = payment_collections[0].get("payments", [])
            print(f"payments: {payments}")
            if payments:
                return {
                    "payment_id": payments[0].get("id"),
                    "amount": payments[0].get("amount"),
                    "currency_code": payments[0].get("currency_code"),
                }
        
        logger.warning(f"No payment found for order: {order_id}")
        return None
    
    async def capture_payment(self, payment_id: str) -> dict | None:
        result = await self.execute_request(
            endpoint=f"/admin/payments/{payment_id}/capture",
            method="POST"
        ) 

        print(f"capture_payment result: {result}")

        if not result.success:
            logger.error(f"Capture failed: {result.message}")
            return None

        logger.info(f"Payment captured: {payment_id}")

        print(f"result.data {result.data.get("payment")}")

        return result.data.get("payment")
    
    async def process_settle_ok(self, order_id: str) -> GenericApiResponse | None:

        print("Processing!!! process_settle_ok")

        get_payment_by_order = await self.get_payment_by_order(order_id)

        if not get_payment_by_order:
            logger.error("Failed to get payment")
            return None
        
        payment_id = get_payment_by_order.get("payment_id")
        capture_payment = await self.capture_payment(payment_id)

        if not capture_payment:
            logger.error("Failed to capture payment")
            return None
        
        print('axeee process_settle_ok')
        print(order_id)

        return GenericApiResponse(
            success=True,
            message=f"{order_id} successfully settled",
            status_code=status.HTTP_200_OK,
            data=None
        )
    
medusa_service = MedusaService()