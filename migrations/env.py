"""Alembic environment. The database URL comes from DATABASE_URL via bot.config (.env)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from bot.config import Settings
from bot.models import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# '%' must be doubled because Alembic stores the URL in a ConfigParser.
config.set_main_option("sqlalchemy.url", Settings.from_env().database_url.replace("%", "%%"))
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite can't ALTER most things; batch mode recreates the table
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
