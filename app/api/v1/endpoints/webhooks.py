import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import get_unit_of_work
from app.core.exceptions import WebhookProcessingError
from app.core.unit_of_work import UnitOfWork
from app.schemas.webhook import WebhookAck, WebhookEventCreate
from app.services.idempotency_service import IdempotencyService
from app.services.medusa_service import medusa_service
from app.services.slack_service import slack_service

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

@router.post("/solidgate")
async def handle_solidgate_webhook(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work),
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
    print(f"request.headers.get(solidgate-event-type): {request.headers.get('solidgate-event-type')}")
    print(f"request.headers.get(solidgate-event-id): {request.headers.get('solidgate-event-id')}")
    print("=" * 80)
    print("=" * 80)

   
    event_id = request.headers.get("solidgate-event-id")
    event_type = request.headers.get("solidgate-event-type")

    if not event_id or not event_type:
        await _slack_alert_safe(
            title="Solidgate Webhook — Missing Headers",
            alert="Received a Solidgate webhook without required headers "
                  "(solidgate-event-id / solidgate-event-type).",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required headers: solidgate-event-id, solidgate-event-type",
        )

    logger.info(
        "Received Solidgate webhook: event_type=%s, event_id=%s",
        event_type,
        event_id,
    )

    order = payload.get("order", {})
    cart_id = order.get("order_id")
    order_status = order.get("status")

    webhook_data = WebhookEventCreate(
        event_id=event_id,
        psp="solidgate",
        event_type=event_type,
        medusa_order_id=cart_id,
        payload=payload,
    )

    service = IdempotencyService(uow)
    idempotency_result = await service.check_and_create_webhook_event(webhook_data)

    if idempotency_result is None:
        logger.info("Webhook already processed or in-flight: %s", event_id)
        return WebhookAck(success=True, message="Event already processed")

    webhook_event_id = idempotency_result.id

    if order_status == "settle_ok":
        if not cart_id:
            error_msg = "Missing cart_id (order.order_id) in settle_ok payload"
            await _mark_failed_safe(uow, webhook_event_id, error_msg)
            await _slack_alert_safe(
                title="Solidgate settle_ok — Missing cart_id",
                alert=f"event_id: `{event_id}`\n{error_msg}",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

        try:
            result = await medusa_service.process_settle_ok(cart_id)

            await uow.webhook_events.mark_as_processed(webhook_event_id)
            await uow.commit()

            return result

        except WebhookProcessingError as exc:
            step = exc.details.get("step", "unknown")
            logger.error(
                "settle_ok processing failed for cart %s at step [%s]: %s",
                cart_id,
                step,
                exc.message,
            )
            await _mark_failed_safe(uow, webhook_event_id, exc.message)
            await _slack_alert_safe(
                title="Solidgate settle_ok — Processing Failed",
                alert=(
                    f"*Step:* `{step}`\n"
                    f"*Cart:* `{cart_id}`\n"
                    f"*Event:* `{event_id}`\n"
                    f"*Error:* {exc.message}"
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process settle_ok: {exc.message}",
            )

        except Exception as exc:
            logger.exception(
                "Unexpected error processing settle_ok for cart %s", cart_id,
            )
            await _mark_failed_safe(
                uow, webhook_event_id, f"Unexpected error: {exc}",
            )
            await _slack_alert_safe(
                title="Solidgate settle_ok — Unexpected Error",
                alert=(
                    f"*Cart:* `{cart_id}`\n"
                    f"*Event:* `{event_id}`\n"
                    f"*Error:* {exc}"
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while processing settle_ok",
            )

   
   
    #Non settle_ok will be marked processed here
    await uow.webhook_events.mark_as_processed(webhook_event_id)
    await uow.commit()

    return WebhookAck(success=True, message="Webhook processed")


async def _slack_alert_safe(
    title: str,
    alert: str,
) -> None:
    """
    Best-effort Slack alert.  Never raises — a Slack delivery failure
    must not interfere with the webhook response.
    """
    try:
        await slack_service.send_critical_alert(
            title=title,
            alert=alert,
            platform="Solidgate",
        )
    except Exception as slack_err:
        logger.error("Failed to send Slack alert: %s", slack_err)


async def _mark_failed_safe(
    uow: UnitOfWork,
    webhook_event_id: str,
    error_message: str,
) -> None:
    """
    Best-effort attempt to record the failure on the webhook event row.
    If the DB write itself fails we log the error but don't mask the
    original exception.
    """
    try:
        await uow.webhook_events.mark_as_failed(webhook_event_id, error_message)
        await uow.commit()
    except Exception as db_err:
        logger.error(
            "Failed to mark webhook %s as failed: %s", webhook_event_id, db_err,
        )
        try:
            await uow.rollback()
        except Exception:
            pass
