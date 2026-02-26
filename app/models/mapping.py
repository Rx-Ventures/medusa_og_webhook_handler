

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


from app.models.base import Base, TimestampMixin, generate_prefixed_id

def generate_mappings_id() -> str:
    return generate_prefixed_id("migim")

class MigrationIdMappings(TimestampMixin,Base):
    __tablename__ = "migration_id_mappings"

    __table_args__ = (
        UniqueConstraint('entity_type', 'source_id', name='uq_entity_source'),
    )
    
    id: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
        default=generate_mappings_id,
    )
    entity_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    source_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    target_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    
    def __repr__(self) -> str:
        return f"<MigrationIdMappings {self.id} {self.entity_type}:{self.source_id}>"