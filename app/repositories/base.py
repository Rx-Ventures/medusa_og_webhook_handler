from typing import Any, Generic, Sequence, TypeVar 

from sqlalchemy import select, update, delete 
from sqlalchemy.ext.asyncio import AsyncSession 

from app.models.base import Base 

ModelType = TypeVar("ModelType", bound=Base)

class BaseRepository(Generic[ModelType]):
    def __init__(self, model: type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session


    async def get_by_id(self, id: str) -> ModelType | None:
        result = await self.session.execute(
            select(self.model).where(self.model.id == id)
        )

        return result.scalar_one_or_none()
    

    async def get_all(self, limit: int = 100, offset: int = 0) -> Sequence[ModelType] | None:
        result = await self.session.execute(
            select(self.model).limit(limit).offset(offset)
        )

        return result.scalars().all()
    
    async def create(self, **kwargs: Any) -> ModelType:
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        return instance 
    
    async def update_by_id(self, id: str, **kwargs: Any) -> ModelType | None:
        await self.session.execute(
            update(self.model)
            .where(self.model.id == id)
            .values(**kwargs)
        )

        await self.session.flush()
        return await self.get_by_id(id)
    

    async def delete_by_id(self, id:str) -> bool:
        result = await self.session.execute(
            delete(self.model)
            .where(self.model.id == id)
        )
        
        await self.session.flush()
        return result.rowcount > 0