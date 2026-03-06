"""
NetValve Router Aggregator.

Combines all NetValve sub-routers into a single router with
prefix /netvalve. When registered in the main app under /api/v1,
the full paths become:

  POST /api/v1/netvalve/hpf/session   — HPF session initialization
  GET  /api/v1/netvalve/hpf/session   — HPF session (GET convenience)
  POST /api/v1/netvalve/payment       — Process payment (authorize/sale)
  POST /api/v1/netvalve/capture       — Capture authorized payment
  POST /api/v1/netvalve/refund        — Refund captured payment
  POST /api/v1/netvalve/cancel        — Cancel (void) payment
  POST /api/v1/netvalve/webhook       — NetValve webhook receiver
  GET  /api/v1/netvalve/status        — Payment status lookup

"""

from fastapi import APIRouter

from app.api.v1.endpoints.netvalve.hpf import router as hpf_router
from app.api.v1.endpoints.netvalve.payment import router as payment_router
from app.api.v1.endpoints.netvalve.capture import router as capture_router
from app.api.v1.endpoints.netvalve.refund import router as refund_router
from app.api.v1.endpoints.netvalve.cancel import router as cancel_router
from app.api.v1.endpoints.netvalve.webhook import router as webhook_router
from app.api.v1.endpoints.netvalve.status import router as status_router

# Main NetValve router — prefix is applied in api.py as /netvalve
netvalve_router = APIRouter()

# Include all sub-routers (paths are defined within each module)
netvalve_router.include_router(hpf_router)
netvalve_router.include_router(payment_router)
netvalve_router.include_router(capture_router)
netvalve_router.include_router(refund_router)
netvalve_router.include_router(cancel_router)
netvalve_router.include_router(webhook_router)
netvalve_router.include_router(status_router)
