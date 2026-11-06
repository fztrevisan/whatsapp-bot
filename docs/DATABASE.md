# Database and migrations

Events are stored in a relational database through SQLAlchemy. The database is chosen with `DATABASE_URL` in `.env`.

| Database | `DATABASE_URL` |
| --- | --- |
| SQLite (default) | `sqlite:///./events.db` |
| PostgreSQL | `postgresql+psycopg://user:password@localhost:5432/whatsapp_bot` (run `uv add "psycopg[binary]"` first) |

## First run / updates

```bash
uv sync
uv run alembic upgrade head      # creates or updates the tables
```

Run this before starting the bot and again after pulling new migrations. Do not rely on `create_all_tables()` for a real database: it exists for tests, and running it before Alembic makes the first migration fail with "table already exists".

## Changing the schema

1. Edit `bot/models.py`.
2. `uv run alembic revision --autogenerate -m "describe the change"`
3. Review the generated file in `migrations/versions/`.
4. `uv run alembic upgrade head` (undo with `uv run alembic downgrade -1`).

## Table `events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | primary key, autoincrement |
| `name` | string, null | from the LLM |
| `date` | date | required, indexed |
| `time` | time, null | HH:MM |
| `location` | string | required |
| `author` | string | required, who sent the command |
| `prices` | list of strings, null | JSON on SQLite, `ARRAY` on PostgreSQL |
| `extra_info` | string, null | from the LLM |
| `created_at` / `updated_at` | timestamp | set by the database (UTC on SQLite) |

`list-events` shows only events with `date >= today` (today in `EVENT_TIMEZONE`). Past events stay in the table; `/health` reports the total row count.
