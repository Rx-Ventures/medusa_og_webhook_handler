"""
NetValve Webhook Route — idempotent, with Medusa trigger and Slack alerting.

Endpoint:
  POST /api/v1/netvalve/webhook — Receive webhooks from NetValve

Maps inbound webhook event types to payment actions:
  authorized       → AUTHORIZED
  captured / paid  → SUCCESSFUL  (triggers Medusa order completion)
  pending          → PENDING
  requires_more    → REQUIRES_MORE
  failed / declined → FAILED
  canceled         → CANCELED

Resilience:
  - Idempotency: duplicate events are detected via event_id and silently ACKed.
  - SUCCESSFUL: triggers medusa_service.process_settle_ok(cart_id).
  - Failures: marked in the DB and alerted via Slack.
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import get_unit_of_work
from app.core.exceptions import WebhookProcessingError
from app.core.unit_of_work import UnitOfWork
from app.schemas.netvalve import WebhookPayload, WebhookResponse
from app.schemas.webhook import WebhookEventCreate
from app.services.idempotency_service import IdempotencyService
from app.services.medusa_service import medusa_service
from app.services.netvalve_service import netvalve_service
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _extract_cart_id(payload: dict) -> str | None:
    """
    Extract the Medusa cart ID from a NetValve webhook payload.

    NetValve echoes `client_order_id` (set to the cart ID in POST /sale)
    or `order_id` back in webhook events. Falls back to `session_id`.
    """
    return (
        payload.get("client_order_id")
        or payload.get("order_id")
        or payload.get("session_id")
    ) or None


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    summary="Receive NetValve webhook events",
    description=(
        "Receive and process webhook callbacks from the NetValve payment gateway. "
        "Idempotent — duplicate events are silently acknowledged. "
        "SUCCESSFUL events trigger Medusa order completion."
    ),
    tags=["netvalve", "webhooks"],
)
async def handle_netvalve_webhook(
    payload: WebhookPayload,
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """
    POST /api/v1/netvalve/webhook

    Receives webhook events from NetValve, maps them to internal payment
    actions, and — for SUCCESSFUL events — triggers Medusa order completion.
    """
    payload_dict = payload.model_dump(exclude_none=True)

    # Derive a stable event_id for idempotency
    event_id = (
        payload_dict.get("event_id")
        or payload_dict.get("id")
        or payload_dict.get("session_id")
        or request.headers.get("x-netvalve-event-id")
        or f"nv_{int(time.time() * 1000)}"
    )
    event_type = payload_dict.get("type") or "unknown"
    cart_id = _extract_cart_id(payload_dict)

    logger.info(
        "[netvalve] webhook received — event_id=%s, type=%s, cart_id=%s",
        event_id,
        event_type,
        cart_id,
    )

    # Map event type → action (synchronous)
    result = netvalve_service.process_webhook(payload_dict)
    action = result.get("action", "NOT_SUPPORTED")

    # ── Idempotency check ────────────────────────────────────────────────────
    webhook_data = WebhookEventCreate(
        event_id=event_id,
        psp="netvalve",
        event_type=event_type,
        medusa_order_id=cart_id,
        payload=payload_dict,
    )

    idempotency_service = IdempotencyService(uow)
    idempotency_result = await idempotency_service.check_and_create_webhook_event(
        webhook_data
    )

    if idempotency_result is None:
        logger.info(
            "[netvalve] webhook already processed or in-flight — event_id=%s", event_id
        )
        return WebhookResponse(action=action, data=result.get("data"))

    webhook_event_id = idempotency_result.id

    # ── SUCCESSFUL: trigger Medusa order completion ──────────────────────────
    if action == "SUCCESSFUL":
        if not cart_id:
            error_msg = (
                "Missing cart_id in NetValve SUCCESSFUL webhook payload. "
                "Expected client_order_id or order_id."
            )
            logger.error("[netvalve] %s event_id=%s", error_msg, event_id)

            try:
                await uow.webhook_events.mark_as_failed(webhook_event_id, error_msg)
                await uow.commit()
            except Exception as db_err:
                logger.error(
                    "[netvalve] Failed to mark webhook %s as failed: %s",
                    webhook_event_id,
                    db_err,
                )
                try:
                    await uow.rollback()
                except Exception:
                    pass

            try:
                await slack_service.send_critical_alert(
                    title="NetValve SUCCESSFUL — Missing cart_id",
                    alert=f"*event_id:* `{event_id}`\n{error_msg}",
                    platform="NetValve",
                )
            except Exception as slack_err:
                logger.error("[netvalve] Slack alert failed: %s", slack_err)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

        try:
            medusa_result = await medusa_service.process_settle_ok(cart_id)

            await uow.webhook_events.mark_as_processed(webhook_event_id)
            await uow.commit()

            logger.info(
                "[netvalve] SUCCESSFUL — Medusa order completed for cart_id=%s", cart_id
            )
            return WebhookResponse(action=action, data=result.get("data"))

        except WebhookProcessingError as exc:
            step = exc.details.get("step", "unknown")
            logger.error(
                "[netvalve] SUCCESSFUL processing failed for cart_id=%s at step [%s]: %s",
                cart_id,
                step,
                exc.message,
            )
            try:
                await uow.webhook_events.mark_as_failed(webhook_event_id, exc.message)
                await uow.commit()
            except Exception as db_err:
                logger.error(
                    "[netvalve] Failed to mark webhook %s as failed: %s",
                    webhook_event_id,
                    db_err,
                )
                try:
                    await uow.rollback()
                except Exception:
                    pass
            try:
                await slack_service.send_critical_alert(
                    title="NetValve SUCCESSFUL — Processing Failed",
                    alert=(
                        f"*Step:* `{step}`\n"
                        f"*Cart:* `{cart_id}`\n"
                        f"*Event:* `{event_id}`\n"
                        f"*Error:* {exc.message}"
                    ),
                    platform="NetValve",
                )
            except Exception as slack_err:
                logger.error("[netvalve] Slack alert failed: %s", slack_err)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process SUCCESSFUL event: {exc.message}",
            )

        except Exception as exc:
            logger.exception(
                "[netvalve] Unexpected error processing SUCCESSFUL for cart_id=%s",
                cart_id,
            )
            try:
                await uow.webhook_events.mark_as_failed(
                    webhook_event_id, f"Unexpected error: {exc}"
                )
                await uow.commit()
            except Exception as db_err:
                logger.error(
                    "[netvalve] Failed to mark webhook %s as failed: %s",
                    webhook_event_id,
                    db_err,
                )
                try:
                    await uow.rollback()
                except Exception:
                    pass
            try:
                await slack_service.send_critical_alert(
                    title="NetValve SUCCESSFUL — Unexpected Error",
                    alert=(
                        f"*Cart:* `{cart_id}`\n"
                        f"*Event:* `{event_id}`\n"
                        f"*Error:* {exc}"
                    ),
                    platform="NetValve",
                )
            except Exception as slack_err:
                logger.error("[netvalve] Slack alert failed: %s", slack_err)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while processing the SUCCESSFUL event",
            )

    # ── Non-SUCCESSFUL: mark as processed and ACK ────────────────────────────
    await uow.webhook_events.mark_as_processed(webhook_event_id)
    await uow.commit()

    logger.info(
        "[netvalve] webhook processed — action=%s, event_id=%s", action, event_id
    )
    return WebhookResponse(action=action, data=result.get("data"))

