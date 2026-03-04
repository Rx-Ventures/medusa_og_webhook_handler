
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

class SolidgateWebhookBase(BaseModel):
    event: str | None = None
    order_id: str | None = None
    transaction_id: str | None = None
    amount: int | None = None
    currency: str | None = None
    status: str | None = None
    payment_token: str | None = None
    
    model_config = ConfigDict(extra="allow")
    
    def to_json(self):
        return self.model_dump_json()

class SolidgateWebhookPayload(SolidgateWebhookBase):
    event: str
    order_id: str
    transaction_id: str
    amount: int
    currency: str
    status: str


# ── Solidgate Refund Schemas ──────────────────────────────────────────


class RefundOrder(BaseModel):
    """
    Payload sent to Solidgate POST /api/v1/refund.
    amount is in minor units (cents). e.g. $10.50 = 1050
    refund_reason_code must be "0022" through "0029".
    """
    order_id: str
    amount: int
    refund_reason_code: str = Field(
        ...,
        pattern=r"^002[2-9]$",
        description='Refund reason code must be "0022" to "0029"',
    )


class RefundResponse(BaseModel):
    success: bool
    message: str
    data: dict[str, Any] | None = None
