from datetime import date, time

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bot.db import create_all_tables
from bot.models import Event


def test_roundtrip_with_optional_fields(session):
    ev = Event(date=date(2026, 10, 10), location="Park", author="Ana",
               name="BBQ", time=time(13, 0), prices=["Entry: R$ 40", "Drinks: R$ 10"], extra_info="Bring ice")
    session.add(ev)
    session.commit()
    session.expire_all()
    got = session.scalars(select(Event)).one()
    assert got.id == 1 and got.name == "BBQ" and got.time == time(13, 0)
    assert got.prices == ["Entry: R$ 40", "Drinks: R$ 10"] and got.author == "Ana"


def test_nullable_fields_default_to_none(session):
    session.add(Event(date=date(2026, 10, 10), location="Park", author="Ana"))
    session.commit()
    got = session.scalars(select(Event)).one()
    assert got.name is None and got.time is None and got.prices is None and got.extra_info is None


def test_timestamps_set_by_database(session):
    ev = Event(date=date(2026, 10, 10), location="Park", author="Ana")
    session.add(ev)
    session.commit()
    session.refresh(ev)
    assert ev.created_at is not None and ev.updated_at is not None
    ev.location = "Beach"
    session.commit()
    session.refresh(ev)
    assert ev.updated_at is not None


@pytest.mark.parametrize("kwargs", [
    {"date": None, "location": "Park", "author": "Ana"},
    {"date": date(2026, 10, 10), "location": None, "author": "Ana"},
    {"date": date(2026, 10, 10), "location": "Park", "author": None},
])
def test_required_columns_enforced(session, kwargs):
    session.add(Event(**kwargs))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_create_all_tables_is_idempotent(engine):
    create_all_tables(engine)
    create_all_tables(engine)


def test_data_visible_from_new_session(engine, session):
    session.add(Event(date=date(2026, 10, 10), location="Park", author="Ana"))
    session.commit()
    with Session(engine) as other:
        assert other.scalars(select(Event)).one().location == "Park"
