from pydantic import BaseModel, ConfigDict

class PaymentInitializeBase(BaseModel):
    order_id: str | None = None
    amount: int | None = None
    currency: str = "USD"
    customer_email: str | None = None
    psp: str = "solidgate"
    
    model_config = ConfigDict(from_attributes=True)
    
    def to_json(self):
        return self.model_dump_json()


class PaymentInitializeRequest(PaymentInitializeBase):
    order_id: str
    amount: int
    customer_email: str


class PaymentInitializeResponse(BaseModel):
    session_id: str
    psp: str
    merchant: str
    signature: str
    payment_intent: str


class UpdateReferenceRequest(BaseModel):
    """Maps a Solidgate payment (keyed by cart_id) to the final Medusa order_id."""
    cart_id: str
    order_id: str