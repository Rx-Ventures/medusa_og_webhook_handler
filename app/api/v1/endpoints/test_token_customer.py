from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.models.test_token_customer import TestTokenCustomer
from app.schemas.test_token_customer import (
    TestTokenCustomerCreate,
    TestTokenCustomerResponse,
)

router = APIRouter()


@router.post(
    "/",
    response_model=TestTokenCustomerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_test_token_customer(
    payload: TestTokenCustomerCreate,
    session: AsyncSession = Depends(get_db_session),
):
    record = TestTokenCustomer(
        customer_id=payload.customer_id,
        order_id=payload.order_id,
        payment_token=payload.payment_token,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record
