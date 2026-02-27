from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import get_unit_of_work
from app.core.unit_of_work import UnitOfWork
from app.schemas.webhook import WebhookEventCreate, WebhookEventResponse
from app.schemas.common import GenericApiResponse
from app.services.idempotency_service import IdempotencyService
from app.services.medusa_service import medusa_service

import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# @router.post('/solidgate_webhook')
# async def solidgate_webhook(request: Request, db: Session = Depends(get_db_session)):
#     try:
#         body = await request.json()


#         order = body.get("order", {})
#         order_id = order.get("order_id", "")
#         order_status = order.get("status", "")

#         idempotency = await idempotency_service.create_webhook_event(db, WebhookEventCreate(
#             psp="solidgate",
#             event_type=request.headers.get("solidgate-event-type"),
#             event_id=request.headers.get("solidgate-event-id"),
#             medusa_order_id=order_id,
#             processed=True,
#             payload=body,
#         ))

#         if not idempotency:
#             logger.error(f"Webhook event log already exists for idempotency key: {request.headers.get('solidgate-event-id')}")
#             return {"message": "Webhook event log already exists", "received": body}

#         if order_status == "settle_ok":
#             result = await medusa_service.process_settle_ok(order_id)

#             if not result:
#                 raise HTTPException(
#                     status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                     detail="An unexpected error occurred"
#                 )

#             return result
        
#         return GenericApiResponse(
#             success=True,
#             message="solidgate_webhook call success", 
#             status_code=status.HTTP_201_CREATED,
#             data=body
#         )
    
#     except Exception as e:
#         logger.error(f"Webhook error: {e}")  # ✅ Now you can see real error
#         import traceback
#         traceback.print_exc()  # ✅ Full traceback
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="An unexpected error occurred"
#         )

@router.post("/solidgate", response_model=WebhookEventResponse)
async def handle_solidgate_webhook(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work)
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
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    payload = await request.json()

    event_type = payload.get("type") or payload.get("event_type") or "ordergroove.unknown"
    event_id = (
        payload.get("event_id")
        or payload.get("id")
        or request.headers.get("x-ordergroove-event-id")
        or f"og_{int(__import__('time').time() * 1000)}"
    )
    order_id = (
        payload.get("merchant_order_id")
        or payload.get("order_id")
        or payload.get("session_id")
    )

    logger.info(f"OrderGroove webhook received — event_id={event_id}, type={event_type}, order={order_id}")
    logger.info(f"OrderGroove webhook payload: {payload}")

    webhook_data = WebhookEventCreate(
        event_id=event_id,
        psp="ordergroove",
        event_type=event_type,
        medusa_order_id=order_id,
        payload=payload,
    )

    service = IdempotencyService(uow)
    idempotency_result = await service.check_and_create_webhook_event(webhook_data)

    if idempotency_result is None:
        logger.info(f"OrderGroove webhook already processed: {event_id}")
        return {"message": "Event already processed"}

    return idempotency_result
