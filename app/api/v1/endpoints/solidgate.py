import logging

from fastapi import APIRouter, HTTPException

from app.schemas.solidgate import RefundOrder, RefundResponse
from app.services.solidgate_service import solidgate_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/refund", response_model=RefundResponse)
async def refund_order(payload: RefundOrder):
    """
    Initiate a Solidgate refund (full or partial).

    - **order_id**: The Solidgate order ID for the original transaction.
    - **amount**: Refund amount in minor units (cents). e.g. $10.50 = 1050
    - **refund_reason_code**: "0022" through "0029"
    """
    try:
        result = await solidgate_service.refund_order(payload.model_dump())

        if result.get("success"):
            return RefundResponse(
                success=True,
                message="Order refunded successfully",
                data=result.get("data"),
            )
        else:
            error_data = result.get("data") or result.get("error") or {}
            error_message = (
                error_data.get("error", {}).get("message")
                if isinstance(error_data, dict)
                else str(error_data)
            ) or "Solidgate refund failed"

            raise HTTPException(
                status_code=400,
                detail={
                    "success": False,
                    "message": error_message,
                    "data": error_data,
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error during Solidgate refund: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": f"Internal server error: {str(e)}",
                "data": None,
            },
        )