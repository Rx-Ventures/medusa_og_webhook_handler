from typing import AsyncGenerator

from app.core.database import AsyncSessionLocal
from app.core.unit_of_work import UnitOfWork

async def get_unit_of_work() -> AsyncGenerator[UnitOfWork, None]:
    uow = UnitOfWork(AsyncSessionLocal)
    async with uow:
        yield uow