import base64
import json
import logging
from urllib.parse import quote
import httpx
from fastapi import status
from Crypto.Cipher import AES

from typing import Any

from app.core.config import settings
from app.schemas.ordergroove import (
    PurchasePostRequest,
    PurchasePostResponse,
)
from app.schemas.common import GenericApiResponse

logger = logging.getLogger(__name__)

class OrderGrooveService:
    BLOCK_SIZE = 32
    PADDING = "{"

    def __init__(self):
        self.api_key = settings.ORDERGROOVE_API_KEY
        self.hash_key = settings.ORDERGROOVE_HASH_KEY
        self.merchant_id = settings.ORDERGROOVE_MERCHANT_ID

        #prod for now lets put condition for staging test this first 
        self.rest_api_url = "https://restapi.ordergroove.com"
        self.sc_api_url = "https://sc.ordergroove.com"

    
    def _pad(self, data: str) -> bytes:
        if isinstance(data,str):
            data = data.encode("utf-8")
        padding_length = self.BLOCK_SIZE - (len(data) % self.BLOCK_SIZE)
        return data + (self.PADDING.encode("utf-8")) * padding_length
    
    def _encrypt_aes(self, data: str) -> str:
        if not self.hash_key:
            raise ValueError("OG hashkey not configured")
        
        print(f"self.hash_key: {self.hash_key}")
        
        cipher = AES.new(self.hash_key.encode("utf-8"), AES.MODE_ECB)
        padded_data = self._pad(data)
        encrypted = cipher.encrypt(padded_data)
        return base64.b64encode(encrypted).decode("utf-8")
    
    def _encrypt_payment_fields(self, payload: dict) -> dict:
        if "payment" not in payload or payload["payment"] is None:
            return payload
        
        payment = payload["payment"]

        if payment.get("cc_number"):
            payment["cc_number"] = self._encrypt_aes(payment["cc_number"])

        if payment.get("cc_holder"):
            payment["cc_holder"] = self._encrypt_aes(payment["cc_holder"])

        if payment.get("cc_exp_date"):
            payment["cc_exp_date"] = self._encrypt_aes(payment["cc_exp_date"])

        return payload
    
    def _build_request_body(self, payload: dict) -> str:
        json_payload = json.dumps(payload, separators=(",",":"))
        encoded_json = quote(json_payload)
        return f"create_request={encoded_json}"
    

    async def execute_request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        use_sc_api: bool = False
    ) -> GenericApiResponse:
        if use_sc_api:
            url = f"{self.sc_api_url}{endpoint}" 
        else:
            url = f"{self.rest_api_url}{endpoint}"

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": self.api_key
        }

        try: 
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=payload,
                    params=params,
                    headers=headers
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

                if response.status_code == status.HTTP_207_MULTI_STATUS: 
                    data = response.json() if response.text.strip() else {}
                    return GenericApiResponse(
                        success=True,
                        message="Partial success",
                        status_code=response.status_code,
                        data=data,
                    )

                if response.status_code == status.HTTP_409_CONFLICT:
                    return GenericApiResponse(
                        success=False,
                        message=f"Request to {endpoint} failed",
                        status_code=response.status_code,
                        data=None,
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
            logger.error(f"OG request error: {e}")
            return GenericApiResponse(
                success=False,
                message=f"Request to {endpoint} failed: {str(e)}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                data=None,
            )

    async def send_purchase_post(
        self,
        request: PurchasePostRequest
    ) -> PurchasePostResponse:
        payload = request.model_dump(exclude_none=True)
        payload = self._encrypt_payment_fields(payload)

        json_payload = json.dumps(payload, separators=(",",":"))
        encoded_json = quote(json_payload)
        body = f"create_request={encoded_json}"

        #static later
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": self.api_key
        }

        url = f"{self.sc_api_url}/subscription/create"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers=headers
                )

            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response body: {response.text}")

            if response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_206_PARTIAL_CONTENT):
                return PurchasePostResponse(**response.json())
            
            if response.status_code == status.HTTP_207_MULTI_STATUS:
                data =response.json()
                return PurchasePostResponse(
                    result="Partial success",
                    error_message=str(data.get("errors", [])),
                )
            
            if response.status_code == status.HTTP_409_CONFLICT:
                return PurchasePostResponse(
                    error="Conflict",
                    error_message="merchant_order_id already exists",
                )
            
            error_data = response.json() if response.text.strip() else {}
            return PurchasePostResponse(
                error="error",
                error_message=error_data.get("error_message")
            )
        
        except Exception as e:
            logger.error(f"Purchase POST error: {e}")
            return PurchasePostResponse(
                error="Request failed",
                error_message=str(e),
            )
        
    async def get_product(self, product_id: str) -> GenericApiResponse:
        return await self.execute_request(
            endpoint=f"/products/{product_id}/",
            method="GET",
        )
    
    async def get_products(self) -> GenericApiResponse:
         return await self.execute_request(
            endpoint=f"/products/",
            method="GET",
        )
    
    async def create_products(
        self,
        products: list[dict[str, Any]] 
    ) -> GenericApiResponse:
        return await self.execute_request(
            endpoint="/products-batch/create/",
            method="POST",
            payload=products,
        )
    
    async def update_products(
        self,
        products: list[dict[str, Any]],  
    ) -> GenericApiResponse:
        return await self.execute_request(
            endpoint="/products-batch/update/",
            method="PATCH",
            payload=products,
        )
    
    async def get_subscription(self, subscription_id: str) -> GenericApiResponse:
        return await self.execute_request(
            endpoint=f"/subscriptions/{subscription_id}/",
            method="GET",
        )
    
    async def list_subscription(
        self,
        customer_id: str | None = None  
    ) -> GenericApiResponse:
        params = {}
        if customer_id:
            params["customer"] = customer_id

        return await self.execute_request(
            endpoint="/subscriptions/",
            method="GET",
            params=params,
        )

    async def cancel_subscription(self, subscription_id: str) -> GenericApiResponse:
        return await self.execute_request(
            endpoint=f"/subscriptions/{subscription_id}/cancel/",
            method="PATCH",
        )

    async def get_customer(self, customer_id: str) -> GenericApiResponse:
        return await self.execute_request(
            endpoint=f"/customers/{customer_id}/",
            method="GET",
        )
    
    async def get_order(self, order_id: str) -> GenericApiResponse:
        return await self.execute_request(
            endpoint=f"/orders/{order_id}/",
            method="GET",
        )
    
    async def list_orders(
        self,
        customer_id: str | None = None,
        order_status: int | None = None,
    ) -> GenericApiResponse:
        params = {}
        if customer_id:
            params["customer"] = customer_id
        if order_status is not None:
            params["status"] = order_status

        return await self.execute_request(
            endpoint="/orders/",
            method="GET",
            params=params,
        )
    
    async def get_purchase_post_status(self, subs_req_id: str) -> GenericApiResponse:
        return await self.execute_request(
            endpoint=f"/subscription/status/{subs_req_id}",
            method="GET",
            use_sc_api=True,
        )
    
ordergroove_service = OrderGrooveService()