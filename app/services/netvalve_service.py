"""
NetValve Payment Gateway Service.

Handles all external API calls to the NetValve payment platform,
including HPF session initialization, sale processing, capture,
refund, cancel, and webhook handling.

implementation and converted to Python/async with httpx.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

APPROVED_RESPONSE_CODE = "GTW_1000"
APPROVED_BANK_CODE = "BNK_2000"

DECLINE_BANK_CODES = {
    "05", "51", "14", "54", "41", "43", "61", "62", "65",
}

BANK_DECLINE_REASONS: Dict[str, str] = {
    "05": "Card declined by issuing bank",
    "51": "Insufficient funds",
    "14": "Invalid card number",
    "54": "Card expired",
    "41": "Card reported lost",
    "43": "Card reported stolen",
    "61": "Exceeds withdrawal limit",
    "62": "Restricted card",
    "65": "Exceeds withdrawal frequency",
}

LOOPBACK_IP_RE = re.compile(
    r"^(::1|::ffff:127\.0\.0\.1|127\.0\.0\.1|0\.0\.0\.0)$"
)

PUBLIC_IP_ENDPOINTS = [
    "https://api.ipify.org?format=text",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
]

DECLINE_MESSAGE_RE = re.compile(
    r"declin|insufficient|invalid|not supported|failed|do not honor|expired|lost|stolen|restricted",
    re.IGNORECASE,
)

AUTH_FLAG_KEYS = [
    "authorized", "is_authorized", "hpf_completed", "card_form_submitted",
]

AUTH_STRING_KEYS = [
    "netvalve_token", "transaction_id", "transactionId",
    "netvalve_transaction_id", "order_id", "orderId",
    "checkout_id", "checkoutId",
]

# URL constants
SANDBOX_BACKOFFICE_URL = "https://backoffice-api.uat.sandbox-netvalve.com"
PRODUCTION_BACKOFFICE_URL = "https://backoffice-api.netvalve.com"
SANDBOX_PAYMENT_API_URL = "https://payment-api.uat.sandbox-netvalve.com"
PRODUCTION_PAYMENT_API_URL = "https://api.netvalve.com"
SANDBOX_HPP_BASE_URL = "https://hpp-api.uat.sandbox-netvalve.com"
PRODUCTION_HPP_BASE_URL = "https://hpp-api.netvalve.com"
SANDBOX_DEFAULT_HPF_SCRIPT_SRC = (
    "https://tokenfield.uat.sandbox-netvalve.com/sdk/index.DUbZDKWj.js"
)

# Customer field mapping: session key → NetValve sale payload key
CUSTOMER_FIELD_MAPPING: List[Tuple[str, str]] = [
    ("customer_email", "customerEmail"),
    ("customer_first_name", "customerFirstName"),
    ("customer_last_name", "customerLastName"),
    ("card_holder_name", "cardHolderName"),
    ("customer_phone", "customerPhone"),
    ("customer_address", "customerAddress"),
    ("customer_city", "customerCity"),
    ("customer_state", "customerState"),
    ("customer_zip_code", "customerZipCode"),
    ("customer_country_code", "customerCountryCode"),
]


# ══════════════════════════════════════════════════════════════════════
# Pure helper functions
# ══════════════════════════════════════════════════════════════════════


def _env(key: str) -> str:
    """Read a NetValve env var from settings, stripped."""
    return (getattr(settings, key, "") or "").strip()


def _is_sandbox() -> bool:
    return _env("NETVALVE_ENVIRONMENT") == "sandbox"


def _resolve_backoffice_url() -> str:
    return (
        _env("NETVALVE_BACKOFFICE_API_URL")
        or (SANDBOX_BACKOFFICE_URL if _is_sandbox() else PRODUCTION_BACKOFFICE_URL)
    )


def _resolve_payment_api_url() -> str:
    return (
        _env("NETVALVE_PAYMENT_API_URL")
        or _env("NETVALVE_BASE_URL")
        or (SANDBOX_PAYMENT_API_URL if _is_sandbox() else PRODUCTION_PAYMENT_API_URL)
    )


def _resolve_hpp_base_url() -> str:
    return (
        _env("NETVALVE_HPP_BASE_URL")
        or (
            _env("NETVALVE_SANDBOX_HPP_BASE_URL") or SANDBOX_HPP_BASE_URL
            if _is_sandbox()
            else _env("NETVALVE_PRODUCTION_HPP_BASE_URL") or PRODUCTION_HPP_BASE_URL
        )
    )


def _resolve_fallback_hpf_script_src() -> str:
    explicit = _env("NETVALVE_HPF_SCRIPT_FALLBACK_SRC")
    if explicit:
        return explicit
    if _is_sandbox():
        return SANDBOX_DEFAULT_HPF_SCRIPT_SRC
    return ""


def _resolve_netvalve_mid_id(currency_code: Optional[str] = None) -> str:
    currency = (currency_code or "").upper()
    if currency == "EUR":
        return _env("NETVALVE_MID_ID_EUR")
    elif currency == "USD":
        return _env("NETVALVE_MID_ID_USD")
    elif currency == "PHP":
        return _env("NETVALVE_MID_ID_PHP")
    else:
        return (
            _env("NETVALVE_MID_ID_USD")
            or _env("NETVALVE_MID_ID_EUR")
            or _env("NETVALVE_MID_ID_PHP")
        )


def _pick_string(source: Dict[str, Any], *keys: str) -> str:
    """Return first non-empty string value from source, or ''."""
    for k in keys:
        v = source.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _sanitize_order_description(raw: str, fallback: str) -> str:
    """
    Sanitize order description for NetValve /sale.
    Allows alphanumeric, spaces, and basic punctuation only.
    """
    cleaned = re.sub(r"[^\w\s,.\-]", "", raw)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()[:100]
    return cleaned or fallback


def _build_customer_fields(data: Dict[str, Any]) -> Dict[str, str]:
    """
    Map customer fields from session data into POST /sale payload.
    """
    result: Dict[str, str] = {}
    for src, dest in CUSTOMER_FIELD_MAPPING:
        val = data.get(src)
        if isinstance(val, str) and val:
            result[dest] = val
    return result


def _format_decline_detail(result: Dict[str, Any]) -> str:
    """Build a human-readable decline suffix."""
    decline_reason = result.get("decline_reason") or result.get("declineReason")
    if decline_reason:
        return f" ({decline_reason})"
    bank_code = result.get("bank_response_code") or result.get("bankResponseCode")
    if bank_code:
        return f" (bank code {bank_code})"
    return ""


def _has_payment_confirmation(data: Dict[str, Any]) -> bool:
    """
    Check if session data contains authorization proof.
    """
    if not data:
        return False
    if any(data.get(k) is True for k in AUTH_FLAG_KEYS):
        return True
    return any(
        isinstance(data.get(k), str) and len(data.get(k, "")) > 0
        for k in AUTH_STRING_KEYS
    )


def _pick_first_store_url() -> str:
    """Resolve the primary store URL for HPP redirect defaults."""
    configured = _env("NETVALVE_RETURN_BASE_URL")
    if configured:
        return configured
    cors = getattr(settings, "CORS_ORIGINS", "")
    parts = [s.strip() for s in cors.split(",") if s.strip()]
    return parts[0] if parts else "http://localhost:8000"


# ══════════════════════════════════════════════════════════════════════
# NetValveService class
# ══════════════════════════════════════════════════════════════════════


class NetValveService:
    """
    Service class that encapsulates all NetValve payment gateway operations.

    """

    def __init__(self) -> None:
        self._cached_public_ip: Optional[str] = None
        self._cached_public_ip_at: float = 0
        self._public_ip_ttl: float = 600  # 10 minutes

        # Backoffice bearer token cache (module-scoped in original)
        self._cached_token: Optional[Dict[str, Any]] = None  # {accessToken, expiresAt}

    # ──────────────────────────────────────────────────────────────
    # Credential / URL resolution
    # ──────────────────────────────────────────────────────────────

    @property
    def api_key(self) -> str:
        return _env("NETVALVE_API_KEY")

    @property
    def client_id(self) -> str:
        return _env("NETVALVE_CLIENT_ID")

    @property
    def site_id(self) -> str:
        return _env("NETVALVE_SITE_ID")

    @property
    def base_url(self) -> str:
        return _resolve_payment_api_url()

    # ──────────────────────────────────────────────────────────────
    # Public IP resolution
    # ──────────────────────────────────────────────────────────────

    async def _resolve_public_ip(self) -> str:
        """
        Resolve the server's public IP via external APIs.
        Cached for 10 minutes.
        """
        if (
            self._cached_public_ip
            and time.time() - self._cached_public_ip_at < self._public_ip_ttl
        ):
            return self._cached_public_ip

        async with httpx.AsyncClient(timeout=3.0) as client:
            for url in PUBLIC_IP_ENDPOINTS:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        ip = resp.text.strip()
                        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                            self._cached_public_ip = ip
                            self._cached_public_ip_at = time.time()
                            logger.info(f"[netvalve] resolved public IP: {ip}")
                            return ip
                except Exception:
                    continue

        logger.warning(
            "[netvalve] could not resolve public IP — all lookup endpoints failed"
        )
        return ""

    # ──────────────────────────────────────────────────────────────
    # Backoffice authentication
    # ──────────────────────────────────────────────────────────────

    async def _get_backoffice_token(self) -> Optional[str]:
        """
        Sign in to NetValve backoffice and return a bearer token.
        Cached with a 5-minute pre-expiry buffer.
        """
        if (
            self._cached_token
            and time.time() * 1000 < self._cached_token["expiresAt"] - 300_000
        ):
            return self._cached_token["accessToken"]

        username = _env("NETVALVE_BASIC_AUTH_USERNAME")
        password = _env("NETVALVE_BASIC_AUTH_PASSWORD")
        if not username or not password:
            return None

        url = f"{_resolve_backoffice_url()}/backoffice/users/sign-in"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "userName": username,
                        "password": password,
                        "checkForBot": "net",
                    },
                    headers={"Content-Type": "application/json"},
                )

            if resp.status_code != 200:
                logger.error(
                    f"[netvalve] backoffice sign-in failed: HTTP {resp.status_code} {resp.text}"
                )
                return None

            data = resp.json()
            access_token = data.get("accessToken")
            if not access_token:
                logger.error(
                    "[netvalve] backoffice sign-in: no accessToken in response"
                )
                return None

            self._cached_token = {
                "accessToken": access_token,
                "expiresAt": time.time() * 1000
                + (data.get("expiresIn", 3600)) * 1000,
            }
            return access_token

        except Exception as e:
            logger.error(f"[netvalve] backoffice sign-in error: {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # HPF Session Initialization
    # ──────────────────────────────────────────────────────────────

    async def initialize_hpf_session(self) -> Optional[Dict[str, Any]]:
        """
        Call NetValve GET /hpf/initializeSession to get HPF script
        and payment token.
        """
        client_id = self.client_id
        api_key = self.api_key

        if not client_id or not api_key:
            logger.error(
                "[netvalve] HPF initializeSession: missing NETVALVE_CLIENT_ID or NETVALVE_API_KEY"
            )
            return None

        base_url = _resolve_payment_api_url()
        url = f"{base_url}/hpf/initializeSession"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    headers={
                        "netvalve-client-id": client_id,
                        "netvalve-api-key": api_key,
                    },
                )

            if resp.status_code != 200:
                logger.error(
                    f"[netvalve] HPF initializeSession failed: HTTP {resp.status_code} – {resp.text}"
                )
                return None

            data = resp.json()
            if not data.get("netvalveScriptSrc") and not data.get("paymentToken"):
                logger.error(
                    "[netvalve] HPF initializeSession: no script src or paymentToken in response"
                )
                return None

            return data

        except Exception as e:
            logger.error(f"[netvalve] HPF initializeSession error: {e}")
            return None

    async def _fetch_hpf_script(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Fetch HPF script from backoffice API.
        """
        url = f"{_resolve_backoffice_url()}/backoffice/hpf/script"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )

            if resp.status_code != 200:
                logger.error(
                    f"[netvalve] HPF scripts fetch failed: HTTP {resp.status_code}"
                )
                return None

            scripts = resp.json()
            if not isinstance(scripts, list) or len(scripts) == 0:
                return None

            # Filter active, non-deleted scripts with https URLs
            active = [
                s
                for s in scripts
                if s.get("status") == "ACTIVE"
                and not s.get("deleted")
                and isinstance(s.get("netvalveScriptSrc"), str)
                and s["netvalveScriptSrc"].startswith("https://")
            ]

            # Prefer the default script
            default_script = next((s for s in active if s.get("isDefault")), None)
            if default_script:
                return default_script

            # Sort by creation date descending
            active.sort(
                key=lambda s: s.get("createdDate", ""),
                reverse=True,
            )
            return active[0] if active else None

        except Exception as e:
            logger.error(f"[netvalve] HPF scripts fetch error: {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # HPP Fallback
    # ──────────────────────────────────────────────────────────────

    def _build_hpp_order_endpoint_candidates(
        self,
    ) -> List[Dict[str, str]]:
        """
        Build list of candidate URLs for HPP order creation.
        """
        candidates: List[Dict[str, str]] = []

        configured_host = _env("NETVALVE_HPP_ORDER_HOST")
        hosts = [h for h in [configured_host, _resolve_hpp_base_url()] if h]

        configured_path = _env("NETVALVE_HPP_ORDER_PATH")
        paths_raw = [configured_path, "/hpp/order", "/order"]
        paths = [
            p if p.startswith("/") else f"/{p}"
            for p in paths_raw
            if p
        ]

        seen: set = set()
        for host in hosts:
            for path in paths:
                # Build full URL
                full_url = host.rstrip("/") + path
                if full_url not in seen:
                    seen.add(full_url)
                    candidates.append({"method": "POST", "url": full_url})

        return candidates

    @staticmethod
    def _normalize_hpp_redirect(data: Dict[str, Any]) -> Optional[str]:
        """
        Extract redirect URL from HPP order response.
        """
        root = data
        nested = [
            root.get(k)
            for k in ["data", "payload", "order"]
            if isinstance(root.get(k), dict)
        ]

        redirect_keys = [
            "redirectUrl", "redirect_url", "url", "paymentUrl", "payment_url",
        ]

        for source in [root, *nested]:
            for key in redirect_keys:
                val = source.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()

        return None

    async def _try_hpp_fallback(
        self,
        bearer_token: Optional[str],
        body: Dict[str, Any],
        checkout: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Attempt HPP fallback when HPF is unavailable.
        """
        if _env("NETVALVE_HPP_FALLBACK_ENABLED").lower() == "false":
            return {
                "success": False,
                "attempts": [],
                "reason": "hpp_fallback_disabled",
            }

        # Direct URL override
        direct_url = _env("NETVALVE_HPP_DIRECT_URL")
        if direct_url:
            return {
                "success": True,
                "attempts": [],
                "data": {"redirectUrl": direct_url},
                "endpoint": {"method": "CONFIGURED", "url": direct_url},
            }

        if not bearer_token:
            return {
                "success": False,
                "attempts": [],
                "reason": "hpp_fallback_no_bearer_token",
            }

        amount = checkout.get("amount")
        if not amount or amount <= 0:
            return {
                "success": False,
                "attempts": [],
                "reason": "hpp_fallback_missing_amount",
            }

        currency = (checkout.get("currency_code") or "USD").upper()
        mid_id = _resolve_netvalve_mid_id(currency)
        site_id = _env("NETVALVE_SITE_ID")

        if not mid_id or not site_id:
            return {
                "success": False,
                "attempts": [],
                "reason": "hpp_fallback_missing_site_or_mid",
            }

        return_base = _pick_first_store_url()
        payload = {
            "mode": _env("NETVALVE_HPP_MODE") or "SALE",
            "amount": amount,
            "currency": currency,
            "siteId": site_id,
            "netvalveMidId": mid_id,
            "clientOrderId": checkout.get("cart_id") or f"cart_{int(time.time())}",
            "orderDesc": body.get("order_desc") or "Medusa checkout",
            "successUrl": (
                body.get("success_url")
                or _env("NETVALVE_HPP_SUCCESS_URL")
                or f"{return_base}/checkout-v2/payment?netvalve_status=success"
            ),
            "cancelUrl": (
                body.get("cancel_url")
                or _env("NETVALVE_HPP_CANCEL_URL")
                or f"{return_base}/checkout-v2/payment?netvalve_status=cancel"
            ),
            "failedUrl": (
                body.get("failed_url")
                or _env("NETVALVE_HPP_FAILED_URL")
                or f"{return_base}/checkout-v2/payment?netvalve_status=failed"
            ),
            "pendingUrl": (
                body.get("pending_url")
                or _env("NETVALVE_HPP_PENDING_URL")
                or f"{return_base}/checkout-v2/payment?netvalve_status=pending"
            ),
        }

        attempts: List[Dict[str, Any]] = []
        candidates = self._build_hpp_order_endpoint_candidates()

        async with httpx.AsyncClient(timeout=15.0) as http_client:
            for c in candidates:
                try:
                    resp = await http_client.post(
                        c["url"],
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {bearer_token}",
                        },
                    )

                    text = resp.text
                    attempts.append({
                        "method": c["method"],
                        "url": c["url"],
                        "status": resp.status_code,
                        "body": text[:500],
                    })

                    content_type = resp.headers.get("content-type", "")
                    if resp.status_code >= 400 or "application/json" not in content_type:
                        continue

                    try:
                        parsed = resp.json()
                    except Exception:
                        continue

                    redirect_url = self._normalize_hpp_redirect(parsed)
                    if redirect_url:
                        return {
                            "success": True,
                            "endpoint": {"method": c["method"], "url": c["url"]},
                            "attempts": attempts,
                            "data": {**parsed, "redirectUrl": redirect_url},
                        }

                except Exception as err:
                    attempts.append({
                        "method": c["method"],
                        "url": c["url"],
                        "status": 0,
                        "body": str(err),
                    })

        return {"success": False, "attempts": attempts, "reason": "hpp_fallback_no_redirect"}

    # ──────────────────────────────────────────────────────────────
    # Diagnostic builder
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _build_diagnostic(
        token_obtained: bool,
        hpf_script_found: bool,
        hpp_result: Dict[str, Any],
    ) -> str:
        """
        Build a diagnostic message when HPF session init fails.
        """
        lines: List[str] = [
            "NetValve payment session could not be initialized.",
            "",
        ]

        if not token_obtained:
            lines.append(
                "AUTH: Backoffice sign-in failed — check NETVALVE_BASIC_AUTH_USERNAME "
                "and NETVALVE_BASIC_AUTH_PASSWORD in .env."
            )
        elif not hpf_script_found:
            lines.append(
                "HPF: No active HPF script found in the backoffice. "
                "Ensure HPF scripts are configured in the NetValve admin panel."
            )

        hpp_401 = any(
            a.get("status") == 401 for a in hpp_result.get("attempts", [])
        )
        reason = hpp_result.get("reason")

        if reason == "hpp_fallback_no_bearer_token":
            lines.append("HPP: No Bearer token available for HPP API.")
        elif hpp_401:
            lines.append(
                "HPP: Bearer token rejected by HPP API (401). "
                "The HPP API may use a different token than the backoffice."
            )
        elif reason:
            lines.append(f"HPP: {reason}")

        lines.extend([
            "",
            "QUICK FIX — set one of these in .env:",
            "  • NETVALVE_HPF_SCRIPT_SRC=<url>  — HPF script URL",
            "  • NETVALVE_HPP_DIRECT_URL=<url>  — pre-built HPP redirect URL",
        ])

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────
    # Full HPF session flow
    # ──────────────────────────────────────────────────────────────

    async def create_hpf_session(
        self,
        body: Dict[str, Any],
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Execute the 5-step HPF session initialization waterfall.
        Returns (status_code, response_body).

        """
        currency_code = (body.get("currency_code") or "").upper() or None

        common_payload = {
            "provider": "netvalve",
            "environment": _env("NETVALVE_ENVIRONMENT") or "production",
            "currency_code": currency_code,
            "site_id": self.site_id,
            "client_id": self.client_id,
            "netvalve_mid_id": _resolve_netvalve_mid_id(currency_code),
        }

        checkout = {
            "amount": body.get("amount"),
            "currency_code": currency_code,
            "cart_id": body.get("cart_id"),
        }

        try:
            # Step 0: NETVALVE_HPP_DIRECT_URL override
            direct_hpp_url = _env("NETVALVE_HPP_DIRECT_URL")
            if direct_hpp_url:
                return 200, {
                    **common_payload,
                    "flow": "hpp",
                    "hpp": {"redirect_url": direct_hpp_url},
                    "payment_session_patch": {
                        "requires_redirect": True,
                        "redirect_url": direct_hpp_url,
                    },
                }

            # Step 0.5: NETVALVE_HPF_SCRIPT_SRC static override
            static_script_src = _env("NETVALVE_HPF_SCRIPT_SRC")
            if static_script_src:
                integrity = _env("NETVALVE_HPF_SCRIPT_INTEGRITY") or None
                return 200, {
                    **common_payload,
                    "flow": "hpf",
                    "hpf": {
                        "script_src": static_script_src,
                        "integrity": integrity,
                    },
                    "payment_session_patch": {"hpf_initialized": True},
                }

            # Step 1: Payment API → HPF initializeSession (primary)
            hpf_session = await self.initialize_hpf_session()

            if hpf_session and hpf_session.get("netvalveScriptSrc"):
                # Extract jwtToken from script URL if not in separate field
                jwt_token = hpf_session.get("jwtToken")
                if not jwt_token and hpf_session.get("netvalveScriptSrc"):
                    try:
                        parsed_url = urlparse(hpf_session["netvalveScriptSrc"])
                        qs = parse_qs(parsed_url.query)
                        jwt_token = qs.get("jwtToken", [None])[0]
                    except Exception:
                        pass

                return 200, {
                    **common_payload,
                    "flow": "hpf",
                    "hpf": {
                        "script_src": hpf_session["netvalveScriptSrc"],
                        "integrity": hpf_session.get("integrity"),
                        "version": hpf_session.get("version"),
                        "payment_token": hpf_session.get("paymentToken"),
                        "jwt_token": jwt_token,
                        "trace_id": hpf_session.get("traceID"),
                    },
                    "payment_session_patch": {
                        "hpf_initialized": True,
                        "hpf_payment_token": hpf_session.get("paymentToken"),
                    },
                }

            # Step 2: Backoffice Bearer token (legacy fallback)
            bearer_token = await self._get_backoffice_token()
            hpf_script: Optional[Dict[str, Any]] = None

            if bearer_token:
                hpf_script = await self._fetch_hpf_script(bearer_token)

            if hpf_script:
                return 200, {
                    **common_payload,
                    "flow": "hpf",
                    "hpf": {
                        "script_src": hpf_script["netvalveScriptSrc"],
                        "integrity": hpf_script.get("integrity"),
                        "version": hpf_script.get("clientVersion"),
                        "script_id": hpf_script.get("id"),
                    },
                    "payment_session_patch": {"hpf_initialized": True},
                }

            # Step 3: HPP fallback
            hpp_result = await self._try_hpp_fallback(
                bearer_token=bearer_token,
                body=body,
                checkout=checkout,
            )

            if hpp_result["success"] and hpp_result.get("data"):
                redirect_url = hpp_result["data"]["redirectUrl"]
                return 200, {
                    **common_payload,
                    "flow": "hpp",
                    "hpp": {
                        "redirect_url": redirect_url,
                        "order_id": hpp_result["data"].get("orderId"),
                        "transaction_id": hpp_result["data"].get("transactionID"),
                    },
                    "netvalve_endpoint": hpp_result.get("endpoint"),
                    "payment_session_patch": {
                        "requires_redirect": True,
                        "redirect_url": redirect_url,
                        "hpp_order_id": hpp_result["data"].get("orderId"),
                        "hpp_transaction_id": hpp_result["data"].get("transactionID"),
                    },
                }

            # Step 4: Final HPF fallback script
            fallback_script_src = _resolve_fallback_hpf_script_src()
            if fallback_script_src:
                diagnostic = self._build_diagnostic(
                    token_obtained=bool(bearer_token),
                    hpf_script_found=bool(hpf_script),
                    hpp_result=hpp_result,
                )
                return 200, {
                    **common_payload,
                    "flow": "hpf",
                    "hpf": {
                        "script_src": fallback_script_src,
                        "integrity": _env("NETVALVE_HPF_SCRIPT_INTEGRITY") or None,
                        "source": "fallback",
                    },
                    "payment_session_patch": {
                        "hpf_initialized": True,
                        "hpf_fallback_script": True,
                    },
                    "diagnostic": diagnostic,
                }

            # Step 5: Everything failed → diagnostic 502
            diagnostic = self._build_diagnostic(
                token_obtained=bool(bearer_token),
                hpf_script_found=bool(hpf_script),
                hpp_result=hpp_result,
            )

            return 502, {
                "message": "NetValve payment session could not be initialized",
                "diagnostic": diagnostic,
                "debug": {
                    "backoffice_token_obtained": bool(bearer_token),
                    "hpf_script_found": bool(hpf_script),
                    "hpp_fallback": {
                        "success": False,
                        "reason": hpp_result.get("reason"),
                        "attempts": [
                            {
                                "method": a.get("method"),
                                "url": a.get("url"),
                                "status": a.get("status"),
                                "body": a.get("body"),
                            }
                            for a in hpp_result.get("attempts", [])
                        ],
                    },
                },
            }

        except Exception as e:
            return 500, {
                "message": "Unexpected error initializing NetValve payment session",
                "error": str(e),
            }

    # ──────────────────────────────────────────────────────────────
    # POST /sale — core payment processing
    # ──────────────────────────────────────────────────────────────

    async def process_payment(
        self,
        data: Dict[str, Any],
        payment_type: str = "CARD",
    ) -> Dict[str, Any]:
        """
        Call NetValve POST /sale to process a payment.
        Returns a SaleResult-compatible dict.

        """
        # Step 1: Resolve payment token
        payment_token = _pick_string(
            data,
            "netvalve_token", "paymentToken", "payment_token", "hpf_payment_token",
        )

        if not payment_token:
            logger.warning(
                "[netvalve] processPaymentWithNetValve: no paymentToken found"
            )
            return {"success": False, "response_message": "No payment token available"}

        token_source = (
            "netvalve_token" if data.get("netvalve_token")
            else "paymentToken" if data.get("paymentToken")
            else "payment_token" if data.get("payment_token")
            else "hpf_payment_token"
        )

        logger.info(
            f"[netvalve] processPaymentWithNetValve: using token={payment_token[:12]}... "
            f"(source={token_source})"
        )

        # Step 2: Resolve amount and currency
        raw_amount = data.get("amount", 0)
        amount = float(raw_amount) if raw_amount else 0.0
        currency_code = (data.get("currency_code") or "USD").upper()
        netvalve_amount = round(amount, 2)

        logger.info(
            f"[netvalve] processPaymentWithNetValve — raw amount: {raw_amount}, "
            f"sending: {netvalve_amount}"
        )

        # Step 3: Resolve credentials
        client_id = self.client_id
        api_key = self.api_key
        site_id = self.site_id
        mid_id = _resolve_netvalve_mid_id(currency_code) or ""

        client_order_id = (
            _pick_string(data, "cartId", "cart_id", "client_order_id", "id")
            or f"medusa_{int(time.time())}"
        )

        # Step 4: Resolve enrichment fields
        raw_desc = (
            _pick_string(data, "order_description", "orderDescription")
            or f"Order {client_order_id}"
        )
        order_desc = _sanitize_order_description(raw_desc, f"Order {client_order_id}")

        email = _pick_string(data, "customer_email", "email_address", "emailAddress")

        client_ip = _pick_string(
            data, "client_ip_address", "ip_address", "ipAddress"
        )
        if not client_ip or LOOPBACK_IP_RE.match(client_ip):
            try:
                client_ip = await self._resolve_public_ip()
            except Exception:
                client_ip = ""

        # Step 5: Build POST /sale payload
        payload: Dict[str, Any] = {
            "amount": netvalve_amount,
            "currency": currency_code,
            "paymentType": payment_type,
            "paymentToken": payment_token,
            "siteId": site_id,
            "netvalveMidId": mid_id,
            "clientOrderId": client_order_id,
            "orderDesc": order_desc,
        }
        if email:
            payload["customerEmail"] = email
        if client_ip:
            payload["customerIp"] = client_ip
        payload.update(_build_customer_fields(data))

        logger.info(
            f"[netvalve] POST /sale — amount={netvalve_amount} {currency_code}, "
            f"clientOrderId={client_order_id}, paymentType={payment_type}"
        )

        # Step 6: Execute POST /sale
        url = f"{self.base_url}/sale"

        try:
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                resp = await http_client.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "netvalve-client-id": client_id,
                        "netvalve-api-key": api_key,
                    },
                )

            text = resp.text
            try:
                parsed = resp.json()
            except Exception:
                logger.error(
                    f"[netvalve] POST /sale non-JSON response: {text[:500]}"
                )
                return {
                    "success": False,
                    "response_code": str(resp.status_code),
                    "response_message": f"Non-JSON response: {text[:200]}",
                }

            # Step 7: Parse response fields
            response_code = str(parsed.get("responseCode", ""))
            response_message = str(parsed.get("responseMessage", ""))
            response_code_type = str(
                parsed.get("responseCodeType", "")
            ).upper()
            transaction_id = (
                str(parsed["transactionID"])
                if parsed.get("transactionID") is not None
                else None
            )
            order_id = (
                str(parsed["orderId"])
                if parsed.get("orderId") is not None
                else None
            )
            bank_response_code = (
                str(parsed["bankResponseCode"])
                if parsed.get("bankResponseCode") is not None
                else None
            )

            # Step 8: Decline detection
            has_decline_type = any(
                kw in response_code_type
                for kw in ["DECLINE", "FAILED", "REJECT"]
            )
            has_decline_message = bool(DECLINE_MESSAGE_RE.search(response_message))
            is_bank_decline = (
                response_code.startswith("BNK_")
                and response_code != APPROVED_BANK_CODE
            )
            has_bank_decline_code = (
                bank_response_code is not None
                and bank_response_code in DECLINE_BANK_CODES
            )
            decline_reason = (
                BANK_DECLINE_REASONS.get(bank_response_code)
                if bank_response_code
                else None
            )

            is_success = (
                resp.status_code < 400
                and response_code == APPROVED_RESPONSE_CODE
                and not has_decline_type
                and not has_decline_message
                and not is_bank_decline
                and not has_bank_decline_code
            )

            logger.info(
                f"[netvalve] POST /sale result — HTTP {resp.status_code}, "
                f"responseCode={response_code}, transactionID={transaction_id}, "
                f"success={is_success}"
            )

            # Extract card metadata
            response_card_type = (
                parsed.get("cardType") if isinstance(parsed.get("cardType"), str) else None
            )
            response_card_number = (
                parsed.get("cardNumber") if isinstance(parsed.get("cardNumber"), str) else None
            )

            # Expiry extraction with fallbacks
            response_card_expiry: Optional[str] = None
            for k in [
                "cardExpiry", "expiryDate", "cardExpiryDate",
                "cc_exp_date", "card_expiry", "exp_date",
                "expirationDate", "expiration_date",
                "card_exp", "cc_exp", "expiry",
            ]:
                if isinstance(parsed.get(k), str) and parsed[k]:
                    response_card_expiry = parsed[k]
                    break

            if not response_card_expiry:
                # Check split month/year fields
                m = (
                    parsed.get("cardExpiryMonth")
                    or parsed.get("expMonth")
                    or parsed.get("exp_month")
                    or parsed.get("expiryMonth")
                    or parsed.get("expiry_month")
                )
                y = (
                    parsed.get("cardExpiryYear")
                    or parsed.get("expYear")
                    or parsed.get("exp_year")
                    or parsed.get("expiryYear")
                    or parsed.get("expiry_year")
                )
                if m is not None and y is not None:
                    mm = str(m).zfill(2)
                    yyyy = str(y)
                    if len(yyyy) == 2:
                        yyyy = f"20{yyyy}"
                    elif len(yyyy) == 1:
                        yyyy = f"200{yyyy}"
                    response_card_expiry = f"{mm}/{yyyy}"

            # Gateway validation errors
            gateway_errors = (
                parsed["errors"]
                if isinstance(parsed.get("errors"), dict)
                else None
            )

            return {
                "success": is_success,
                "transaction_id": transaction_id,
                "order_id": order_id,
                "response_code": response_code,
                "response_message": response_message,
                "bank_response_code": bank_response_code,
                "decline_reason": decline_reason,
                "raw": parsed,
                "client_order_id": client_order_id,
                "payment_token": payment_token,
                "site_id": site_id,
                "mid_id": mid_id,
                "amount": netvalve_amount,
                "currency": currency_code,
                "gateway_errors": gateway_errors,
                "card_number": response_card_number,
                "card_type": response_card_type,
                "card_expiry": (
                    response_card_expiry
                    or _pick_string(data, "card_expiry", "cardExpiry")
                    or None
                ),
                "card_holder_name": (
                    _pick_string(data, "card_holder_name", "cardHolderName") or None
                ),
            }

        except Exception as e:
            logger.error(f"[netvalve] POST /sale error: {e}")
            return {
                "success": False,
                "response_message": f"Network error: {e}",
                "client_order_id": client_order_id,
                "payment_token": payment_token,
                "site_id": site_id,
                "mid_id": mid_id,
                "amount": netvalve_amount,
                "currency": currency_code,
            }

    # ──────────────────────────────────────────────────────────────
    # Authorize Payment
    # ──────────────────────────────────────────────────────────────

    async def authorize_payment(
        self, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Authorize a payment by calling POST /sale.
        Three sub-paths: HPF (CARD), TOKEN, External proof.

        """
        logger.info(
            f"[netvalve] authorizePayment called — data keys: {list(data.keys())}"
        )

        # Guard: no payment confirmation
        if not _has_payment_confirmation(data):
            return {
                "status": "requires_more",
                "data": {
                    **data,
                    "id": data.get("id") or f"netvalve_{int(time.time())}",
                    "status": "requires_more",
                    "requires_payment_input": True,
                    "payment_type": "card",
                    "message": "NetValve payment requires card details before authorizing the order.",
                },
            }

        # Guard: idempotency — sale already succeeded
        if data.get("netvalve_sale_success") is True and data.get(
            "netvalve_transaction_id"
        ):
            logger.info(
                f"[netvalve] authorizePayment — sale already succeeded "
                f"(txn={data.get('netvalve_transaction_id')}), returning AUTHORIZED"
            )
            return {
                "status": "authorized",
                "data": {
                    **data,
                    "id": data.get("id") or f"netvalve_{int(time.time())}",
                    "status": "authorized",
                    "requires_payment_input": False,
                    "authorized_at": data.get("authorized_at")
                    or _iso_now(),
                },
            }

        # Path 3a: HPF flow — POST /sale CARD
        if data.get("hpf_completed") is True:
            logger.info(
                "[netvalve] authorizePayment — HPF flow, calling POST /sale CARD"
            )
            sale_result = await self.process_payment(data, payment_type="CARD")

            if not sale_result.get("success"):
                return self._build_decline_response_dict(data, sale_result, {"payment_flow": "hpf"})

            return self._build_authorized_response_dict(data, sale_result, {"payment_flow": "hpf"})

        # Path 3b: TOKEN flow
        hpf_session_token = data.get("hpf_payment_token", "")
        stored_token = data.get("netvalve_token", "")

        if (
            isinstance(stored_token, str)
            and stored_token
            and stored_token != hpf_session_token
        ):
            logger.info(
                "[netvalve] authorizePayment — stored card token, calling POST /sale TOKEN"
            )
            sale_result = await self.process_payment(data, payment_type="TOKEN")

            if not sale_result.get("success"):
                return self._build_decline_response_dict(data, sale_result)

            return self._build_authorized_response_dict(data, sale_result)

        # Path 3c: External transaction proof (HPP / webhook callback)
        external_keys = [
            "transaction_id", "transactionId", "netvalve_transaction_id",
            "order_id", "orderId",
        ]
        has_external_proof = any(
            isinstance(data.get(k), str) and data.get(k)
            for k in external_keys
        )

        if not has_external_proof:
            logger.warning(
                "[netvalve] authorizePayment — refusing local auth without transaction proof"
            )
            return {
                "status": "requires_more",
                "data": {
                    **data,
                    "id": data.get("id") or f"netvalve_{int(time.time())}",
                    "status": "requires_more",
                    "requires_payment_input": True,
                    "netvalve_sale_attempted": False,
                    "netvalve_sale_success": False,
                    "error_message": "Payment declined — unable to verify payment with NetValve. Please try a different card.",
                },
            }

        logger.info(
            "[netvalve] authorizePayment — external transaction proof, authorizing locally"
        )
        return {
            "status": "authorized",
            "data": {
                **data,
                "id": data.get("id") or f"netvalve_{int(time.time())}",
                "status": "authorized",
                "requires_payment_input": False,
                "authorized_at": _iso_now(),
            },
        }

    # ──────────────────────────────────────────────────────────────
    # Capture
    # ──────────────────────────────────────────────────────────────

    async def capture_payment(
        self,
        transaction_id: str,
        amount: float,
        already_captured: bool = False,
    ) -> Dict[str, Any]:
        """
        Capture authorized funds via POST /capture.
        """
        if already_captured:
            logger.info(
                f"[netvalve] capture — already captured, skipping API call"
            )
            return {
                "status": "captured",
                "transaction_id": transaction_id,
                "data": {},
            }

        try:
            url = f"{self.base_url}/capture"
            capture_amount = round(amount, 2)

            async with httpx.AsyncClient(timeout=15.0) as http_client:
                resp = await http_client.post(
                    url,
                    json={
                        "transactionID": int(transaction_id),
                        "amount": capture_amount,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "netvalve-client-id": self.client_id,
                        "netvalve-api-key": self.api_key,
                    },
                )

            parsed = resp.json() if resp.status_code < 500 else {}
            logger.info(
                f"[netvalve] POST /capture — HTTP {resp.status_code}, "
                f"responseCode={parsed.get('responseCode', 'N/A')}"
            )

            return {
                "status": "captured",
                "transaction_id": transaction_id,
                "response_code": parsed.get("responseCode"),
                "response_message": parsed.get("responseMessage"),
                "data": parsed,
            }

        except Exception as e:
            logger.error(f"[netvalve] POST /capture error: {e}")
            return {
                "status": "capture_error",
                "transaction_id": transaction_id,
                "error": str(e),
                "data": {},
            }

    # ──────────────────────────────────────────────────────────────
    # Refund
    # ──────────────────────────────────────────────────────────────

    async def refund_payment(
        self,
        transaction_id: str,
        amount: float,
    ) -> Dict[str, Any]:
        """
        Refund a captured payment via POST /refund.
        """
        try:
            url = f"{self.base_url}/refund"
            refund_amount = round(amount, 2)

            async with httpx.AsyncClient(timeout=15.0) as http_client:
                resp = await http_client.post(
                    url,
                    json={
                        "transactionID": int(transaction_id),
                        "amount": refund_amount,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "netvalve-client-id": self.client_id,
                        "netvalve-api-key": self.api_key,
                    },
                )

            parsed = resp.json() if resp.status_code < 500 else {}
            logger.info(
                f"[netvalve] POST /refund — HTTP {resp.status_code}, "
                f"responseCode={parsed.get('responseCode', 'N/A')}"
            )

            return {
                "status": "refunded",
                "transaction_id": transaction_id,
                "refunded_amount": refund_amount,
                "response_code": parsed.get("responseCode"),
                "response_message": parsed.get("responseMessage"),
                "data": parsed,
            }

        except Exception as e:
            logger.error(f"[netvalve] POST /refund error: {e}")
            return {
                "status": "refund_error",
                "transaction_id": transaction_id,
                "error": str(e),
                "data": {},
            }

    # ──────────────────────────────────────────────────────────────
    # Cancel
    # ──────────────────────────────────────────────────────────────

    async def cancel_payment(
        self, transaction_id: str
    ) -> Dict[str, Any]:
        """
        Cancel (void) an authorized payment via POST /cancel.
        """
        try:
            url = f"{self.base_url}/cancel"

            async with httpx.AsyncClient(timeout=15.0) as http_client:
                resp = await http_client.post(
                    url,
                    json={"transactionID": int(transaction_id)},
                    headers={
                        "Content-Type": "application/json",
                        "netvalve-client-id": self.client_id,
                        "netvalve-api-key": self.api_key,
                    },
                )

            parsed = resp.json() if resp.status_code < 500 else {}
            logger.info(
                f"[netvalve] POST /cancel — HTTP {resp.status_code}, "
                f"responseCode={parsed.get('responseCode', 'N/A')}"
            )

            return {
                "status": "canceled",
                "transaction_id": transaction_id,
                "response_code": parsed.get("responseCode"),
                "response_message": parsed.get("responseMessage"),
                "data": parsed,
            }

        except Exception as e:
            logger.error(f"[netvalve] POST /cancel error: {e}")
            return {
                "status": "cancel_error",
                "transaction_id": transaction_id,
                "error": str(e),
                "data": {},
            }

    # ──────────────────────────────────────────────────────────────
    # Webhook processing
    # ──────────────────────────────────────────────────────────────

    def process_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map inbound webhook event type to an action.
        """
        event_type = payload.get("type")
        if not event_type or not isinstance(event_type, str):
            return {"action": "NOT_SUPPORTED"}

        normalized = event_type.lower()

        # Extract webhook data
        webhook_data = None
        session_id = payload.get("session_id") or payload.get("id")
        amount = payload.get("amount")
        if session_id and (isinstance(amount, (str, int, float))):
            webhook_data = {"session_id": session_id, "amount": amount}

        if "authorized" in normalized:
            return {"action": "AUTHORIZED", "data": webhook_data}
        if "captured" in normalized or "paid" in normalized:
            return {"action": "SUCCESSFUL", "data": webhook_data}
        if "pending" in normalized:
            return {"action": "PENDING", "data": webhook_data}
        if "requires_more" in normalized or "action_required" in normalized:
            return {"action": "REQUIRES_MORE", "data": webhook_data}
        if "failed" in normalized or "declined" in normalized:
            return {"action": "FAILED", "data": webhook_data}
        if "canceled" in normalized or "cancelled" in normalized:
            return {"action": "CANCELED", "data": webhook_data}

        return {"action": "NOT_SUPPORTED", "data": webhook_data}

    # ──────────────────────────────────────────────────────────────
    # Internal response builders
    # ──────────────────────────────────────────────────────────────

    def _build_decline_response_dict(
        self,
        input_data: Dict[str, Any],
        sale_result: Dict[str, Any],
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build a requires_more decline response from a failed SaleResult.
        """
        decline_detail = _format_decline_detail(sale_result)

        merged: Dict[str, Any] = {
            **input_data,
            "id": input_data.get("id") or f"netvalve_{int(time.time())}",
            "status": "requires_more",
            "requires_payment_input": True,
            "netvalve_sale_attempted": True,
            "netvalve_sale_success": False,
            "netvalve_response_code": sale_result.get("response_code"),
            "netvalve_response_message": sale_result.get("response_message"),
            "netvalve_bank_response_code": sale_result.get("bank_response_code"),
            "netvalve_decline_reason": sale_result.get("decline_reason"),
            "error_message": f"Payment declined{decline_detail}. Please try a different card.",
        }

        # Persist request-side identifiers
        for key in [
            "client_order_id", "payment_token", "site_id", "mid_id",
            "amount", "currency",
        ]:
            if sale_result.get(key) is not None:
                merged[key] = sale_result[key]

        # Gateway errors
        if sale_result.get("gateway_errors"):
            merged["errors"] = sale_result["gateway_errors"]

        # Card metadata
        for key in ["card_number", "card_type", "card_expiry", "card_holder_name"]:
            if sale_result.get(key):
                merged[key] = sale_result[key]

        if extra_fields:
            merged.update(extra_fields)

        return {"status": "requires_more", "data": merged}

    def _build_authorized_response_dict(
        self,
        input_data: Dict[str, Any],
        sale_result: Dict[str, Any],
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build an authorized response from a successful SaleResult.
        """
        merged: Dict[str, Any] = {
            **input_data,
            "id": input_data.get("id") or f"netvalve_{int(time.time())}",
            "status": "authorized",
            "requires_payment_input": False,
            "authorized_at": _iso_now(),
            "netvalve_sale_attempted": True,
            "netvalve_sale_success": True,
            "netvalve_transaction_id": sale_result.get("transaction_id"),
            "netvalve_order_id": sale_result.get("order_id"),
            "netvalve_response_code": sale_result.get("response_code"),
            "netvalve_response_message": sale_result.get("response_message"),
        }

        # Persist request-side identifiers
        for key in [
            "client_order_id", "payment_token", "site_id", "mid_id",
            "amount", "currency",
        ]:
            if sale_result.get(key) is not None:
                merged[key] = sale_result[key]

        # Gateway errors
        if sale_result.get("gateway_errors"):
            merged["errors"] = sale_result["gateway_errors"]

        # Card metadata
        for key in ["card_number", "card_type", "card_expiry", "card_holder_name"]:
            if sale_result.get(key):
                merged[key] = sale_result[key]

        if extra_fields:
            merged.update(extra_fields)

        return {"status": "authorized", "data": merged}


def _iso_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# Module-level singleton
netvalve_service = NetValveService()
