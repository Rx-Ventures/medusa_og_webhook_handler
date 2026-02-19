from pydantic import BaseModel, ConfigDict

from app.schemas.base import BaseDBSchema

class WebhookEventBase(BaseModel):
    event_id: str | None = None
    psp: str | None = None
    event_type: str | None = None
    medusa_order_id: str | None = None
    processed: bool = False
    error_message: str | None = None

    model_config = ConfigDict(from_attributes=True)
    
    def to_json(self):
        return self.model_dump_json()

class WebhookEventCreate(WebhookEventBase):
    event_id: str
    psp: str
    event_type: str
    payload: dict

class WebhookEventResponse(WebhookEventBase, BaseDBSchema):
    pass

# class WebhookAck(BaseModel):
#     success: bool = True
#     message: str = "Webhook received"