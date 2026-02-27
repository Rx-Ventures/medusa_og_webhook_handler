from pydantic import BaseModel
from datetime import datetime


class TestTokenCustomerCreate(BaseModel):
    customer_id: str
    order_id: str
    payment_token: str | None = None


class TestTokenCustomerResponse(BaseModel):
    id: str
    customer_id: str
    order_id: str
    payment_token: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
