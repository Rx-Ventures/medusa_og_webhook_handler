import logging
from datetime import datetime
from app.core.database import get_db_session
from sqlalchemy.orm import Session

from fastapi import APIRouter, Header, HTTPException, Request, status, Depends
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.webhook import WebhookEvent
from app.schemas.common import GenericApiResponse
from app.services.idempotency_service import idempotency_service
from app.services.solidgate_service import solidgate_service
from app.services.medusa_service import medusa_service

from app.schemas.webhook import WebhookEventCreate, WebhookEventResponse

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post('/solidgate_webhook')
async def solidgate_webhook(request: Request, db: Session = Depends(get_db_session)):
    try:
        body = await request.json()

        print("=" * 80)
        print("=" * 80)
        print("=" * 80)
        print("")
        print("")
        print(body)
        print("")
        print("")
        print("=" * 80)
        print("=" * 80)
        print("=" * 80)
        print(f"request.headers.get(solidgate-event-type): {request.headers.get("solidgate-event-type")}")
        print(f"request.headers.get(solidgate-event-id): {request.headers.get("solidgate-event-id")}")
        print("=" * 80)
        print("=" * 80)

        order = body.get("order", {})
        order_id = order.get("order_id", "")
        order_status = order.get("status", "")

        idempotency = await idempotency_service.create_webhook_event(db, WebhookEventCreate(
            psp="solidgate",
            event_type=request.headers.get("solidgate-event-type"),
            event_id=request.headers.get("solidgate-event-id"),
            medusa_order_id=order_id,
            processed=True,
            payload=body,
        ))

        if not idempotency:
            logger.error(f"Webhook event log already exists for idempotency key: {request.headers.get('solidgate-event-id')}")
            return {"message": "Webhook event log already exists", "received": body}

        if order_status == "settle_ok":
            result = await medusa_service.process_settle_ok(order_id)

            if not result:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="An unexpected error occurred"
                )

            return result
        
        return GenericApiResponse(
            success=True,
            message="solidgate_webhook call success", 
            status_code=status.HTTP_201_CREATED,
            data=body
        )
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")  # ✅ Now you can see real error
        import traceback
        traceback.print_exc()  # ✅ Full traceback
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )