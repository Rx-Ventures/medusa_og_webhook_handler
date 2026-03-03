"""
OrderGroove Recurring Order Processing Service.

Handles the full flow when OrderGroove sends a recurring order placement:
  1. Query the original Medusa order to get the Netvalve transactionID from metadata
  2. Create a new Medusa order (cart → line items → shipping → payment → complete)
  3. Call Netvalve POST /rebill with the original transactionID + new order amount
  4. Capture the payment on the newly created order

The Netvalve /rebill endpoint charges the same card used in the original transaction
without needing card details or a payment token again.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.services.medusa_service import MedusaService

logger = logging.getLogger(__name__)

NETVALVE_SANDBOX_BASE_URL = "https://payment-api.uat.sandbox-netvalve.com"
NETVALVE_PRODUCTION_BASE_URL = "https://api.netvalve.com"


class OrderGrooveRecurringService:
    def __init__(self):
        self.medusa = MedusaService()
        self.netvalve_base_url = self._resolve_netvalve_base_url()

    def _resolve_netvalve_base_url(self) -> str:
        if settings.NETVALVE_PAYMENT_API_URL:
            return settings.NETVALVE_PAYMENT_API_URL
        if settings.NETVALVE_BASE_URL:
            return settings.NETVALVE_BASE_URL
        if settings.NETVALVE_ENVIRONMENT == "sandbox":
            return NETVALVE_SANDBOX_BASE_URL
        return NETVALVE_PRODUCTION_BASE_URL

    async def process_recurring_order(
        self,
        og_order_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Full flow:
          1. Extract data from OG XML payload
          2. Get original order metadata (for Netvalve transactionID)
          3. Create new Medusa order
          4. Rebill via Netvalve
          5. Capture payment on new order
        """
        head = og_order_data.get("head", {})
        customer = og_order_data.get("customer", {})
        items_data = og_order_data.get("items", {})

        og_order_id = head.get("orderOgId", "")
        total_value = head.get("orderTotalValue", "0")
        currency = head.get("orderCurrency", "USD")

        item_list = items_data.get("item", [])
        if isinstance(item_list, dict):
            item_list = [item_list]

        original_order_id = None
        for item in item_list:
            sub = item.get("subscription", {})
            if isinstance(sub, dict) and sub.get("originalOrderId"):
                original_order_id = sub["originalOrderId"]
                break

        if not original_order_id:
            raise RecurringOrderError(
                "No originalOrderId found in OG items",
                step="extract_original_order_id",
            )

        customer_id = customer.get("customerPartnerId", "")
        customer_email = customer.get("customerEmail", "")
        region_id = None

        logger.info(
            f"[og-recurring] Processing OG order {og_order_id} — "
            f"original={original_order_id}, customer={customer_id}, "
            f"total={total_value} {currency}, items={len(item_list)}"
        )

        # ── Step 1: Query original order for Netvalve transactionID ──
        transaction_id = await self._get_original_transaction_id(original_order_id)
        logger.info(
            f"[og-recurring] Original order {original_order_id} → "
            f"transactionID={transaction_id}"
        )

        # ── Step 2: Get region + shipping from original order ──
        original_order_details = await self._get_order_details(original_order_id)
        if original_order_details:
            region_id = original_order_details.get("region_id")

        if not region_id:
            region_id = await self._get_default_region_id()

        # ── Step 3: Create new Medusa order ──
        new_order = await self._create_medusa_order(
            customer_id=customer_id,
            customer_email=customer_email,
            region_id=region_id,
            items=item_list,
            original_order=original_order_details,
            og_customer=customer,
            currency=currency,
            original_transaction_id=transaction_id,
        )
        new_order_id = new_order["order_id"]
        new_cart_id = new_order["cart_id"]
        cart_total = new_order.get("cart_total", 0.0)
        logger.info(
            f"[og-recurring] New Medusa order created — "
            f"order_id={new_order_id}, cart_id={new_cart_id}"
        )

        # ── Step 4: Rebill via Netvalve ──
        # Use Medusa cart total (includes shipping) instead of OG orderTotalValue
        amount = cart_total if cart_total > 0 else float(total_value)
        logger.info(
            f"[og-recurring] Rebill amount={amount} "
            f"(cart_total={cart_total}, og_total={total_value})"
        )
        rebill_result = await self._netvalve_rebill(
            transaction_id=transaction_id,
            amount=amount,
            client_order_id=new_order_id,
        )
        logger.info(
            f"[og-recurring] Netvalve rebill success — "
            f"new transactionID={rebill_result.get('transactionId')}"
        )

        # ── Step 5: Capture payment on new order ──
        capture_result = await self._capture_new_order_payment(new_cart_id)
        logger.info(
            f"[og-recurring] Payment captured for order {new_order_id}"
        )

        return {
            "og_order_id": og_order_id,
            "original_order_id": original_order_id,
            "new_order_id": new_order_id,
            "new_cart_id": new_cart_id,
            "transaction_id": transaction_id,
            "rebill_transaction_id": rebill_result.get("transactionID") or rebill_result.get("transactionId"),
            "amount": amount,
            "currency": currency,
        }

    # ──────────────────────────────────────────────────────────────
    # Step 1: Get transactionID from original order metadata
    # ──────────────────────────────────────────────────────────────

    async def _get_original_transaction_id(self, order_id: str) -> str:
        result = await self.medusa.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="GET",
            params={"fields": "id,metadata"},
        )

        if not result.success:
            raise RecurringOrderError(
                f"Failed to fetch original order {order_id}: {result.message}",
                step="get_original_order",
            )

        order = result.data.get("order", {})
        metadata = order.get("metadata", {})

        payment_capture = metadata.get("payment_capture", {})
        transaction_id = (
            payment_capture.get("transactionId")
            or payment_capture.get("netvalve_transaction_id")
        )

        if not transaction_id:
            payments = payment_capture.get("payments", [])
            for p in payments:
                tid = p.get("transactionId") or p.get("netvalve_transaction_id")
                if tid:
                    transaction_id = tid
                    break

        if not transaction_id:
            raise RecurringOrderError(
                f"No Netvalve transactionID in order {order_id} metadata. "
                f"metadata.payment_capture={payment_capture}",
                step="extract_transaction_id",
            )

        return str(transaction_id)

    async def _get_order_details(self, order_id: str) -> dict | None:
        result = await self.medusa.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="GET",
            params={
                "fields": (
                    "id,region_id,currency_code,"
                    "shipping_address.*,billing_address.*,"
                    "shipping_methods.*"
                ),
            },
        )
        if not result.success:
            logger.warning(
                f"[og-recurring] Could not fetch order details for {order_id}: "
                f"{result.message} — data={result.data}"
            )
            return None

        order = result.data.get("order", {})

        for pc_fields in (
            "+payment_collection.payment_sessions",
            "+payment_collection",
            "payment_collection",
        ):
            pc_result = await self.medusa.execute_request(
                endpoint=f"/admin/orders/{order_id}",
                method="GET",
                params={"fields": pc_fields},
            )
            if pc_result.success:
                pc_order = pc_result.data.get("order", {})
                pc = pc_order.get("payment_collection")
                if pc:
                    order["payment_collection"] = pc
                    logger.info(
                        f"[og-recurring] Original order payment_collection loaded "
                        f"(fields={pc_fields}) — sessions={len(pc.get('payment_sessions', []))}"
                    )
                    break
            else:
                logger.debug(
                    f"[og-recurring] payment_collection query failed with "
                    f"fields={pc_fields}: {pc_result.message}"
                )
        else:
            logger.warning(
                f"[og-recurring] Could not fetch payment_collection for {order_id} "
                f"— all field patterns failed"
            )

        return order

    @staticmethod
    def _build_address_payload(
        original_order: dict | None,
        og_customer: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build the shipping_address + billing_address payload for the cart.
        Prefers the original Medusa order's addresses; falls back to the OG
        customer XML data so the cart always has valid addresses.
        """
        if original_order:
            shipping = original_order.get("shipping_address") or {}
            billing = original_order.get("billing_address") or shipping
            if shipping.get("address_1"):
                return {
                    "shipping_address": {
                        "first_name": shipping.get("first_name", ""),
                        "last_name": shipping.get("last_name", ""),
                        "address_1": shipping.get("address_1", ""),
                        "address_2": shipping.get("address_2", ""),
                        "city": shipping.get("city", ""),
                        "province": shipping.get("province", ""),
                        "postal_code": shipping.get("postal_code", ""),
                        "country_code": shipping.get("country_code", "ph"),
                        "phone": shipping.get("phone", ""),
                    },
                    "billing_address": {
                        "first_name": billing.get("first_name", ""),
                        "last_name": billing.get("last_name", ""),
                        "address_1": billing.get("address_1", ""),
                        "address_2": billing.get("address_2", ""),
                        "city": billing.get("city", ""),
                        "province": billing.get("province", ""),
                        "postal_code": billing.get("postal_code", ""),
                        "country_code": billing.get("country_code", "ph"),
                        "phone": billing.get("phone", ""),
                    },
                }

        ship_addr = {
            "first_name": og_customer.get("customerShippingFirstName", ""),
            "last_name": og_customer.get("customerShippingLastName", ""),
            "address_1": og_customer.get("customerShippingAddress1", ""),
            "address_2": og_customer.get("customerShippingAddress2", ""),
            "city": og_customer.get("customerShippingCity", ""),
            "province": og_customer.get("customerShippingState", ""),
            "postal_code": og_customer.get("customerShippingZip", ""),
            "country_code": og_customer.get("customerShippingCountry", "ph"),
            "phone": og_customer.get("customerShippingPhone", ""),
        }
        bill_addr = {
            "first_name": og_customer.get("customerBillingFirstName", ""),
            "last_name": og_customer.get("customerBillingLastName", ""),
            "address_1": og_customer.get("customerBillingAddress1", ""),
            "address_2": og_customer.get("customerBillingAddress2", ""),
            "city": og_customer.get("customerBillingCity", ""),
            "province": og_customer.get("customerBillingState", ""),
            "postal_code": og_customer.get("customerBillingZip", ""),
            "country_code": og_customer.get("customerBillingCountry", "ph"),
            "phone": og_customer.get("customerBillingPhone", ""),
        }

        logger.info("[og-recurring] Using OG customer data for addresses (original order unavailable)")
        return {"shipping_address": ship_addr, "billing_address": bill_addr}

    async def _get_default_region_id(self) -> str:
        result = await self.medusa.execute_request(
            endpoint="/admin/regions",
            method="GET",
        )
        if result.success:
            regions = result.data.get("regions", [])
            if regions:
                return regions[0]["id"]
        raise RecurringOrderError(
            "No regions found in Medusa",
            step="get_default_region",
        )

    # ──────────────────────────────────────────────────────────────
    # Step 2: Create a new Medusa order
    # ──────────────────────────────────────────────────────────────

    async def _create_medusa_order(
        self,
        customer_id: str,
        customer_email: str,
        region_id: str,
        items: list[dict],
        original_order: dict | None,
        og_customer: dict[str, Any],
        currency: str,
        original_transaction_id: str = "",
    ) -> dict[str, str]:
        # 2a. Create cart
        cart_payload: dict[str, Any] = {"region_id": region_id}
        if customer_email:
            cart_payload["email"] = customer_email

        result = await self.medusa.execute_request(
            endpoint="/store/carts",
            method="POST",
            payload=cart_payload,
        )
        if not result.success:
            raise RecurringOrderError(
                f"Failed to create cart: {result.message}",
                step="create_cart",
            )
        cart = result.data.get("cart", {})
        cart_id = cart.get("id")
        logger.info(f"[og-recurring] Cart created: {cart_id}")

        # 2b. Add line items (variant_id from product_id in OG XML)
        for item in items:
            variant_id = item.get("product_id", "")
            qty = int(item.get("qty", "1"))

            if not variant_id:
                logger.warning(f"[og-recurring] Skipping item with no product_id: {item}")
                continue

            add_result = await self.medusa.execute_request(
                endpoint=f"/store/carts/{cart_id}/line-items",
                method="POST",
                payload={
                    "variant_id": variant_id,
                    "quantity": qty,
                },
            )
            if not add_result.success:
                raise RecurringOrderError(
                    f"Failed to add item {variant_id} to cart: {add_result.message}",
                    step="add_line_item",
                )
            logger.info(f"[og-recurring] Added variant {variant_id} x{qty} to cart {cart_id}")

        # 2c. Set addresses — from original order, or fall back to OG customer data
        address_payload = self._build_address_payload(original_order, og_customer)
        addr_result = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="POST",
            payload=address_payload,
        )
        if not addr_result.success:
            raise RecurringOrderError(
                f"Failed to set addresses on cart {cart_id}: {addr_result.message}",
                step="set_addresses",
            )
        logger.info(f"[og-recurring] Addresses set on cart {cart_id}")

        # 2d. Add shipping method (use first available)
        shipping_result = await self.medusa.execute_request(
            endpoint="/store/shipping-options",
            method="GET",
            params={"cart_id": cart_id},
        )
        if shipping_result.success:
            options = shipping_result.data.get("shipping_options", [])
            if options:
                option_id = options[0].get("id")
                await self.medusa.execute_request(
                    endpoint=f"/store/carts/{cart_id}/shipping-methods",
                    method="POST",
                    payload={"option_id": option_id},
                )
                logger.info(f"[og-recurring] Shipping method {option_id} added to cart {cart_id}")

        # 2e. Copy payment collection from original order
        await self._init_payment_from_original_order(
            cart_id, original_order, original_transaction_id
        )

        # 2f. Fetch cart total (includes shipping) before completing
        cart_result = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="GET",
        )
        cart_total = 0.0
        if cart_result.success:
            cart_data = cart_result.data.get("cart", {})
            raw_total = cart_data.get("total")
            if raw_total is not None:
                cart_total = float(raw_total)
            logger.info(
                f"[og-recurring] Cart {cart_id} total={cart_total} "
                f"(subtotal={cart_data.get('subtotal')}, "
                f"shipping_total={cart_data.get('shipping_total')})"
            )

        # 2g. Complete cart → creates the order
        complete_result = await self.medusa.complete_cart(cart_id)
        if not complete_result:
            raise RecurringOrderError(
                f"Failed to complete cart {cart_id}",
                step="complete_cart",
            )

        return {
            "order_id": complete_result["order_id"],
            "cart_id": cart_id,
            "cart_total": cart_total,
        }

    async def _init_payment_from_original_order(
        self,
        cart_id: str,
        original_order: dict | None,
        original_transaction_id: str = "",
    ) -> None:
        """
        Copy the payment collection setup from the original order onto the
        new cart so Medusa can complete the cart without customer interaction.

        Steps:
          1. Get the cart's payment_collection (auto-created by Medusa)
          2. Extract the provider_id and session data from the original order
          3. Initialize a payment session with the same provider
          4. Update the session data with auth proof so cart.complete() works
        """
        # ── Get the original order's payment session data ──
        original_provider = "pp_netvalve_netvalve"
        original_session_data: dict[str, Any] = {}

        if original_order:
            orig_pc = original_order.get("payment_collection") or {}
            orig_sessions = orig_pc.get("payment_sessions", [])
            if orig_sessions:
                orig_session = orig_sessions[0]
                original_provider = orig_session.get("provider_id", original_provider)
                original_session_data = orig_session.get("data", {})
                if isinstance(original_session_data, str):
                    original_session_data = {}
                logger.info(
                    f"[og-recurring] Original payment session: provider={original_provider}, "
                    f"data_keys={list(original_session_data.keys()) if isinstance(original_session_data, dict) else 'N/A'}"
                )

        # ── Get or create the cart's payment collection ──
        cart_result = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="GET",
            params={"fields": "+payment_collection.payment_sessions"},
        )
        if not cart_result.success:
            raise RecurringOrderError(
                f"Failed to fetch cart {cart_id} for payment setup",
                step="payment_get_cart",
            )

        cart_data = cart_result.data.get("cart", {})
        pc = cart_data.get("payment_collection") or {}
        payment_collection_id = pc.get("id")

        if not payment_collection_id:
            logger.info(f"[og-recurring] Cart {cart_id} has no payment collection — creating one")
            create_pc_result = await self.medusa.execute_request(
                endpoint="/store/payment-collections",
                method="POST",
                payload={"cart_id": cart_id},
            )
            if not create_pc_result.success:
                raise RecurringOrderError(
                    f"Failed to create payment collection for cart {cart_id}: "
                    f"{create_pc_result.message} — data={create_pc_result.data}",
                    step="payment_create_collection",
                )
            payment_collection_id = (
                create_pc_result.data.get("payment_collection", {}).get("id")
            )

        if not payment_collection_id:
            raise RecurringOrderError(
                f"Cart {cart_id} still has no payment_collection after creation attempt",
                step="payment_no_collection",
            )

        logger.info(
            f"[og-recurring] Cart {cart_id} payment_collection_id={payment_collection_id}"
        )

        # ── Initialize payment session with the same provider ──
        init_result = await self.medusa.execute_request(
            endpoint=f"/store/payment-collections/{payment_collection_id}/payment-sessions",
            method="POST",
            payload={"provider_id": original_provider},
        )
        if not init_result.success:
            raise RecurringOrderError(
                f"Failed to initialize payment session on collection "
                f"{payment_collection_id}: {init_result.message} — data={init_result.data}",
                step="payment_init_session",
            )
        logger.info(
            f"[og-recurring] Payment session initialized — provider={original_provider}"
        )

        # ── Re-fetch to get the new session ID ──
        cart_result2 = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="GET",
            params={"fields": "+payment_collection.payment_sessions"},
        )
        if not cart_result2.success:
            raise RecurringOrderError(
                f"Failed to re-fetch cart {cart_id} after payment init",
                step="payment_refetch",
            )

        sessions = (
            cart_result2.data.get("cart", {})
            .get("payment_collection", {})
            .get("payment_sessions", [])
        )
        if not sessions:
            raise RecurringOrderError(
                f"No payment sessions found after initialization on cart {cart_id}",
                step="payment_no_sessions",
            )

        session_id = sessions[0].get("id")

        # ── Update the session data — copy from original + mark as rebill-authorized ──
        rebill_data: dict[str, Any] = {}

        if isinstance(original_session_data, dict):
            for key in (
                "netvalve_order_id",
                "siteId",
                "netvalveMidId",
                "midId",
                "currency_code",
            ):
                if key in original_session_data:
                    rebill_data[key] = original_session_data[key]

        # The Netvalve provider's authorizePayment has an idempotency guard:
        #   if netvalve_sale_success === true && netvalve_transaction_id → AUTHORIZED
        # This skips POST /sale entirely, which is what we want for rebill orders.
        # Do NOT set hpf_completed or card_form_submitted — those trigger POST /sale.
        rebill_data.update({
            "netvalve_transaction_id": original_transaction_id,
            "netvalve_sale_attempted": True,
            "netvalve_sale_success": True,
            "authorized_at": datetime.now(timezone.utc).isoformat(),
            "rebill_order": True,
        })

        update_result = await self.medusa.execute_request(
            endpoint=f"/store/payment-collections/{payment_collection_id}/payment-sessions/{session_id}",
            method="POST",
            payload={"data": rebill_data},
        )
        if not update_result.success:
            logger.warning(
                f"[og-recurring] Payment session update returned non-success "
                f"(may be non-fatal): {update_result.message}"
            )

        logger.info(
            f"[og-recurring] Payment session {session_id} configured with "
            f"rebill auth proof — keys={list(rebill_data.keys())}"
        )

    # ──────────────────────────────────────────────────────────────
    # Step 3: Netvalve /rebill
    # ──────────────────────────────────────────────────────────────

    async def _netvalve_rebill(
        self,
        transaction_id: str,
        amount: float,
        client_order_id: str,
    ) -> dict[str, Any]:
        url = f"{self.netvalve_base_url}/rebill"
        payload = {
            "transactionID": int(transaction_id) if transaction_id.isdigit() else transaction_id,
            "amount": round(amount, 2),
            "clientOrderId": client_order_id,
        }
        headers = {
            "Content-Type": "application/json",
            "netvalve-client-id": settings.NETVALVE_CLIENT_ID,
            "netvalve-api-key": settings.NETVALVE_API_KEY,
        }

        logger.info(
            f"[og-recurring] POST {url} — "
            f"transactionID={transaction_id}, amount={amount}, "
            f"clientOrderId={client_order_id}"
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=30.0,
                )

            response_data = {}
            try:
                response_data = response.json()
            except Exception:
                response_data = {"raw": response.text}

            logger.info(
                f"[og-recurring] Rebill response — "
                f"HTTP {response.status_code}: {response_data}"
            )

            if response.status_code not in (200, 201):
                raise RecurringOrderError(
                    f"Netvalve rebill failed — HTTP {response.status_code}: {response_data}",
                    step="netvalve_rebill",
                )

            response_code_type = response_data.get("responseCodeType", "")
            response_code = response_data.get("responseCode", "")
            if response_code_type.upper() != "APPROVED":
                decline_reason = response_data.get("declineReason", "")
                response_msg = response_data.get("responseMessage", "")
                raise RecurringOrderError(
                    f"Netvalve rebill declined — code={response_code}, "
                    f"type={response_code_type}, "
                    f"reason={decline_reason}, message={response_msg}",
                    step="netvalve_rebill",
                )

            return response_data

        except RecurringOrderError:
            raise
        except Exception as exc:
            raise RecurringOrderError(
                f"Netvalve rebill network error: {exc}",
                step="netvalve_rebill",
            )

    # ──────────────────────────────────────────────────────────────
    # Step 4: Capture payment on new order
    # ──────────────────────────────────────────────────────────────

    async def _capture_new_order_payment(self, cart_id: str) -> dict | None:
        payment_session_id = await self.medusa.get_payment_session_id_from_cart(cart_id)
        if not payment_session_id:
            raise RecurringOrderError(
                f"No payment session on cart {cart_id}",
                step="capture_get_session",
            )

        payment_id = await self.medusa.get_payment_id_by_session(payment_session_id)
        if not payment_id:
            raise RecurringOrderError(
                f"No payment found for session {payment_session_id}",
                step="capture_get_payment",
            )

        capture_result = await self.medusa.capture_payment(payment_id)
        if not capture_result:
            raise RecurringOrderError(
                f"Failed to capture payment {payment_id}",
                step="capture_payment",
            )

        return capture_result


class RecurringOrderError(Exception):
    def __init__(self, message: str, step: str = "unknown"):
        super().__init__(message)
        self.message = message
        self.step = step


og_recurring_service = OrderGrooveRecurringService()
