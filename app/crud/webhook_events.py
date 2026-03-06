import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import WebhookEvent
from app.schemas.webhook import WebhookEventCreate, WebhookEventResponse

logger = logging.getLogger(__name__)


#To be removed
async def create_webhook_event(
    db: AsyncSession, 
    webhook_event: WebhookEventCreate
) -> WebhookEventResponse:
    try:
        db_webhook_event = WebhookEvent(**webhook_event.model_dump())
        db.add(db_webhook_event)
        await db.commit()
        await db.refresh(db_webhook_event)
        
        logger.info("webhook_event created successfully")
        
        return WebhookEventResponse.model_validate(db_webhook_event)
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create webhook_event: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


#To be removed
async def get_webhook_event_by_event_id(
    db: AsyncSession, 
    event_id: str
):
    result = await db.execute(
        select(WebhookEvent).where(WebhookEvent.event_id == event_id)
    )
    return result.scalar_one_or_none()