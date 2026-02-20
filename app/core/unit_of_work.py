from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.webhook import WebhookEventRepository

class UnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._webhook_events: WebhookEventRepository | None = None

    async def __aenter__(self) -> "UnitOfWork":
        self._session = self._session_factory()
        return self 
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            await self.rollback()
        if self._session:
            await self._session.close()

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork session not initialized. Use 'async with uow'")
        return self._session

    @property
    def webhook_events(self) -> WebhookEventRepository:
        if self._webhook_events is None:
            self._webhook_events = WebhookEventRepository(self.session)
        return self._webhook_events
    
    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def flush(self) -> None:
        await self.session.flush()
