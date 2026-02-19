from fastapi import APIRouter
from app.api.v1.endpoints import webhooks, payments, ordergroove

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
    ordergroove.router, 
    prefix="/ordergroove", 
    tags=["ordergroove"]
)
