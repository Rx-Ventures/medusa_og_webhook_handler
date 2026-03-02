from fastapi import APIRouter
from app.api.v1.endpoints import webhooks, payments
from app.api.v1.endpoints.netvalve.router import netvalve_router

api_router = APIRouter()

api_router.include_router(
    webhooks.router, 
    prefix="/webhooks", 
    tags=["webhooks"]
)


api_router.include_router(
    payments.router, 
    prefix="/payments", 
    tags=["payments"]
)

# NetValve payment gateway routes — prefix /netvalve
# Full paths: /api/v1/netvalve/hpf/session, /api/v1/netvalve/payment, etc.
api_router.include_router(
    netvalve_router,
    prefix="/netvalve",
    tags=["netvalve"],
)
