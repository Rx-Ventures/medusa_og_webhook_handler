"""
NetValve Webhook Route — idempotent, with DB tracking and Slack alerting.

Endpoint:
  POST /api/v1/netvalve/webhook — Receive webhooks from NetValve

Maps inbound webhook event types to payment actions:
  authorized       → AUTHORIZED
  captured / paid  → SUCCESSFUL
  pending          → PENDING
  requires_more    → REQUIRES_MORE
  failed / declined → FAILED
  canceled         → CANCELED

Resilience:
  - Idempotency: duplicate events are detected via event_id and silently ACKed.
  - All events are tracked in the DB (processed / failed).
  - FAILED/DECLINED events trigger a Slack critical alert.

NOTE — why process_settle_ok is NOT called here:
  For NetValve, order creation always happens synchronously *before* the
  webhook arrives:
    • HPF / TOKEN purchases: cart.complete() is called by the storefront,
      which authorises + auto-captures the payment via the subscriber.
    • Rebill / recurring orders: the recurring service calls complete_cart()
      before POSTing to /rebill, so the order already exists when NetValve
      sends the webhook.
  process_settle_ok() is a Solidgate-specific helper (completes the cart on
  settle_ok).  Invoking it here would (a) fail on already-completed carts and
  (b) corrupt NetValve order metadata with Solidgate-keyed fields.
"""

import logging
import time

from fastapi import APIRouter, Depends, Request

from app.core.dependencies import get_unit_of_work
from app.core.unit_of_work import UnitOfWork
from app.schemas.netvalve import WebhookPayload, WebhookResponse
from app.schemas.webhook import WebhookEventCreate
from app.services.idempotency_service import IdempotencyService
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
        "All events are tracked in the DB; FAILED/DECLINED events trigger a Slack alert."
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

    Receives webhook events from NetValve and maps them to internal payment
    actions (AUTHORIZED, SUCCESSFUL, FAILED, etc.).

    Order creation is handled synchronously by the storefront (HPF/TOKEN) or
    the recurring-order service (rebill) — the webhook is a notification only.
    No Medusa cart-completion is triggered here.
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
    reference_id = _extract_cart_id(payload_dict)

    logger.info(
        "[netvalve] webhook received — event_id=%s, type=%s, reference_id=%s",
        event_id,
        event_type,
        reference_id,
    )

    # Map event type → action (synchronous)
    result = netvalve_service.process_webhook(payload_dict)
    action = result.get("action", "NOT_SUPPORTED")

    # ── Idempotency check ────────────────────────────────────────────────────
    webhook_data = WebhookEventCreate(
        event_id=event_id,
        psp="netvalve",
        event_type=event_type,
        medusa_order_id=reference_id,
        payload=payload_dict,
    )

    idempotency_service = IdempotencyService(uow)
    idempotency_result = await idempotency_service.check_and_create_webhook_event(
        webhook_data
    )

    if idempotency_result is None:
        logger.info(
            "[netvalve] duplicate webhook — already processed or in-flight, event_id=%s",
            event_id,
        )
        return WebhookResponse(action=action, data=result.get("data"))

    webhook_event_id = idempotency_result.id

    # ── FAILED / DECLINED: alert ops via Slack ───────────────────────────────
    if action in ("FAILED", "CANCELED"):
        failure_detail = (
            payload_dict.get("response_message")
            or payload_dict.get("decline_reason")
            or "no detail"
        )
        logger.warning(
            "[netvalve] webhook action=%s — event_id=%s, reference_id=%s, detail=%s",
            action,
            event_id,
            reference_id,
            failure_detail,
        )
        try:
            await slack_service.send_critical_alert(
                title=f"NetValve Payment {action}",
                alert=(
                    f"*Action:* `{action}`\n"
                    f"*Event ID:* `{event_id}`\n"
                    f"*Reference:* `{reference_id}`\n"
                    f"*Detail:* {failure_detail}"
                ),
                platform="NetValve",
            )
        except Exception as slack_err:
            logger.error("[netvalve] Slack alert failed: %s", slack_err)

    # ── Mark event as processed and ACK ─────────────────────────────────────
    try:
        await uow.webhook_events.mark_as_processed(webhook_event_id)
        await uow.commit()
    except Exception as db_err:
        logger.error(
            "[netvalve] Failed to mark webhook %s as processed: %s",
            webhook_event_id,
            db_err,
        )
        try:
            await uow.rollback()
        except Exception:
            pass

    logger.info(
        "[netvalve] webhook processed — action=%s, event_id=%s",
        action,
        event_id,
    )
    return WebhookResponse(action=action, data=result.get("data"))

