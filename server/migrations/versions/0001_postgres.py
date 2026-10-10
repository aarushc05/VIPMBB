"""Authoritative PostgreSQL schema, leased jobs and vector-capable knowledge."""
from pathlib import Path
from alembic import op

revision = '0001_postgres'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    source = Path(__file__).with_name('0001_schema.sql').read_text()
    for statement in source.split(';'):
        if statement.strip():
            op.execute(statement)

def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally disabled. Restore a verified backup to a separate database.')
