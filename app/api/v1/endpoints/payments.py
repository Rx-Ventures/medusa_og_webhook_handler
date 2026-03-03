
import logging

from fastapi import APIRouter, status

from app.schemas.payment import (
    PaymentInitializeRequest,
    PaymentInitializeResponse,
    UpdateReferenceRequest,
)
from app.schemas.common import GenericApiResponse
from app.services.solidgate_service import solidgate_service
from app.services.medusa_service import medusa_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/initialize", status_code=status.HTTP_200_OK)
async def initialize_payment(payload: PaymentInitializeRequest) -> GenericApiResponse:
    try:
        result = solidgate_service.create_payment_intent(
            order_id=payload.order_id,
            amount=payload.amount,
            currency=payload.currency,
            customer_email=payload.customer_email,
        )
        response_data = PaymentInitializeResponse(
            session_id=payload.order_id,
            psp=payload.psp,
            merchant=result["merchant"],
            signature=result["signature"],
            payment_intent=result["payment_intent"],
        )

        print(f"response_data: {response_data}")
        
        return GenericApiResponse(
            success=True,
            message="Payment intent created successfully",
            data=response_data.model_dump()
        )
        
    except Exception as e:
        logger.error(f"Payment initialization failed: {e}")
        return GenericApiResponse(
            success=False,
            message="Failed to initialize payment",
            data=None
        )


