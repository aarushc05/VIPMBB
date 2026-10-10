"""Native PostgreSQL connections. Never logs credentials or connection strings."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def schema_name():
    value = os.environ.get("VIPMBB_DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise ValueError("VIPMBB_DB_SCHEMA must be a lowercase PostgreSQL identifier.")
    return value


def connection_settings():
    if os.environ.get("DATABASE_URL"):
        return {"conninfo": os.environ["DATABASE_URL"]}
    settings = {
        key: os.environ[name]
        for key, name in (
            ("host", "PGHOST"),
            ("port", "PGPORT"),
            ("dbname", "PGDATABASE"),
            ("user", "PGUSER"),
            ("password", "PGPASSWORD"),
            ("sslmode", "PGSSLMODE"),
        )
        if os.environ.get(name)
    }
    if os.environ.get("PGPASSWORD_FILE"):
        settings["password"] = (
            Path(os.environ["PGPASSWORD_FILE"]).read_text().rstrip("\r\n")
        )
    if not settings.get("dbname"):
        raise RuntimeError(
            "PostgreSQL is not configured. Start Docker or set PGDATABASE and connection settings."
        )
    return settings


def connect(*, autocommit=False):
    return psycopg.connect(
        **connection_settings(),
        row_factory=dict_row,
        autocommit=autocommit,
        connect_timeout=10,
        options=f"-c search_path={schema_name()},public -c timezone=UTC",
    )


@contextmanager
def database():
    with connect() as connection:
        yield connection


def lock_key(name):
    return int.from_bytes(
        hashlib.sha256((schema_name() + ":" + name).encode()).digest()[:8],
        "big",
        signed=True,
    )


@contextmanager
def advisory_lock(name, wait=False):
    # Dedicated session holds the lock without a transaction during network I/O.
    with connect(autocommit=True) as connection:
        key = lock_key(name)
        if wait:
            connection.execute("SELECT pg_advisory_lock(%s)", (key,))
            acquired = True
        else:
            acquired = connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (key,)
            ).fetchone()["acquired"]
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute("SELECT pg_advisory_unlock(%s)", (key,))


def jsonb(value):
    return Jsonb(value)
