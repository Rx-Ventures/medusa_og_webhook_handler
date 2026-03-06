"""
NetValve Cancel Route.

Endpoint:
  POST /api/v1/netvalve/cancel — Cancel (void) an authorized payment

  Method: cancelPayment
  Branch: feat/netvalve-payment-gateway
"""

import logging

from fastapi import APIRouter

from app.schemas.netvalve import CancelRequest, CancelResponse
from app.services.netvalve_service import netvalve_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/cancel",
    response_model=CancelResponse,
    summary="Cancel (void) a NetValve payment",
    description=(
        "Cancel (void) a previously authorized NetValve payment. "
        "Posts to NetValve POST /cancel with the transaction ID. "
    ),
    tags=["netvalve", "payments"],
)
async def cancel_payment(body: CancelRequest):
    """
    POST /api/v1/netvalve/cancel

    Void an authorized NetValve transaction.

    """
    result = await netvalve_service.cancel_payment(
        transaction_id=body.transaction_id,
    )

    return CancelResponse(
        status=result.get("status", "canceled"),
        transaction_id=result.get("transaction_id"),
        response_code=result.get("response_code"),
        response_message=result.get("response_message"),
        data=result.get("data", {}),
    )
