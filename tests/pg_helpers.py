"""Strictly isolated PostgreSQL test namespaces; never use an application DB."""

from __future__ import annotations

from contextlib import contextmanager
import os
import re
import uuid


def test_database_url():
    from psycopg.conninfo import conninfo_to_dict

    value = os.environ.get("VIPMBB_TEST_DATABASE_URL", "")
    if not value:
        raise RuntimeError(
            "Set VIPMBB_TEST_DATABASE_URL for the dedicated vipmbb_test database; application DATABASE_URL is never used by tests."
        )
    try:
        config = conninfo_to_dict(value)
    except Exception:
        raise RuntimeError(
            "The dedicated test database configuration is invalid; connection details are not displayed."
        ) from None
    if config.get("dbname") != "vipmbb_test":
        raise RuntimeError(
            "Refusing to test against any database other than vipmbb_test."
        )
    return value


@contextmanager
def isolated_schema():
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    url = test_database_url()
    schema = "test_" + uuid.uuid4().hex
    if not re.fullmatch(r"test_[a-f0-9]{32}", schema):
        raise RuntimeError("Unsafe test schema identifier")
    try:
        admin = psycopg.connect(
            url, autocommit=True, row_factory=dict_row, connect_timeout=5
        )
    except Exception:
        raise RuntimeError(
            "Cannot connect to the dedicated test database. Start the PostgreSQL test service; connection details are not displayed."
        ) from None
    with admin:
        actual = admin.execute("SELECT current_database() AS database").fetchone()[
            "database"
        ]
        if actual != "vipmbb_test":
            raise RuntimeError(
                "Connected database is not vipmbb_test; no schema was created."
            )
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield url, schema
        finally:
            # The generated exact namespace is the only destructive target.
            # No public schema or shared table is ever truncated or dropped.
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def json_parameter(value):
    from psycopg.types.json import Jsonb

    return Jsonb(value)
