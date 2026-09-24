"""
Base SQLAlchemy model with common fields and mixins.

This module provides the declarative base and base model class for all ORM models.
All models inherit from this base to get standard fields (id, created_at, updated_at).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, declarative_base, mapped_column


class BaseModel:
    """
    Base model with common fields for all entities.

    Provides:
    - id: UUID primary key (auto-generated)
    - created_at: Server-set timestamp (UTC)
    - updated_at: Auto-updated timestamp on insert/update
    """

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


Base = declarative_base(cls=BaseModel)

# Plain base without the UUID id/created_at/updated_at mixin — use for
# models that define their own primary key (e.g. AppConfig uses `key`).
PlainBase = declarative_base()
