from fastapi import APIRouter
from app.api.v1.endpoints import (
    webhooks,
    payments,
    test_token_customer,
    ordergroove_order,
    solidgate,
)
from app.api.v1.endpoints import webhooks, payments
from app.api.v1.endpoints.netvalve.router import netvalve_router

api_router = APIRouter()

api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])


api_router.include_router(payments.router, prefix="/payments", tags=["payments"])


api_router.include_router(
    test_token_customer.router,
    prefix="/test-token-customer",
    tags=["test-token-customer"],
)


api_router.include_router(
    ordergroove_order.router,
    prefix="/ordergroove",
    tags=["ordergroove"],
)

# NetValve payment gateway routes — prefix /netvalve
# Full paths: /api/v1/netvalve/hpf/session, /api/v1/netvalve/payment, etc.
api_router.include_router(
    netvalve_router,
    prefix="/netvalve",
    tags=["netvalve"],
)

# Solidgate routes — prefix /solidgate
# Full paths: /api/v1/solidgate/refund
api_router.include_router(
    solidgate.router,
    prefix="/solidgate",
    tags=["solidgate"],
)
