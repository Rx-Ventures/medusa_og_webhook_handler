"""
NetValve Reconcile Route.

Endpoint:
  POST /api/v1/netvalve/reconcile — Manually trigger Medusa order resolution

Used to re-process a NetValve payment that succeeded at the gateway but
whose webhook was missed or failed. The endpoint calls
medusa_service.process_settle_ok(cart_id) directly and is safe to call
multiple times — idempotent at the Medusa layer.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.dependencies import get_unit_of_work
from app.core.exceptions import WebhookProcessingError
from app.core.unit_of_work import UnitOfWork
from app.schemas.netvalve import ReconcileRequest, ReconcileResponse
from app.services.medusa_service import medusa_service
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/reconcile",
    response_model=ReconcileResponse,
    summary="Manually reconcile a NetValve payment",
    description=(
        "Trigger Medusa order completion for a NetValve payment whose webhook "
        "was missed or failed to process. Safe to call multiple times — Medusa "
        "handles duplicate completion gracefully. Requires the Medusa cart ID "
        "that was used as the clientOrderId in the original POST /sale."
    ),
    tags=["netvalve", "reconcile"],
)
async def reconcile_netvalve_payment(
    body: ReconcileRequest,
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """
    POST /api/v1/netvalve/reconcile

    Manually trigger Medusa order resolution for a cart whose NetValve payment
    succeeded but whose webhook was not received or not processed correctly.
    """
    cart_id = body.cart_id.strip()
    if not cart_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cart_id is required",
        )

    logger.info(
        "[netvalve] reconcile requested — cart_id=%s, force=%s",
        cart_id,
        body.force,
    )

    try:
        result = await medusa_service.process_settle_ok(cart_id)
        order_id = (result.data or {}).get("order_id") if result.data else None

        logger.info(
            "[netvalve] reconcile completed — cart_id=%s, order_id=%s",
            cart_id,
            order_id,
        )
        return ReconcileResponse(
            success=True,
            cart_id=cart_id,
            message=(
                f"Order reconciled successfully — order_id={order_id}"
                if order_id
                else "Order reconciled successfully"
            ),
            data=result.data,
        )

    except WebhookProcessingError as exc:
        logger.error(
            "[netvalve] reconcile failed for cart_id=%s: %s",
            cart_id,
            exc.message,
        )
        try:
            await slack_service.send_critical_alert(
                title="NetValve Reconcile Failed",
                alert=(
                    f"*Cart ID:* `{cart_id}`\n"
                    f"*Error:* {exc.message}"
                ),
                platform="NetValve",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reconciliation failed: {exc.message}",
        )

    except Exception as exc:
        logger.exception(
            "[netvalve] reconcile unexpected error for cart_id=%s", cart_id
        )
        try:
            await slack_service.send_critical_alert(
                title="NetValve Reconcile Unexpected Error",
                alert=(
                    f"*Cart ID:* `{cart_id}`\n"
                    f"*Error:* {exc}"
                ),
                platform="NetValve",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during reconciliation",
        )
