"""
OrderGroove Recurring Order Processing Service.

Handles the full flow when OrderGroove sends a recurring order placement.

- Solidgate: Create a cart only (items, addresses, shipping, Solidgate payment session),
  mark cart metadata.recurring_order=true. Call Solidgate POST /recurring with
  order_id=cart_id. When Solidgate sends settle_ok, the /solidgate webhook runs
  process_settle_ok(cart_id) and completes the cart → order, capture, metadata.
  Recurring orders do not trigger OrderGroove Purchase POST (no second subscription).

- Netvalve: Create a full Medusa order (cart marked recurring_order, then complete),
  call Netvalve POST /rebill with the original transactionID, then capture the new
  order payment. Recurring orders do not trigger OrderGroove Purchase POST (no second subscription).
"""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.services.medusa_service import MedusaService
from app.services.solidgate_service import solidgate_service

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

    def _is_solidgate_flow(self, og_order_data: dict[str, Any]) -> bool:
        """
        Determine PSP from OrderGroove order-placement payload using paymentLabel.
        Expects head.paymentLabel or head.orderPaymentLabel to be "solidgate" or "netvalve".
        """
        head = og_order_data.get("head", {})
        payment_label = (
            head.get("paymentLabel") or head.get("orderPaymentLabel") or ""
        )
        value = (payment_label or "").strip().lower()
        is_solidgate = value == "solidgate"
        logger.info(
            "[og-recurring] paymentLabel=%s → flow=%s",
            payment_label or "(empty)",
            "solidgate" if is_solidgate else "netvalve",
        )
        return is_solidgate

    async def process_recurring_order(
        self,
        og_order_data: dict[str, Any],
        solidgate_recurring_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Full flow: branch on PSP from XML (see _is_solidgate_flow).
        - Solidgate: create full order, then Solidgate POST /recurring.
        - Netvalve: create full order, then Netvalve POST /rebill, then capture.
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

        logger.info(
            f"[og-recurring] Processing OG order {og_order_id} — "
            f"original={original_order_id}, customer={customer_id}, "
            f"total={total_value} {currency}, items={len(item_list)}"
        )

        if self._is_solidgate_flow(og_order_data):
            # ── Solidgate flow: create cart only (no complete). Pass cart_id as order_id to
            #    /recurring so Solidgate settle_ok webhook receives that cart_id and
            #    process_settle_ok completes the cart → order, capture, Purchase POST.
            if not solidgate_recurring_token:
                raise RecurringOrderError(
                    "Solidgate recurring token not found (orderTokenId from OrderGroove XML is required)",
                    step="solidgate_token",
                )
            region_id = None
            original_order_details = await self._get_order_details(original_order_id)
            if original_order_details:
                region_id = original_order_details.get("region_id")
            if not region_id:
                region_id = await self._get_default_region_id()

            cart_result = await self._create_medusa_order_solidgate(
                customer_id=customer_id,
                customer_email=customer_email,
                region_id=region_id,
                items=item_list,
                original_order=original_order_details,
                og_customer=customer,
                currency=currency,
            )
            new_cart_id = cart_result["cart_id"]
            cart_total = cart_result.get("cart_total", 0.0)
            amount = cart_total if cart_total > 0 else float(total_value)
            amount_minor = int(round(amount * 100))

            await self._init_payment_solidgate_recurring(new_cart_id)
            await self._set_cart_recurring_metadata(new_cart_id)

            logger.info(
                f"[og-recurring] Solidgate /recurring — amount={amount} ({amount_minor} minor), "
                f"currency={currency}, order_id={new_cart_id} (cart_id; order created on settle_ok)"
            )
            recurring_result = await self._solidgate_recurring(
                order_id=new_cart_id,
                amount=amount_minor,
                currency=currency,
                recurring_token=solidgate_recurring_token,
                order_description=f"Recurring order {og_order_id}",
                customer_email=customer_email or "",
            )
            logger.info(
                "[og-recurring] Solidgate /recurring API return: %s",
                recurring_result,
            )
            if not recurring_result.get("success"):
                data = recurring_result.get("data") or recurring_result.get("error") or {}
                msg = data.get("message") or data.get("error") or recurring_result.get("message", "Solidgate recurring failed")
                raise RecurringOrderError(
                    str(msg),
                    step="solidgate_recurring",
                )
            logger.info(
                f"[og-recurring] Solidgate /recurring success — cart_id={new_cart_id} (order will be created on settle_ok)"
            )
            return {
                "og_order_id": og_order_id,
                "original_order_id": original_order_id,
                "new_cart_id": new_cart_id,
                "new_order_id": None,
                "solidgate_transaction_id": (recurring_result.get("data") or {}).get("transaction", {}).get("id")
                or (recurring_result.get("data") or {}).get("transaction_id"),
                "amount": amount,
                "currency": currency,
            }
        else:
            # ── Netvalve flow: create order, rebill, capture ──
            transaction_id = await self._get_original_transaction_id(
                original_order_id, customer_id=customer_id
            )
            logger.info(
                f"[og-recurring] Original order {original_order_id} → "
                f"transactionID={transaction_id}"
            )
            region_id = None
            original_order_details = await self._get_order_details(original_order_id)
            if original_order_details:
                region_id = original_order_details.get("region_id")
            if not region_id:
                region_id = await self._get_default_region_id()

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

    @staticmethod
    def _extract_transaction_id_from_order(order: dict) -> str | None:
        """Pull the Netvalve transactionId out of an order's payment_capture metadata."""
        metadata = order.get("metadata") or {}
        pc = metadata.get("payment_capture") or {}
        tid = pc.get("transactionId") or pc.get("netvalve_transaction_id")
        if not tid:
            for p in pc.get("payments", []):
                tid = p.get("transactionId") or p.get("netvalve_transaction_id")
                if tid:
                    break
        return str(tid) if tid else None

    async def _get_original_transaction_id(
        self, order_id: str, customer_id: str = ""
    ) -> str:
        """
        Return the Netvalve transactionId from the original order.

        Primary path  : fetch the order directly by ID (works when
                        originalOrderId from OG == Medusa order ID).
        Fallback path : when the direct fetch 404s (e.g. originalOrderId is an
                        OG-internal numeric ID like "25"), query the customer's
                        recent orders and pick the most recent one that has a
                        Netvalve transactionId in its payment_capture metadata.
        """
        result = await self.medusa.execute_request(
            endpoint=f"/admin/orders/{order_id}",
            method="GET",
            params={"fields": "id,metadata"},
        )

        if result.success:
            order = result.data.get("order", {})
            transaction_id = self._extract_transaction_id_from_order(order)
            if transaction_id:
                return transaction_id
            metadata = order.get("metadata") or {}
            raise RecurringOrderError(
                f"No Netvalve transactionID in order {order_id} metadata. "
                f"metadata.payment_capture={metadata.get('payment_capture', {})}",
                step="extract_transaction_id",
            )

        # ── Fallback: originalOrderId is not a Medusa ID ──────────────────────
        # OG may store the merchant's own order reference (e.g. "25") instead of
        # the Medusa order ID.  If we have the Medusa customer_id, look up their
        # recent orders and locate a Netvalve transactionId.
        if customer_id:
            logger.warning(
                "[og-recurring] Order '%s' not found in Medusa (status=%s) — "
                "falling back to customer order lookup for customer_id=%s",
                order_id,
                result.status_code,
                customer_id,
            )
            fallback = await self.medusa.execute_request(
                endpoint="/admin/orders",
                method="GET",
                params={
                    "customer_id": customer_id,
                    "fields": "id,metadata",
                    "order": "-created_at",
                    "limit": "20",
                },
            )
            if fallback.success:
                for o in fallback.data.get("orders", []):
                    tid = self._extract_transaction_id_from_order(o)
                    if tid:
                        logger.info(
                            "[og-recurring] Found transactionId=%s via customer "
                            "fallback — customer=%s, medusa_order=%s "
                            "(og_original_order_id=%s)",
                            tid,
                            customer_id,
                            o.get("id"),
                            order_id,
                        )
                        return tid
            raise RecurringOrderError(
                f"Order '{order_id}' not found in Medusa and no Netvalve "
                f"transactionId found via customer fallback "
                f"(customer_id={customer_id})",
                step="get_original_order",
            )

        raise RecurringOrderError(
            f"Failed to fetch original order {order_id}: {result.message}",
            step="get_original_order",
        )

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

        # Medusa v2 Admin API uses payment_collections (plural); some versions use
        # payment_collection (singular). Try plural first to avoid 500 on invalid fields.
        for pc_fields in (
            "payment_collections",
            "+payment_collections",
            "+payment_collection",
            "payment_collection",
        ):
            pc_result = await self.medusa.execute_request(
                endpoint=f"/admin/orders/{order_id}",
                method="GET",
                params={"fields": pc_fields},
            )
            if not pc_result.success:
                logger.debug(
                    f"[og-recurring] payment_collection query failed with "
                    f"fields={pc_fields}: {pc_result.message}"
                )
                continue
            pc_order = pc_result.data.get("order", {})
            # Support both singular (legacy) and plural (Medusa v2)
            pc = pc_order.get("payment_collection")
            if not pc and pc_order.get("payment_collections"):
                collections = pc_order["payment_collections"]
                pc = collections[0] if isinstance(collections, list) and collections else None
            if pc:
                order["payment_collection"] = pc
                sessions = pc.get("payment_sessions") or pc.get("payments") or []
                logger.info(
                    f"[og-recurring] Original order payment_collection loaded "
                    f"(fields={pc_fields}) — sessions={len(sessions)}"
                )
                break
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

    async def _set_cart_recurring_metadata(self, cart_id: str) -> None:
        """Set cart metadata.recurring_order = True so order.placed and settle_ok skip Purchase POST."""
        result = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="GET",
        )
        if not result.success:
            logger.warning(
                "[og-recurring] Could not fetch cart to set recurring metadata: %s",
                result.message,
            )
            return
        cart = result.data.get("cart", {})
        metadata = dict(cart.get("metadata") or {})
        metadata["recurring_order"] = True
        update = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="POST",
            payload={"metadata": metadata},
        )
        if update.success:
            logger.info("[og-recurring] Cart %s marked as recurring_order (skip Purchase POST)", cart_id)
        else:
            logger.warning(
                "[og-recurring] Failed to set recurring metadata on cart %s: %s",
                cart_id,
                update.message,
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
        payment_provider_override: str | None = None,
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

        # 2e. Init payment so cart can complete: Solidgate recurring or Netvalve rebill
        if payment_provider_override == "pp_solidgate_solidgate":
            await self._init_payment_solidgate_recurring(cart_id)
        else:
            await self._init_payment_netvalve_rebill(
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

        # Mark cart as recurring so order.placed subscriber does not trigger Purchase POST
        await self._set_cart_recurring_metadata(cart_id)

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

    async def _create_medusa_order_solidgate(
        self,
        customer_id: str,
        customer_email: str,
        region_id: str,
        items: list[dict],
        original_order: dict | None,
        og_customer: dict[str, Any],
        currency: str,
    ) -> dict[str, Any]:
        """Create cart with items, addresses, shipping; return cart_id and cart_total. No payment or complete."""
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
        logger.info(f"[og-recurring] Cart created (Solidgate): {cart_id}")

        for item in items:
            variant_id = item.get("product_id", "")
            qty = int(item.get("qty", "1"))
            if not variant_id:
                continue
            add_result = await self.medusa.execute_request(
                endpoint=f"/store/carts/{cart_id}/line-items",
                method="POST",
                payload={"variant_id": variant_id, "quantity": qty},
            )
            if not add_result.success:
                raise RecurringOrderError(
                    f"Failed to add item {variant_id}: {add_result.message}",
                    step="add_line_item",
                )

        address_payload = self._build_address_payload(original_order, og_customer)
        addr_result = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="POST",
            payload=address_payload,
        )
        if not addr_result.success:
            raise RecurringOrderError(
                f"Failed to set addresses: {addr_result.message}",
                step="set_addresses",
            )

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

        cart_result = await self.medusa.execute_request(
            endpoint=f"/store/carts/{cart_id}",
            method="GET",
        )
        cart_total = 0.0
        if cart_result.success:
            raw_total = cart_result.data.get("cart", {}).get("total")
            if raw_total is not None:
                cart_total = float(raw_total)
        return {"cart_id": cart_id, "cart_total": cart_total}

    async def _solidgate_recurring(
        self,
        order_id: str,
        amount: int,
        currency: str,
        recurring_token: str,
        order_description: str = "Recurring order",
        customer_email: str = "",
    ) -> dict[str, Any]:
        """Call Solidgate POST /recurring (1-click). amount in minor units. Returns result dict with success key."""
        result = await solidgate_service.recurring(
            order_id=order_id,
            amount=amount,
            currency=currency,
            recurring_token=recurring_token,
            order_description=order_description,
            customer_email=customer_email,
        )
        # Log raw API response for testing
        logger.info("[og-recurring] Solidgate /recurring raw response: %s", result)
        if result.get("success") and result.get("status_code") in (200, 201, 204):
            return {"success": True, "data": result.get("data")}
        data = result.get("data") or {}
        err = result.get("error")
        msg = data.get("message") or data.get("error")
        if not msg and isinstance(err, dict):
            msg = err.get("message", "Recurring failed")
        if not msg:
            msg = err if err else "Recurring failed"
        return {"success": False, "data": data, "message": str(msg)}

    async def _get_or_create_payment_collection_id(self, cart_id: str) -> str:
        """Get or create payment collection for cart. Returns payment_collection_id."""
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
        logger.info(f"[og-recurring] Cart {cart_id} payment_collection_id={payment_collection_id}")
        return payment_collection_id

    async def _init_payment_solidgate_recurring(self, cart_id: str) -> None:
        """
        Init payment on cart for Solidgate recurring. Uses pp_solidgate_solidgate and
        session data so Medusa complete_cart succeeds without charging (charge is done via /recurring).
        """
        payment_collection_id = await self._get_or_create_payment_collection_id(cart_id)
        provider_id = "pp_solidgate_solidgate"

        init_result = await self.medusa.execute_request(
            endpoint=f"/store/payment-collections/{payment_collection_id}/payment-sessions",
            method="POST",
            payload={"provider_id": provider_id},
        )
        if not init_result.success:
            raise RecurringOrderError(
                f"Failed to initialize Solidgate payment session: {init_result.message} — data={init_result.data}",
                step="payment_init_session",
            )
        logger.info(f"[og-recurring] Payment session initialized — provider={provider_id} (Solidgate recurring)")

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

        session_data: dict[str, Any] = {
            "solidgate_recurring_authorized": True,
            "authorized_at": datetime.now(timezone.utc).isoformat(),
        }
        update_result = await self.medusa.execute_request(
            endpoint=f"/store/payment-collections/{payment_collection_id}/payment-sessions/{session_id}",
            method="POST",
            payload={"data": session_data},
        )
        if not update_result.success:
            logger.warning(
                f"[og-recurring] Solidgate payment session update returned non-success: {update_result.message}"
            )
        logger.info(
            f"[og-recurring] Payment session {session_id} configured for Solidgate recurring — keys={list(session_data.keys())}"
        )

    async def _init_payment_netvalve_rebill(
        self,
        cart_id: str,
        original_order: dict | None,
        original_transaction_id: str = "",
    ) -> None:
        """
        Init payment on cart for Netvalve rebill. Copies provider and session data from
        original order, then sets rebill auth proof so Medusa complete_cart succeeds
        without calling POST /sale (actual charge is done via /rebill).
        """
        original_provider = "pp_netvalve_netvalve"
        original_session_data: dict[str, Any] = {}

        if original_order:
            orig_pc = original_order.get("payment_collection") or {}
            orig_sessions = orig_pc.get("payment_sessions", [])
            if orig_sessions:
                orig_session = orig_sessions[0]
                original_provider = orig_session.get("provider_id", original_provider)
                original_session_data = orig_session.get("data", {}) or {}
                if isinstance(original_session_data, str):
                    original_session_data = {}
                logger.info(
                    f"[og-recurring] Netvalve rebill: original provider={original_provider}, "
                    f"data_keys={list(original_session_data.keys())}"
                )

        payment_collection_id = await self._get_or_create_payment_collection_id(cart_id)

        init_result = await self.medusa.execute_request(
            endpoint=f"/store/payment-collections/{payment_collection_id}/payment-sessions",
            method="POST",
            payload={"provider_id": original_provider},
        )
        if not init_result.success:
            raise RecurringOrderError(
                f"Failed to initialize Netvalve payment session: {init_result.message} — data={init_result.data}",
                step="payment_init_session",
            )
        logger.info(f"[og-recurring] Payment session initialized — provider={original_provider} (Netvalve rebill)")

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

        session_data: dict[str, Any] = {}
        if isinstance(original_session_data, dict):
            for key in (
                "netvalve_order_id",
                "siteId",
                "netvalveMidId",
                "midId",
                "currency_code",
            ):
                if key in original_session_data:
                    session_data[key] = original_session_data[key]
        session_data.update({
            "netvalve_transaction_id": original_transaction_id,
            "netvalve_sale_attempted": True,
            "netvalve_sale_success": True,
            "authorized_at": datetime.now(timezone.utc).isoformat(),
            "rebill_order": True,
        })

        update_result = await self.medusa.execute_request(
            endpoint=f"/store/payment-collections/{payment_collection_id}/payment-sessions/{session_id}",
            method="POST",
            payload={"data": session_data},
        )
        if not update_result.success:
            logger.warning(
                f"[og-recurring] Netvalve payment session update returned non-success: {update_result.message}"
            )
        logger.info(
            f"[og-recurring] Payment session {session_id} configured with Netvalve rebill auth proof — keys={list(session_data.keys())}"
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
