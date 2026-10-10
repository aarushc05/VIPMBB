"""Preserve unknown legacy transport timestamps as NULL, never invented dates."""
from alembic import op

revision = '0002_unknown_timestamps'
down_revision = '0001_postgres'
branch_labels = None
depends_on = None

def upgrade():
    for table in ('players','sessions','stats','phase_stats','documents'):
        op.execute(f'ALTER TABLE {table} ALTER COLUMN updated_at DROP NOT NULL')

def downgrade():
    raise RuntimeError('Unknown timestamps cannot be filled with invented dates.')
