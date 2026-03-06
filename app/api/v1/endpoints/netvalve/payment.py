"""
NetValve Payment Routes — Sale / Authorize.

Endpoints:
  POST /api/v1/netvalve/payment — Process a payment (authorize via POST /sale)

  Methods: authorizePayment, processPaymentWithNetValve
  Branch: feat/netvalve-payment-gateway

This route replicates the 3-path authorization logic:
  Path A: HPF flow (hpf_completed=true → POST /sale CARD)
  Path B: TOKEN flow (stored netvalve_token → POST /sale TOKEN)
  Path C: External proof (webhook/HPP callback → local authorize)
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.netvalve import SaleRequest, PaymentResponse
from app.services.netvalve_service import netvalve_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/payment",
    response_model=PaymentResponse,
    summary="Process a NetValve payment (authorize)",
    description=(
        "Authorize a payment by calling NetValve POST /sale. "
        "Supports HPF card flow, stored token flow, and external proof flow. "
    ),
    tags=["netvalve", "payments"],
)
async def authorize_payment(body: SaleRequest):
    """
    POST /api/v1/netvalve/payment

    Process a payment authorization through NetValve.

    The endpoint determines the correct flow based on the input:
    - If hpf_completed=true → calls POST /sale with paymentType=CARD
    - If netvalve_token differs from hpf_payment_token → POST /sale TOKEN
    - If transaction_id/order_id present → local authorize (no API call)
    - Otherwise → returns requires_more (needs card input)

    """
    data = body.model_dump(exclude_none=True)

    result = await netvalve_service.authorize_payment(data)

    return PaymentResponse(
        status=result["status"],
        data=result.get("data", {}),
    )
