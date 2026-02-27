from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_prefixed_id


def generate_ttc_id() -> str:
    return generate_prefixed_id("ttc")


class TestTokenCustomer(TimestampMixin, Base):
    __tablename__ = "zzz_test_token_customer"

    id: Mapped[str] = mapped_column(
        String(50), primary_key=True, default=generate_ttc_id
    )
    customer_id: Mapped[str] = mapped_column(String(255), index=True)
    order_id: Mapped[str] = mapped_column(String(255), index=True)
    payment_token: Mapped[str | None] = mapped_column(Text, nullable=True)
