"""Database tables, declared as Python classes with SQLAlchemy's registry decorator."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Date, DateTime, String, Time, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, registry

mapper_registry = registry()
metadata = mapper_registry.metadata

# A list of strings: native ARRAY on PostgreSQL, JSON everywhere else (SQLite has no arrays).
StringList = JSON().with_variant(ARRAY(String), "postgresql")


@mapper_registry.mapped_as_dataclass
class Event:
    """A scheduled event. created_at/updated_at are filled by the database (UTC on SQLite)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, init=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    time: Mapped[dt.time | None] = mapped_column(Time, nullable=True, default=None)
    prices: Mapped[list[str] | None] = mapped_column(StringList, nullable=True, default=None)
    extra_info: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), init=False
    )