@router.post("/update-reference", status_code=status.HTTP_200_OK)
async def update_payment_reference(payload: UpdateReferenceRequest) -> GenericApiResponse:
    """
    After Solidgate checkout completes, the FE calls this endpoint with the
    cart_id (used as Solidgate order_id during payment initialisation) and
    the real Medusa order_id.

    Steps:
      1. Query Solidgate status API for the payment details (card info,
         transaction id, card token).
      2. Deep-merge the Solidgate reference into the Medusa order metadata
         so the Solidgate dashboard and Medusa admin can cross-reference.
      3. Return the enriched payload to the caller.
    """
    cart_id = payload.cart_id
    order_id = payload.order_id

    logger.info(
        "[update-reference] Mapping cart %s → order %s",
        cart_id,
        order_id,
    )

    # ── 1.  Fetch Solidgate order status ──
    solidgate_data: dict = {}
    try:
        sg_result = await solidgate_service.check_order_status(cart_id)
        if sg_result.get("success"):
            solidgate_data = sg_result.get("data", {})
            logger.info(
                "[update-reference] Solidgate status for cart %s: %s",
                cart_id,
                solidgate_data.get("order", {}).get("status"),
            )
        else:
            logger.warning(
                "[update-reference] Solidgate status lookup failed for cart %s: %s",
                cart_id,
                sg_result,
            )
    except Exception as exc:
        logger.warning(
            "[update-reference] Could not reach Solidgate status API for cart %s: %s",
            cart_id,
            exc,
        )

    # ── 2.  Build the solidgate_reference metadata block ──
    sg_order = solidgate_data.get("order", {})
    sg_transaction = solidgate_data.get("transactions", solidgate_data.get("transaction", {}))

    solidgate_reference = {
        "solidgate_order_id": cart_id,
        "solidgate_status": sg_order.get("status"),
        "solidgate_amount": sg_order.get("amount"),
        "solidgate_currency": sg_order.get("currency"),
    }

    # Transaction details (varies by Solidgate API version)
    if isinstance(sg_transaction, dict):
        solidgate_reference["solidgate_transaction_id"] = sg_transaction.get("id")
        card_token_obj = sg_transaction.get("card_token", {})
        if isinstance(card_token_obj, dict) and card_token_obj.get("token"):
            solidgate_reference["solidgate_card_token"] = card_token_obj["token"]
        card = sg_transaction.get("card", {})
        if isinstance(card, dict):
            solidgate_reference["card_number"] = card.get("card_number")
            solidgate_reference["card_brand"] = card.get("card_brand") or card.get("brand")
    elif isinstance(sg_transaction, list) and sg_transaction:
        first_txn = sg_transaction[0]
        solidgate_reference["solidgate_transaction_id"] = first_txn.get("id")

    # Strip None values for a cleaner payload
    solidgate_reference = {k: v for k, v in solidgate_reference.items() if v is not None}

    # ── 3.  Update Medusa order metadata ──
    try:
        existing = await medusa_service.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="GET",
            params={"fields": "id,metadata"},
        )

        existing_metadata = {}
        if existing.success:
            existing_metadata = existing.data.get("order", {}).get("metadata", {}) or {}

        merged_metadata = {
            **existing_metadata,
            "solidgate_reference": solidgate_reference,
        }

        update_result = await medusa_service.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="POST",
            payload={"metadata": merged_metadata},
        )

        if update_result.success:
            logger.info(
                "[update-reference] Medusa order %s metadata updated with Solidgate reference",
                order_id,
            )
        else:
            logger.warning(
                "[update-reference] Failed to update Medusa order %s metadata: %s",
                order_id,
                update_result.message,
            )
    except Exception as exc:
        logger.error(
            "[update-reference] Error updating Medusa order %s metadata: %s",
            order_id,
            exc,
        )

    # ── 4.  Trigger OrderGroove enrollment via Medusa admin API ──
    try:
        # Extract card details from the Solidgate status response
        card_details: dict = {}
        if isinstance(sg_transaction, dict):
            card = sg_transaction.get("card", {})
            if isinstance(card, dict):
                card_details["cc_number"] = card.get("card_number", "")
                card_details["cc_holder"] = card.get("card_holder", "")
                # Build exp date from month/year if available
                exp_month = card.get("card_exp_month", "")
                exp_year = card.get("card_exp_year", "")
                if exp_month and exp_year:
                    card_details["cc_exp_date"] = f"{exp_month}/{exp_year}"
                card_details["cc_type"] = (
                    card.get("card_brand") or card.get("brand") or ""
                )
            card_token_obj = sg_transaction.get("card_token", {})
            if isinstance(card_token_obj, dict):
                card_details["card_token"] = card_token_obj.get("token", "")
        elif isinstance(sg_transaction, list) and sg_transaction:
            first_txn = sg_transaction[0]
            card = first_txn.get("card", {})
            if isinstance(card, dict):
                card_details["cc_number"] = card.get("card_number", "")
                card_details["cc_holder"] = card.get("card_holder", "")
                exp_month = card.get("card_exp_month", "")
                exp_year = card.get("card_exp_year", "")
                if exp_month and exp_year:
                    card_details["cc_exp_date"] = f"{exp_month}/{exp_year}"
                card_details["cc_type"] = (
                    card.get("card_brand") or card.get("brand") or ""
                )
            card_token_obj = first_txn.get("card_token", {})
            if isinstance(card_token_obj, dict):
                card_details["card_token"] = card_token_obj.get("token", "")

        # Strip empty values
        card_details = {k: v for k, v in card_details.items() if v}

        og_payload = {"order_id": order_id, **card_details}

        og_result = await medusa_service.execute_request(
            endpoint="/admin/ordergroove/enroll",
            method="POST",
            payload=og_payload,
        )

        if og_result.success:
            logger.info(
                "[update-reference] OrderGroove enrollment triggered for order %s",
                order_id,
            )
        else:
            logger.warning(
                "[update-reference] OrderGroove enrollment failed for order %s: %s",
                order_id,
                og_result.message,
            )
    except Exception as exc:
        logger.warning(
            "[update-reference] OrderGroove enrollment error for order %s: %s",
            order_id,
            exc,
        )

    return GenericApiResponse(
        success=True,
        message="Payment reference updated",
        data={
            "cart_id": cart_id,
            "order_id": order_id,
            "solidgate_reference": solidgate_reference,
        },
    )