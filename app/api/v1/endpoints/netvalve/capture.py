"""
NetValve Capture Route.

Endpoint:
  POST /api/v1/netvalve/capture — Capture an authorized payment

  Method: capturePayment
  Branch: feat/netvalve-payment-gateway

If the initial POST /sale was in sale mode (not auth-only), the
capture is effectively a no-op.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.netvalve import CaptureRequest, CaptureResponse
from app.services.netvalve_service import netvalve_service
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/capture",
    response_model=CaptureResponse,
    summary="Capture an authorized NetValve payment",
    description=(
        "Capture funds on a previously authorized NetValve payment. "
        "Posts to NetValve POST /capture with the transaction ID and amount. "
    ),
    tags=["netvalve", "payments"],
)
async def capture_payment(body: CaptureRequest):
    """
    POST /api/v1/netvalve/capture

    Capture authorized funds on a NetValve transaction.
    If already_captured=true, the API call is skipped (no-op).

    """
    try:
        result = await netvalve_service.capture_payment(
            transaction_id=body.transaction_id,
            amount=body.amount,
            already_captured=body.already_captured,
        )
    except Exception as exc:
        logger.error("[netvalve] capture failed for txn=%s: %s", body.transaction_id, exc)
        try:
            await slack_service.send_critical_alert(
                title="NetValve Capture Failed",
                alert=(
                    f"*Transaction ID:* `{body.transaction_id}`\n"
                    f"*Error:* {exc}"
                ),
                platform="NetValve",
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Capture failed: {exc}")

    response_code_type = ((result.get("data") or {}).get("responseCodeType") or "").upper()
    if response_code_type and response_code_type != "APPROVED":
        logger.error(
            "[netvalve] capture gateway non-approval — txn=%s, responseCodeType=%s, code=%s",
            body.transaction_id,
            response_code_type,
            result.get("response_code"),
        )
        try:
            await slack_service.send_critical_alert(
                title="NetValve Capture Failed",
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

    return CaptureResponse(
        status=result.get("status", "captured"),
        transaction_id=result.get("transaction_id"),
        response_code=result.get("response_code"),
        response_message=result.get("response_message"),
        data=result.get("data", {}),
    )
