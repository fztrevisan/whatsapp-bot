"""create events table

Revision ID: 0001
Revises:
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("location", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("time", sa.Time(), nullable=True),
        sa.Column(
            "prices",
            sa.JSON().with_variant(postgresql.ARRAY(sa.String()), "postgresql"),
            nullable=True,
        ),
        sa.Column("extra_info", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_date"), "events", ["date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_events_date"), table_name="events")
    op.drop_table("events")
