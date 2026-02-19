from sqlalchemy import select, update

from app.models.webhook import WebhookEvent
from app.repositories.base import BaseRepository


class WebhookEventRepository(BaseRepository[WebhookEvent]):
    def __init__(self, session):
        super().__init__(WebhookEvent, session)

    async def get_by_event_id(self, event_id:str) -> WebhookEvent | None:
        result = await self.session.execute(
            select(WebhookEvent)
            .where(WebhookEvent.event_id == event_id)
        )

        return result.scalar_one_or_none()
    
    async def exists_by_event_id(self, event_id:str) -> bool:
        result = await self.get_by_event_id(event_id)
        return result is not None
    
    async def mark_as_processed(self, id:  str) -> WebhookEvent | None:
        return await self.update_by_id(id, processed=True)
    
    async def mark_as_failed(self, id: str, error_message: str) -> WebhookEvent | None:
        return await self.update_by_id(id, processed=False, error_message=error_message)