from typing import AsyncGenerator
from uuid import uuid4
from sqlalchemy.pool import NullPool

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from app.core.config import settings

class Base(DeclarativeBase):
    pass

engine: AsyncEngine = create_async_engine(
    url=settings.DATABASE_URL,
    poolclass=NullPool,
    echo=settings.DB_ECHO,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",  
    },
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# wont remove since this was the working part 

# async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
#     session = AsyncSessionLocal()
#     try:
#         yield session
#         await session.commit()
#     except Exception:
#         await session.rollback()
#         raise
#     finally:
#         await session.close()

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

# async def check_db_health() -> dict | None:
#     session = AsyncSessionLocal()
#     try:
#         result = await session.execute(text("SELECT version()"))
#         version = result.scalar()
#         return {"status": "healthy", "version": version}
#     except Exception as e:
#         print(f"Database health check failed: {e}")
#         return False 
#     finally:
#         await session.close()
    
async def check_db_health() -> dict | None:
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(text("SELECT version()"))
            version = result.scalar()
            return {"status": "healthy", "version": version}
        except Exception as e:
            print(f"Database health check failed: {e}")
            return None 

if __name__ == "__main__":
    import asyncio

    async def test():
        print("Testing DB connection")

        result = await check_db_health()
        if await check_db_health():
            print("DB connected")
            print(result)
        else:
            print("Db connection failed from test")

    asyncio.run(test())