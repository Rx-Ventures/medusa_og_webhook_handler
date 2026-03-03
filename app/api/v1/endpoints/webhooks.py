import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import get_unit_of_work
from app.core.exceptions import WebhookProcessingError
from app.core.unit_of_work import UnitOfWork
from app.models.test_token_customer import TestTokenCustomer
from app.schemas.common import GenericApiResponse
from app.schemas.webhook import WebhookEventCreate, WebhookEventResponse
from app.services.idempotency_service import IdempotencyService
from app.services.medusa_service import medusa_service
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)


# OrderGroove card type codes (same as Medusa subscriber)
_OG_CARD_TYPE_MAP = {
    "visa": "1",
    "mastercard": "2",
    "american express": "3",
    "amex": "3",
    "discover": "4",
    "diners": "5",
    "diners club": "5",
    "jcb": "6",
}


def _get_solidgate_payment_token(payload: dict[str, Any]) -> str | None:
    """Extract payment/card token from Solidgate settle_ok payload for storage."""
    # Prefer card_token from the first auth transaction (reusable token)
    transactions = payload.get("transactions") or {}
    if isinstance(transactions, dict):
        for tx in transactions.values():
            if isinstance(tx, dict):
                card_token = tx.get("card_token") or tx.get("cardToken")
                if isinstance(card_token, dict):
                    token = card_token.get("token")
                    if isinstance(token, str) and token:
                        return token
                token = tx.get("card_token")  # might be string in some payloads
                if isinstance(token, str) and token:
                    return token
    # Fallback: pay_form token
    pay_form = payload.get("pay_form") or payload.get("payForm") or {}
    token = pay_form.get("token")
    if isinstance(token, str) and token:
        return token
    return None


