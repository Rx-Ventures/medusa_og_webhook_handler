"""
NetValve Refund Route.

Endpoint:
  POST /api/v1/netvalve/refund — Refund a captured payment

  Method: refundPayment
  Branch: feat/netvalve-payment-gateway
"""

import logging

from fastapi import APIRouter

from app.schemas.netvalve import RefundRequest, RefundResponse
from app.services.netvalve_service import netvalve_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/refund",
    response_model=RefundResponse,
    summary="Refund a captured NetValve payment",
    description=(
        "Refund a previously captured NetValve payment. "
        "Posts to NetValve POST /refund with the transaction ID and amount. "
    ),
    tags=["netvalve", "payments"],
)
async def refund_payment(body: RefundRequest):
    """
    POST /api/v1/netvalve/refund

    Refund a captured NetValve transaction by the given amount.

    """
    result = await netvalve_service.refund_payment(
        transaction_id=body.transaction_id,
        amount=body.amount,
    )

    return RefundResponse(
        status=result.get("status", "refunded"),
        transaction_id=result.get("transaction_id"),
        refunded_amount=result.get("refunded_amount"),
        response_code=result.get("response_code"),
        response_message=result.get("response_message"),
        data=result.get("data", {}),
    )
