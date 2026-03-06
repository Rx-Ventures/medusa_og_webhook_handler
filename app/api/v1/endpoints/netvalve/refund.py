"""
NetValve Refund Route.

Endpoint:
  POST /api/v1/netvalve/refund — Refund a captured payment

  Method: refundPayment
  Branch: feat/netvalve-payment-gateway
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.netvalve import RefundRequest, RefundResponse
from app.services.netvalve_service import netvalve_service
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/refund",
    response_model=RefundResponse,
    summary="Refund a captured NetValve payment",
    description=(
        "Refund a previously captured NetValve payment. "
        "Posts to NetValve POST /refund with the transaction ID and amount. "
    ),
    tags=["netvalve", "payments"],
)
async def refund_payment(body: RefundRequest):
    """
    POST /api/v1/netvalve/refund

    Refund a captured NetValve transaction by the given amount.

    """
    try:
        result = await netvalve_service.refund_payment(
            transaction_id=body.transaction_id,
            amount=body.amount,
        )
    except Exception as exc:
        logger.error("[netvalve] refund failed for txn=%s: %s", body.transaction_id, exc)
        try:
            await slack_service.send_critical_alert(
                title="NetValve Refund Failed",
                alert=(
                    f"*Transaction ID:* `{body.transaction_id}`\n"
                    f"*Amount:* {body.amount}\n"
                    f"*Error:* {exc}"
                ),
                platform="NetValve",
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Refund failed: {exc}")

    response_code_type = ((result.get("data") or {}).get("responseCodeType") or "").upper()
    failed_status = result.get("status", "")
    if (response_code_type and response_code_type != "APPROVED") or failed_status in ("error", "failed", "declined", "rejected"):
        logger.error(
            "[netvalve] refund non-approval — txn=%s, responseCodeType=%s, status=%s, code=%s",
            body.transaction_id,
            response_code_type,
            failed_status,
            result.get("response_code"),
        )
        try:
            await slack_service.send_critical_alert(
                title="NetValve Refund Failed",
                alert=(
                    f"*Transaction ID:* `{body.transaction_id}`\n"
                    f"*Amount:* {body.amount}\n"
                    f"*Response Code:* `{result.get('response_code')}`\n"
                    f"*Type:* `{response_code_type}`\n"
                    f"*Message:* {result.get('response_message', 'no detail')}"
                ),
                platform="NetValve",
            )
        except Exception:
            pass

    return RefundResponse(
        status=result.get("status", "refunded"),
        transaction_id=result.get("transaction_id"),
        refunded_amount=result.get("refunded_amount"),
        response_code=result.get("response_code"),
        response_message=result.get("response_message"),
        data=result.get("data", {}),
    )