def _get_solidgate_payment_override(payload: dict[str, Any]) -> dict[str, Any] | None:
    """
    Build payment_override for OrderGroove Purchase POST from Solidgate settle_ok.
    Returns dict with token_id, cc_number (masked), cc_type (OG code), cc_exp_date, cc_holder if available.
    """
    token_id = _get_solidgate_payment_token(payload)
    if not token_id:
        return None

    out: dict[str, Any] = {"token_id": token_id}

    transactions = payload.get("transactions") or {}
    if isinstance(transactions, dict):
        for tx in transactions.values():
            if not isinstance(tx, dict):
                continue
            card = tx.get("card")
            if isinstance(card, dict):
                num = card.get("number") or card.get("card_number")
                if isinstance(num, str) and num:
                    out["cc_number"] = num
                brand = card.get("brand") or card.get("card_type") or ""
                if isinstance(brand, str) and brand:
                    out["cc_type"] = _OG_CARD_TYPE_MAP.get(
                        brand.lower().strip(), "1"
                    )
                month = card.get("card_exp_month") or card.get("exp_month")
                year = card.get("card_exp_year") or card.get("exp_year")
                if month is not None and year is not None:
                    out["cc_exp_date"] = f"{str(month).zfill(2)}/{year}"
                break

    return out

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
    print(f"request.headers.get(solidgate-event-type): {request.headers.get("solidgate-event-type")}")
    print(f"request.headers.get(solidgate-event-id): {request.headers.get("solidgate-event-id")}")
    print("=" * 80)
    print("=" * 80)

   
    event_id = request.headers.get("solidgate-event-id")
    event_type = request.headers.get("solidgate-event-type")

    if not event_id or not event_type:
        return GenericApiResponse(success=False, message="Missing required headers: solidgate-event-id, solidgate-event-type")

    logger.info(
        "Received Solidgate webhook: event_type=%s, event_id=%s",
        event_type,
        event_id,
    )

    order = payload.get("order", {})
    cart_id = order.get("order_id") #cart_id we cant really change the structure
    order_status = order.get("status")

    webhook_data = WebhookEventCreate(
        event_id=event_id,
        psp="solidgate",
        event_type=event_type,
        medusa_order_id=cart_id, # will change this to cart_id later including column name
        payload=payload,
    )

    service = IdempotencyService(uow)
    idempotency_result = await service.check_and_create_webhook_event(webhook_data)

    if idempotency_result is None:
        logger.info("Webhook already processed or in-flight: %s", event_id)
        return GenericApiResponse(success=True, message="Event already processed")

    webhook_event_id = idempotency_result.id

    if order_status == "settle_ok":
        if not cart_id:
            error_msg = "Missing cart_id (order.order_id) in settle_ok payload"
            try:
                await uow.webhook_events.mark_as_failed(webhook_event_id, error_msg)
                await uow.commit()
            except Exception as db_err:
                logger.error(
                    "Failed to mark webhook %s as failed: %s", webhook_event_id, db_err,
                )
                try:
                    await uow.rollback()
                except Exception:
                    pass
            try:
                await slack_service.send_critical_alert(
                    title="Solidgate settle_ok — Missing cart_id",
                    alert=f"event_id: `{event_id}`\n{error_msg}",
                    platform="Solidgate",
                )
            except Exception as slack_err:
                logger.error("Failed to send Slack alert: %s", slack_err)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

        try:
            result = await medusa_service.process_settle_ok(cart_id)
            # Persist token to zzz_test_token_customer for auditing / subscription use
            payment_token = _get_solidgate_payment_token(payload)
            customer_id = (
                order.get("customer_email")
                or order.get("customer_id")
                or cart_id
            )
            if payment_token or customer_id:
                try:
                    # Use Medusa order_id so recurring can look up by originalOrderId
                    order_id_for_token = (result.data or {}).get("order_id") or cart_id
                    record = TestTokenCustomer(
                        psp="solidgate",
                        customer_id=customer_id,
                        order_id=order_id_for_token,
                        payment_token=payment_token,
                    )
                    uow.session.add(record)
                except Exception as token_err:
                    logger.warning(
                        "Failed to save Solidgate token to zzz_test_token_customer: %s",
                        token_err,
                        exc_info=True,
                    )
            # Trigger OrderGroove Purchase POST (Solidgate flow; does not use auto-capture)
            order_id = (result.data or {}).get("order_id")
            payment_override = _get_solidgate_payment_override(payload)
            if order_id and payment_override and payment_override.get("token_id"):
                try:
                    og_result = await medusa_service.trigger_ordergroove_purchase_post(
                        order_id=order_id,
                        payment_override=payment_override,
                    )
                    if og_result.success:
                        logger.info(
                            "OrderGroove Purchase POST triggered for Solidgate order %s",
                            order_id,
                        )
                    else:
                        logger.warning(
                            "OrderGroove Purchase POST failed for order %s: %s",
                            order_id,
                            og_result.message,
                        )
                except Exception as og_err:
                    logger.warning(
                        "Failed to trigger OrderGroove Purchase POST for order %s: %s",
                        order_id,
                        og_err,
                        exc_info=True,
                    )
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
            try:
                await uow.webhook_events.mark_as_failed(webhook_event_id, exc.message)
                await uow.commit()
            except Exception as db_err:
                logger.error(
                    "Failed to mark webhook %s as failed: %s", webhook_event_id, db_err,
                )
                try:
                    await uow.rollback()
                except Exception:
                    pass
            try:
                await slack_service.send_critical_alert(
                    title="Solidgate settle_ok — Processing Failed",
                    alert=(
                        f"*Step:* `{step}`\n"
                        f"*Cart:* `{cart_id}`\n"
                        f"*Event:* `{event_id}`\n"
                        f"*Error:* {exc.message}"
                    ),
                    platform="Solidgate",
                )
            except Exception as slack_err:
                logger.error("Failed to send Slack alert: %s", slack_err)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process settle_ok: {exc.message}",
            )

        except Exception as exc:
            logger.exception(
                "Unexpected error processing settle_ok for cart %s", cart_id,
            )
            try:
                await uow.webhook_events.mark_as_failed(
                    webhook_event_id, f"Unexpected error: {exc}",
                )
                await uow.commit()
            except Exception as db_err:
                logger.error(
                    "Failed to mark webhook %s as failed: %s", webhook_event_id, db_err,
                )
                try:
                    await uow.rollback()
                except Exception:
                    pass
            try:
                await slack_service.send_critical_alert(
                    title="Solidgate settle_ok — Unexpected Error",
                    alert=(
                        f"*Cart:* `{cart_id}`\n"
                        f"*Event:* `{event_id}`\n"
                        f"*Error:* {exc}"
                    ),
                    platform="Solidgate",
                )
            except Exception as slack_err:
                logger.error("Failed to send Slack alert: %s", slack_err)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while processing settle_ok",
            )

   
   
    #Non settle_ok will be marked processed here
    await uow.webhook_events.mark_as_processed(webhook_event_id)
    await uow.commit()

    return GenericApiResponse(success=True, message="Webhook processed")


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
            
