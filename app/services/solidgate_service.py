
import json
import logging
from typing import Any

import httpx
from solidgate import ApiClient

from app.core.config import settings
from app.core.security import SignatureService

logger = logging.getLogger(__name__)


class SolidgateService:
    def __init__(self):
        self.public_key = settings.SOLIDGATE_PUBLIC_KEY
        self.secret_key = settings.SOLIDGATE_SECRET_KEY
        self.client = ApiClient(self.public_key, self.secret_key)
        self.signature_service = SignatureService(self.public_key, self.secret_key)

    def generate_signature(self, payload: str, method: str = "POST") -> str:
        return self.signature_service.generate_signature(payload, method)

    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        return self.signature_service.verify_signature(payload, signature)

    async def execute_request(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        method: str = "POST"
    ) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            payload_json = ""
            if payload:
                payload_json = json.dumps(
                    payload,
                    separators=(",", ":"),
                    sort_keys=True,
                    ensure_ascii=True
                )

            signature = self.generate_signature(payload_json, method)

            headers = {
                "Content-Type": "application/json",
                "merchant": self.public_key,
                "Signature": signature
            }

            try:
                if method == "GET":
                    response = await client.get(endpoint, headers=headers, timeout=30.0)
                elif method == "DELETE":
                    response = await client.delete(endpoint, headers=headers, timeout=30.0)
                else:
                    response = await client.request(
                        method=method,
                        url=endpoint,
                        content=payload_json,
                        headers=headers,
                        timeout=30.0
                    )

                if response.status_code in [200, 201, 204]:
                    result_data = {}
                    if response.status_code != 204 and response.text.strip():
                        try:
                            result_data = response.json()
                        except Exception:
                            result_data = {}

                    if "error" in result_data:
                        return {"success": False, "status_code": response.status_code, "data": result_data}

                    return {"success": True, "status_code": response.status_code, "data": result_data}
                else:
                    try:
                        result_data = response.json() if response.text.strip() else {"error": f"HTTP {response.status_code}"}
                    except Exception:
                        result_data = {"error": f"HTTP {response.status_code}"}

                    return {"success": False, "status_code": response.status_code, "data": result_data}

            except Exception as e:
                logger.error(f"Request error: {e}")
                return {"success": False, "status_code": 0, "error": {"message": str(e)}}

    def create_payment_intent(
        self,
        order_id: str,
        amount: int,
        currency: str,
        customer_email: str,
        order_description: str = "Payment",
        success_url: str | None = None,
        fail_url: str | None = None,
    ) -> dict[str, Any]:
        intent = {
            "order_id": order_id,
            "amount": amount,
            "currency": currency,
            "order_description": order_description,
            "customer_email": customer_email,
            "success_url": success_url or settings.SOLIDGATE_SUCCESS_URL,
            "fail_url": fail_url or settings.SOLIDGATE_FAIL_URL,
        }

        merchant_data = self.client.form_merchant_data(intent)

        print(f"merchant_data: {merchant_data}")

        return {
            "payment_intent": merchant_data.payment_intent,
            "merchant": merchant_data.merchant,
            "signature": merchant_data.signature,
        }

    async def check_order_status(self, order_id: str) -> dict[str, Any]:
        return await self.execute_request(
            endpoint="https://pay.solidgate.com/api/v1/status",
            payload={"order_id": order_id},
            method="POST"
        )

    def extract_order_id(self, webhook_payload: dict) -> str | None:
        return webhook_payload.get("order", {}).get("order_id")

    def extract_order_status(self, webhook_payload: dict) -> str | None:
        return webhook_payload.get("order", {}).get("status")

    def extract_payment_token(self, webhook_payload: dict) -> str | None:
        return webhook_payload.get("transaction", {}).get("card_token", {}).get("token")

    async def recurring(
        self,
        order_id: str,
        amount: int,
        currency: str,
        recurring_token: str,
        order_description: str = "Recurring order",
        customer_email: str = "",
    ) -> dict[str, Any]:
        """
        Call Solidgate POST /recurring for 1-click recurring charge.
        order_id: new order/cart id for this transaction.
        amount: in minor units (cents).
        """
        payload: dict[str, Any] = {
            "order_id": order_id,
            "amount": amount,
            "currency": currency,
            "order_description": order_description,
            "type": "auth",
            "authorization_type": "final",
            "payment_type": "1-click",
            "recurring_token": recurring_token,
            "platform": "WEB",
            "settle_interval": 0,
            "ip_address": (settings.SOLIDGATE_RECURRING_IP or "203.0.113.0").strip(),
        }
        if customer_email:
            payload["customer_email"] = customer_email
        return await self.execute_request(
            endpoint=f"{settings.SOLIDGATE_API_URL}/recurring",
            payload=payload,
            method="POST",
        )

    async def refund_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Call Solidgate POST /api/v1/refund.
        payload: { order_id: str, amount: int (cents), refund_reason_code: str }
        """
        logger.info(f"Initiating Solidgate refund — order_id={payload.get('order_id')}, "
                     f"amount={payload.get('amount')}, reason={payload.get('refund_reason_code')}")
        result = await self.execute_request(
            endpoint=f"{settings.SOLIDGATE_API_URL}/refund",
            payload=payload,
            method="POST",
        )
        if result.get("success"):
            logger.info(f"Solidgate refund succeeded — order_id={payload.get('order_id')}")
        else:
            logger.error(f"Solidgate refund failed — order_id={payload.get('order_id')}, "
                         f"response={result}")
        return result


solidgate_service = SolidgateService()