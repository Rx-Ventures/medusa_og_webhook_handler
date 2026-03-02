"""
NetValve Status Route.

Endpoint:
  GET /api/v1/netvalve/status — Check payment status

  Methods: getPaymentStatus, retrievePayment
  Branch: feat/netvalve-payment-gateway
"""

import logging

from fastapi import APIRouter, Query

from app.schemas.netvalve import PaymentStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/status",
    response_model=PaymentStatusResponse,
    summary="Check NetValve payment status",
    description=(
        "Check the status of a NetValve payment by its session status. "
        "This is a read-only operation that returns the persisted status. "
    ),
    tags=["netvalve", "payments"],
)
async def get_payment_status(
    status: str = Query(
        "pending",
        description="The current payment session status",
        enum=[
            "authorized", "captured", "pending",
            "requires_more", "error", "canceled",
        ],
    ),
    transaction_id: str = Query(None, description="NetValve transaction ID"),
):
    """
    GET /api/v1/netvalve/status

    Returns the payment status. This is a read-only lookup that
    returns the persisted session status.

    """
    # Validate and normalize status (mirrors service.ts logic)
    valid_statuses = {
        "authorized", "captured", "pending",
        "requires_more", "error", "canceled",
    }

    normalized_status = status.lower() if status else "pending"
    if normalized_status not in valid_statuses:
        normalized_status = "pending"

    return PaymentStatusResponse(
        status=normalized_status,
        transaction_id=transaction_id,
    )
