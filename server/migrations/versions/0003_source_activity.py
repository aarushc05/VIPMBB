"""Separate source activity labels from manual confirmation and timing quality."""

from alembic import op

revision = "0003_source_activity"
down_revision = "0002_unknown_timestamps"
branch_labels = None
depends_on = None


def upgrade():
    # Restore the latest explicit decision, including a deliberately unresolved one.
    # Earlier versions incorrectly treated an explicit 'unknown' decision as unreviewed.
    op.execute("""
        UPDATE sessions s SET classification=r.classification, reviewed=TRUE
        FROM (SELECT DISTINCT ON (session_id) session_id,classification
              FROM reviews ORDER BY session_id,id DESC) r
        WHERE s.id=r.session_id
    """)
    # Keep this data migration self-contained; future policy edits must not rewrite history.
    op.execute("""
        WITH evidence AS (
          SELECT s.id,
            EXISTS (SELECT 1 FROM jsonb_array_elements_text(s.source_labels) label
                    WHERE lower(label) IN ('practice','training','shootaround')) AS practice,
            EXISTS (SELECT 1 FROM jsonb_array_elements_text(s.source_labels) label
                    WHERE lower(label) IN ('game','match')) AS game
          FROM sessions s WHERE NOT s.reviewed
        )
        UPDATE sessions s SET classification=CASE
          WHEN e.practice AND NOT e.game THEN 'practice'
          WHEN e.game AND NOT e.practice THEN 'game' ELSE 'unknown' END
        FROM evidence e WHERE s.id=e.id
    """)


def downgrade():
    raise RuntimeError(
        "Activity policy changes retain audit history; restore an approved backup to roll back."
    )
