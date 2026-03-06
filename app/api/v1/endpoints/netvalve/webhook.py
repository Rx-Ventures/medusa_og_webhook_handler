"""
NetValve Webhook Route.

Endpoint:
  POST /api/v1/netvalve/webhook — Receive webhooks from NetValve

  Method: getWebhookActionAndData
  Branch: feat/netvalve-payment-gateway

Maps inbound webhook event types to payment actions:
  authorized       → AUTHORIZED
  captured / paid  → SUCCESSFUL
  pending          → PENDING
  requires_more    → REQUIRES_MORE
  failed / declined → FAILED
  canceled         → CANCELED
"""

import logging

from fastapi import APIRouter, Request

from app.schemas.netvalve import WebhookPayload, WebhookResponse
from app.services.netvalve_service import netvalve_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    summary="Receive NetValve webhook events",
    description=(
        "Receive and process webhook callbacks from the NetValve payment gateway. "
        "Maps event types to internal payment actions. "
    ),
    tags=["netvalve", "webhooks"],
)
async def handle_netvalve_webhook(payload: WebhookPayload, request: Request):
    """
    POST /api/v1/netvalve/webhook

    Receives webhook events from NetValve and maps them to internal
    payment actions (AUTHORIZED, SUCCESSFUL, FAILED, etc.).

    """
    payload_dict = payload.model_dump(exclude_none=True)

    logger.info(
        f"[netvalve] webhook received — type={payload_dict.get('type')}, "
        f"keys={list(payload_dict.keys())}"
    )

    result = netvalve_service.process_webhook(payload_dict)

    logger.info(
        f"[netvalve] webhook processed — action={result.get('action')}"
    )

    return WebhookResponse(
        action=result["action"],
        data=result.get("data"),
    )
