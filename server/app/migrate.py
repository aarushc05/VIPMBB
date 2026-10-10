"""Explicit, versioned schema migrations: python -m server.app.migrate upgrade."""

import argparse
from pathlib import Path
from alembic import command
from alembic.config import Config
from . import db

HEAD = "0002_unknown_timestamps"


def upgrade():
    path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(path))
    with db.advisory_lock("schema-migrations", wait=True):
        command.upgrade(config, "head")


def main():
    parser = argparse.ArgumentParser(
        description="Apply versioned PostgreSQL schema migrations."
    )
    parser.add_argument("command", nargs="?", default="upgrade", choices=["upgrade"])
    parser.parse_args()
    try:
        upgrade()
    except Exception:
        parser.exit(
            1,
            "Database migration failed. Verify PostgreSQL configuration, pgvector availability and permissions. No credentials were logged.\n",
        )
    print("PostgreSQL schema is current.")


if __name__ == "__main__":
    main()
