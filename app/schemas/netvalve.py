"""
Pydantic models for all NetValve API routes.

               medusa_ordergroove_be/src/api/store/netvalve/hpf/session/route.ts
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────
#  HPF Session – POST /api/v1/netvalve/hpf/session
# ──────────────────────────────────────────────────────────────────────


class HpfSessionRequest(BaseModel):
    """Request body for HPF session initialization."""

    version: Optional[str] = None
    currency_code: Optional[str] = None
    amount: Optional[float] = None
    cart_id: Optional[str] = None
    order_desc: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None
    failed_url: Optional[str] = None
    pending_url: Optional[str] = None


class HpfScriptInfo(BaseModel):
    """HPF script metadata returned by the backoffice API."""

    script_src: str
    integrity: Optional[str] = None
    version: Optional[str] = None
    script_id: Optional[int] = None
    payment_token: Optional[str] = None
    jwt_token: Optional[str] = None
    trace_id: Optional[str] = None
    source: Optional[str] = None  # "fallback" when using fallback script


class HppInfo(BaseModel):
    """HPP redirect info."""

    redirect_url: str
    order_id: Optional[str] = None
    transaction_id: Optional[str] = None


class PaymentSessionPatch(BaseModel):
    """Patch data to apply to the frontend payment session."""

    hpf_initialized: Optional[bool] = None
    hpf_payment_token: Optional[str] = None
    hpf_fallback_script: Optional[bool] = None
    requires_redirect: Optional[bool] = None
    redirect_url: Optional[str] = None
    hpp_order_id: Optional[str] = None
    hpp_transaction_id: Optional[str] = None


class HpfSessionResponse(BaseModel):
    """Successful HPF session initialization response."""

    provider: str = "netvalve"
    environment: str = "production"
    currency_code: Optional[str] = None
    site_id: Optional[str] = None
    client_id: Optional[str] = None
    netvalve_mid_id: Optional[str] = None
    flow: str  # "hpf" or "hpp"
    hpf: Optional[HpfScriptInfo] = None
    hpp: Optional[HppInfo] = None
    netvalve_endpoint: Optional[Dict[str, str]] = None
    payment_session_patch: Optional[PaymentSessionPatch] = None
    diagnostic: Optional[str] = None


class HpfSessionErrorResponse(BaseModel):
    """Error response when HPF session initialization fails."""

    message: str
    diagnostic: Optional[str] = None
    debug: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────
#  Sale / Payment – POST /api/v1/netvalve/payment
# ──────────────────────────────────────────────────────────────────────


class SaleRequest(BaseModel):
    """
    Request body for processing a payment via NetValve POST /sale.
    """

    # Session / token data
    netvalve_token: Optional[str] = None
    payment_token: Optional[str] = Field(None, description="Also accepts 'paymentToken'")
    hpf_payment_token: Optional[str] = None

    # Amount & currency
    amount: Optional[float] = None
    currency_code: Optional[str] = "USD"

    # Payment type override
    payment_type: Optional[str] = "CARD"  # CARD or TOKEN

    # Customer fields
    customer_email: Optional[str] = None
    customer_first_name: Optional[str] = None
    customer_last_name: Optional[str] = None
    card_holder_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    customer_city: Optional[str] = None
    customer_state: Optional[str] = None
    customer_zip_code: Optional[str] = None
    customer_country_code: Optional[str] = None

    # Order description
    order_description: Optional[str] = None

    # Client IP
    client_ip_address: Optional[str] = None

    # Order/cart identifiers
    cart_id: Optional[str] = None
    client_order_id: Optional[str] = None

    # Auth flags (from frontend HPF)
    hpf_completed: Optional[bool] = None
    card_form_submitted: Optional[bool] = None
    authorized: Optional[bool] = None
    is_authorized: Optional[bool] = None

    # Transaction proof (webhook/HPP)
    transaction_id: Optional[str] = None
    netvalve_transaction_id: Optional[str] = None
    order_id: Optional[str] = None
    checkout_id: Optional[str] = None

    # Idempotency guard
    netvalve_sale_success: Optional[bool] = None

    class Config:
        populate_by_name = True


class SaleResult(BaseModel):
    """
    Result from calling NetValve POST /sale.
    """

    success: bool = False
    transaction_id: Optional[str] = None
    order_id: Optional[str] = None
    response_code: Optional[str] = None
    response_message: Optional[str] = None
    bank_response_code: Optional[str] = None
    decline_reason: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    # Echoed request-side identifiers
    client_order_id: Optional[str] = None
    payment_token: Optional[str] = None
    site_id: Optional[str] = None
    mid_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None

    # Gateway validation errors
    gateway_errors: Optional[Dict[str, Any]] = None

    # Card metadata
    card_number: Optional[str] = None
    card_type: Optional[str] = None
    card_expiry: Optional[str] = None
    card_holder_name: Optional[str] = None


class PaymentResponse(BaseModel):
    """
    Response for sale/authorize endpoint.
    """

    status: str  # AUTHORIZED, REQUIRES_MORE, etc.
    data: Dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────────
#  Capture – POST /api/v1/netvalve/capture
# ──────────────────────────────────────────────────────────────────────


class CaptureRequest(BaseModel):
    """
    Request body for capturing an authorized payment.
    """

    transaction_id: str = Field(..., description="NetValve transaction ID")
    amount: float = Field(..., description="Amount to capture")
    already_captured: bool = Field(
        False,
        description="If true, sale was already captured (no-op)"
    )


class CaptureResponse(BaseModel):
    """Response from capture endpoint."""

    status: str = "captured"
    transaction_id: Optional[str] = None
    response_code: Optional[str] = None
    response_message: Optional[str] = None
    data: Dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────────
#  Refund – POST /api/v1/netvalve/refund
# ──────────────────────────────────────────────────────────────────────


class RefundRequest(BaseModel):
    """
    Request body for refunding a captured payment.
    """

    transaction_id: str = Field(..., description="NetValve transaction ID")
    amount: float = Field(..., description="Amount to refund")


class RefundResponse(BaseModel):
    """Response from refund endpoint."""

    status: str = "refunded"
    transaction_id: Optional[str] = None
    refunded_amount: Optional[float] = None
    response_code: Optional[str] = None
    response_message: Optional[str] = None
    data: Dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────────
#  Cancel – POST /api/v1/netvalve/cancel
# ──────────────────────────────────────────────────────────────────────


class CancelRequest(BaseModel):
    """
    Request body for cancelling (voiding) an authorized payment.
    """

    transaction_id: str = Field(..., description="NetValve transaction ID")


class CancelResponse(BaseModel):
    """Response from cancel endpoint."""

    status: str = "canceled"
    transaction_id: Optional[str] = None
    response_code: Optional[str] = None
    response_message: Optional[str] = None
    data: Dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────────
#  Webhook – POST /api/v1/netvalve/webhook
# ──────────────────────────────────────────────────────────────────────


class WebhookPayload(BaseModel):
    """
    Incoming webhook payload from NetValve.
    """

    type: Optional[str] = None
    session_id: Optional[str] = None
    id: Optional[str] = None
    amount: Optional[float] = None
    transaction_id: Optional[str] = None
    order_id: Optional[str] = None
    response_code: Optional[str] = None
    response_message: Optional[str] = None

    class Config:
        extra = "allow"


class WebhookResponse(BaseModel):
    """Response after processing a NetValve webhook."""

    action: str  # AUTHORIZED, SUCCESSFUL, FAILED, etc.
    data: Optional[Dict[str, Any]] = None


# ──────────────────────────────────────────────────────────────────────
#  Status – GET /api/v1/netvalve/status
# ──────────────────────────────────────────────────────────────────────


class PaymentStatusResponse(BaseModel):
    """Response for payment status lookup."""

    status: str
    transaction_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
