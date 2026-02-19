from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import get_unit_of_work
from app.core.unit_of_work import UnitOfWork
from app.schemas.webhook import WebhookEventCreate, WebhookEventResponse
from app.schemas.common import GenericApiResponse
from app.services.idempotency_service import IdempotencyService
from app.services.medusa_service import medusa_service
from app.core.config import settings

import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/solidgate", response_model=WebhookEventResponse)
async def handle_solidgate_webhook(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work)
):

    payload = await request.json()

    if settings.ENVIRONMENT == 'development':
        print('its development')
        all_headers = dict(request.headers)
        host = all_headers.get('host')

        if host != settings.TEST_HOST: # you can ouy
            print("return this bad boy!")
        else:
            print('dont return')
         
    print("=" * 80)
    print("=" * 80)
    print("=" * 80)
    print("")
    print("")
    print(payload)
    print("")
    print("")
    print("=" * 80)
    print("=" * 80)
    print("=" * 80)
    print(f"request.headers.get(solidgate-event-type): {request.headers.get("solidgate-event-type")}")
    print(f"request.headers.get(solidgate-event-id): {request.headers.get("solidgate-event-id")}")
    print("=" * 80)
    print("=" * 80)

    order = payload.get("order", {})
    cart_id = order.get("order_id") #cart_id we cant really change the structure
    order_status = order.get("status")

    webhook_data = WebhookEventCreate(
        event_id=request.headers.get("solidgate-event-id"),
        psp="solidgate",
        event_type=request.headers.get("solidgate-event-type"),
        medusa_order_id=cart_id, # will change this to cart_id later including column name
        payload=payload,
    )

    service = IdempotencyService(uow)
    idempotency_result = await service.check_and_create_webhook_event(webhook_data)
    
    if idempotency_result is None:
            logger.info(f"Webhook already processed: {webhook_data.event_id}")
            return {"message": "Event already processed"}

    if order_status == "settle_ok":
        result = await medusa_service.process_settle_ok(cart_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process settle_ok"
            )

        return result

    return GenericApiResponse(
        success=True,
        message="Webhook processed",
        status_code=status.HTTP_200_OK,
        data=payload
    )

@router.post("/ordergroove", response_model=WebhookEventResponse)
async def handle_ordergroove_webhook(
    request: Request,
):
    payload = await request.json()
          
    print("=" * 80)
    print("=" * 80)
    print("=" * 80)
    print("")
    print("")
    print(payload)
    print("")
    print("")
    print("=" * 80)
    print("=" * 80)
    print("=" * 80)
    print(f"request.headers.get(solidgate-event-type): {request.headers.get("solidgate-event-type")}")
    print(f"request.headers.get(solidgate-event-id): {request.headers.get("solidgate-event-id")}")
    print("=" * 80)
    print("=" * 80)


    return GenericApiResponse(
        success=True,
        message="Webhook processed",
        status_code=status.HTTP_200_OK,
        data=payload
    )
