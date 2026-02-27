from fastapi import APIRouter
from app.api.v1.endpoints import webhooks, payments, test_token_customer, ordergroove_order

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
