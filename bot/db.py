"""Engine, session helpers and create_all_tables. The database comes from DATABASE_URL."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from .config import Settings
from .models import metadata


def make_engine(url: str) -> Engine:
    kwargs = {}
    if url.startswith("sqlite"):
        # Webhook work runs in worker threads, so SQLite connections must be shareable.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine(Settings.from_env().database_url)


def get_session() -> Iterator[Session]:
    """Yield a session; roll back on error and always close it."""
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


session_scope = contextmanager(get_session)


def create_all_tables(engine: Engine | None = None) -> None:
    """Create missing tables with create_all. For tests/throwaway setups; use Alembic for real schemas."""
    metadata.create_all(engine or get_engine())
