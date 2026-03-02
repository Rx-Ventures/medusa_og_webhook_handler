"""
NetValve Capture Route.

Endpoint:
  POST /api/v1/netvalve/capture — Capture an authorized payment

  Method: capturePayment
  Branch: feat/netvalve-payment-gateway

If the initial POST /sale was in sale mode (not auth-only), the
capture is effectively a no-op.
"""

import logging

from fastapi import APIRouter

from app.schemas.netvalve import CaptureRequest, CaptureResponse
from app.services.netvalve_service import netvalve_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/capture",
    response_model=CaptureResponse,
    summary="Capture an authorized NetValve payment",
    description=(
        "Capture funds on a previously authorized NetValve payment. "
        "Posts to NetValve POST /capture with the transaction ID and amount. "
    ),
    tags=["netvalve", "payments"],
)
async def capture_payment(body: CaptureRequest):
    """
    POST /api/v1/netvalve/capture

    Capture authorized funds on a NetValve transaction.
    If already_captured=true, the API call is skipped (no-op).

    """
    result = await netvalve_service.capture_payment(
        transaction_id=body.transaction_id,
        amount=body.amount,
        already_captured=body.already_captured,
    )

    return CaptureResponse(
        status=result.get("status", "captured"),
        transaction_id=result.get("transaction_id"),
        response_code=result.get("response_code"),
        response_message=result.get("response_message"),
        data=result.get("data", {}),
    )
