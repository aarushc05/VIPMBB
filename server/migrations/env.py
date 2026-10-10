"""Alembic runs with caller-provided native PostgreSQL connection settings."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from psycopg.rows import tuple_row
from server.app import db

config = context.config


def run_migrations_online():
    def migration_connection():
        connection = db.connect()
        # SQLAlchemy's dialect bootstrap consumes positional DB-API tuples.
        connection.row_factory = tuple_row
        return connection

    engine = create_engine(
        "postgresql+psycopg://", creator=migration_connection, poolclass=NullPool
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table_schema=db.schema_name(),
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError(
        "Offline migrations are not supported; use the explicit upgrade command."
    )
run_migrations_online()
