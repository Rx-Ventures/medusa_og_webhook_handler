from datetime import datetime
from pydantic import BaseModel, ConfigDict

class TimestampMixin(BaseModel):
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

class AuditLogMixin(BaseModel):
    created_by_user_id: int | None = None
    updated_by_user_id: int | None = None
    deleted_by_user_id: int | None = None

class IDMixin(BaseModel):
    id: str | None = None

class BaseDBSchema(IDMixin, TimestampMixin):
    pass

class BaseDBSchemaWithAudit(BaseDBSchema, AuditLogMixin):
    pass