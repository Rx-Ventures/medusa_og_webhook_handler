"""
NetValve Cancel Route.

Endpoint:
  POST /api/v1/netvalve/cancel — Cancel (void) an authorized payment

  Method: cancelPayment
  Branch: feat/netvalve-payment-gateway
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.netvalve import CancelRequest, CancelResponse
from app.services.netvalve_service import netvalve_service
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/cancel",
    response_model=CancelResponse,
    summary="Cancel (void) a NetValve payment",
    description=(
        "Cancel (void) a previously authorized NetValve payment. "
        "Posts to NetValve POST /cancel with the transaction ID. "
    ),
    tags=["netvalve", "payments"],
)
async def cancel_payment(body: CancelRequest):
    """
    POST /api/v1/netvalve/cancel

    Void an authorized NetValve transaction.

    """
    try:
        result = await netvalve_service.cancel_payment(
            transaction_id=body.transaction_id,
        )
    except Exception as exc:
        logger.error("[netvalve] cancel failed for txn=%s: %s", body.transaction_id, exc)
        try:
            await slack_service.send_critical_alert(
                title="NetValve Cancel Failed",
                alert=(
                    f"*Transaction ID:* `{body.transaction_id}`\n"
                    f"*Error:* {exc}"
                ),
                platform="NetValve",
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Cancel failed: {exc}")

    response_code_type = ((result.get("data") or {}).get("responseCodeType") or "").upper()
    if response_code_type and response_code_type != "APPROVED":
        logger.error(
            "[netvalve] cancel gateway non-approval — txn=%s, responseCodeType=%s, code=%s",
            body.transaction_id,
            response_code_type,
            result.get("response_code"),
        )
        try:
            await slack_service.send_critical_alert(
                title="NetValve Cancel Failed",
                alert=(
                    f"*Transaction ID:* `{body.transaction_id}`\n"
                    f"*Response Code:* `{result.get('response_code')}`\n"
                    f"*Type:* `{response_code_type}`\n"
                    f"*Message:* {result.get('response_message', 'no detail')}"
                ),
                platform="NetValve",
            )
        except Exception:
            pass

    return CancelResponse(
        status=result.get("status", "canceled"),
        transaction_id=result.get("transaction_id"),
        response_code=result.get("response_code"),
        response_message=result.get("response_message"),
        data=result.get("data", {}),
    )
