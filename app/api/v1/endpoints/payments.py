
import logging

from fastapi import APIRouter, status

from app.schemas.payment import (
    PaymentInitializeRequest,
    PaymentInitializeResponse,
)
from app.schemas.common import GenericApiResponse
from app.services.solidgate_service import solidgate_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/initialize", status_code=status.HTTP_200_OK)
async def initialize_payment(payload: PaymentInitializeRequest) -> GenericApiResponse:
    try:
        result = solidgate_service.create_payment_intent(
            order_id=payload.order_id,
            amount=payload.amount,
            currency=payload.currency,
            customer_email=payload.customer_email,
        )
        response_data = PaymentInitializeResponse(
            session_id=payload.order_id,
            psp=payload.psp,
            merchant=result["merchant"],
            signature=result["signature"],
            payment_intent=result["payment_intent"],
        )

        print(f"response_data: {response_data}")
        
        return GenericApiResponse(
            success=True,
            message="Payment intent created successfully",
            data=response_data.model_dump()
        )
        
    except Exception as e:
        logger.error(f"Payment initialization failed: {e}")
        return GenericApiResponse(
            success=False,
            message="Failed to initialize payment",
            data=None
        )


