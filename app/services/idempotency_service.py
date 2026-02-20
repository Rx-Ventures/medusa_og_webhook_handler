import logging 

from app.core.unit_of_work import UnitOfWork
from app.schemas.webhook import WebhookEventCreate, WebhookEventResponse

logger = logging.getLogger(__name__)
# from app.crud import (
#     webhook_events as webhook_events_crud
# )
# from sqlalchemy.orm import Session

class IdempotencyService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    # async def create_webhook_event(
    #         self,
    #         db: Session,
    #         webhook_event_log: WebhookEventCreate
    #     ) -> WebhookEventResponse:
    #     try:

    #         print(f"webhook_event_log: {webhook_event_log}")

    #         existing_webhook = await webhook_events_crud.get_webhook_event_by_event_id(
    #             db,
    #             webhook_event_log.event_id
    #         )
    #         if existing_webhook:
    #             logger.info(f"Webhook event log already exists: {webhook_event_log.event_id}")
    #             return False
    #         webhook_event = await webhook_events_crud.create_webhook_event(db, webhook_event_log)
    #         if webhook_event:
    #             return webhook_event
    #         else:
    #             logger.info(f"Error creating webhook_event log")
    #     except Exception as e:
    #         logger.error(f"Error getting subscription webhook_event: {str(e)}")
    #         raise HTTPException(
    #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #             detail="An unexpected error occurred"
    #         )

    async def check_and_create_webhook_event(
        self,
        webhook_data: WebhookEventCreate
    ) -> WebhookEventResponse | None:
        if await self.uow.webhook_events.exists_by_event_id(webhook_data.event_id):
            logger.info(f"Webhook event already exists: {webhook_data.event_id}")
            return None
        
        webhook_event = await self.uow.webhook_events.create(
            **webhook_data.model_dump()
        )

        await self.uow.commit()

        logger.info(f"Created webhook event: {webhook_event.id}")
        return WebhookEventResponse.model_validate(webhook_event)
    
async def get_idempotency_service(uow: UnitOfWork) -> IdempotencyService: 
    return IdempotencyService(uow)