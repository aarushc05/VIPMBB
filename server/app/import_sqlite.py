"""Explicit, read-only authoritative-cache import into an empty PostgreSQL schema.

The source is never checkpointed, changed, renamed or deleted. Stop the old app
and supply a checkpointed snapshot (no nonempty WAL). All destination writes and
parity checks occur in a single transaction. Runtime SQLite access does not exist.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from psycopg import sql
from . import data

TABLES = {
    "players": ("id", "name", "number", "active", "updated_at"),
    "sessions": (
        "id",
        "title",
        "start_utc",
        "end_utc",
        "local_date",
        "source_labels",
        "classification",
        "reviewed",
        "status",
        "expected_players",
        "source_hash",
        "updated_at",
        "source_json",
        "assignment_complete",
        "sync_complete",
        "removed_upstream",
    ),
    "stats": (
        "session_id",
        "player_id",
        "metrics_json",
        "missing_json",
        "legacy",
        "updated_at",
        "raw_json",
    ),
    "assignments": ("session_id", "player_id", "source_json"),
    "phases": (
        "id",
        "session_id",
        "title",
        "start_utc",
        "end_utc",
        "source_labels",
        "source_json",
        "valid",
    ),
    "phase_stats": (
        "phase_id",
        "player_id",
        "metrics_json",
        "missing_json",
        "raw_json",
        "updated_at",
    ),
    "reviews": ("id", "session_id", "classification", "reason", "created_at"),
    "reports": (
        "id",
        "session_id",
        "version",
        "source_hash",
        "payload_json",
        "generated_at",
    ),
    "documents": (
        "id",
        "title",
        "body",
        "kind",
        "updated_at",
        "content_hash",
        "embedding_json",
        "embedding_model",
    ),
    "chat_messages": (
        "id",
        "conversation_id",
        "role",
        "content",
        "response_json",
        "created_at",
    ),
}
KEYS = {
    "stats": ("session_id", "player_id"),
    "assignments": ("session_id", "player_id"),
    "phase_stats": ("phase_id", "player_id"),
}
BOOLS = {
    "active",
    "reviewed",
    "assignment_complete",
    "sync_complete",
    "removed_upstream",
    "legacy",
    "valid",
}
JSONS = {
    "source_labels",
    "source_json",
    "metrics_json",
    "missing_json",
    "raw_json",
    "payload_json",
    "embedding_json",
    "response_json",
}
TIMES = {"updated_at", "start_utc", "end_utc", "created_at", "generated_at"}
IDENTITIES = ("reviews", "reports", "documents", "chat_messages")


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalized(row):
    result = {}
    for key, value in dict(row).items():
        if key in JSONS and value is not None:
            value = data.json_value(value)
        elif key in BOOLS:
            if value not in (0, 1, False, True):
                raise ValueError("Source contains a non-boolean flag.")
            value = bool(value)
        elif key in TIMES:
            if value in ("", None):
                value = None
            else:
                value = data.timestamp(value)
                if value is None:
                    raise ValueError("Source contains an invalid timestamp.")
        elif key == "local_date" and isinstance(value, str):
            value = date.fromisoformat(value)
        result[key] = value
    # Validate finite JSON numbers now rather than silently normalize them away.
    data.canonical(result)
    return result


def _digest_rows(rows):
    digest = hashlib.sha256()
    for row in rows:
        digest.update(data.canonical(row).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _latest_review_classifications(source):
    available = {row[1] for row in source.execute('PRAGMA table_info("reviews")')}
    if not set(TABLES["reviews"]) <= available:
        raise ValueError("Source table reviews is missing required columns.")
    # Match migration 0003: insertion identity, not possibly missing or equal
    # timestamps, establishes the latest explicit staff decision.
    return {
        row["session_id"]: row["classification"]
        for row in source.execute(
            "SELECT session_id,classification FROM reviews ORDER BY id"
        )
    }


def _activity_identity(row, latest_reviews):
    """Apply derived activity policy without changing measurements or provenance."""
    row = dict(row)
    if row["id"] in latest_reviews:
        row["classification"] = latest_reviews[row["id"]]
        row["reviewed"] = True  # An explicit unknown decision is still a review.
    elif not row["reviewed"]:
        row["classification"] = data.source_classification(
            row["source_labels"], row["start_utc"], row["end_utc"]
        )
    return row


def _target_populated(target):
    return any(
        target.execute(
            sql.SQL("SELECT EXISTS(SELECT 1 FROM {}) AS occupied").format(
                sql.Identifier(table)
            )
        ).fetchone()["occupied"]
        for table in (*TABLES, "jobs", "sync_runs", "import_manifests")
    )


def _skipped_nonempty():
    return {
        "status": "skipped-nonempty",
        "reason": "Existing PostgreSQL data is authoritative; no rows were overwritten.",
    }


def import_snapshot(path, *, legacy_owner="local", if_empty=False):
    data.initialize()
    if if_empty:
        # Automatic startup import must not depend on an obsolete rollback file
        # remaining present, valid, or checkpointed after PostgreSQL takes over.
        # This read-only fast path shares the import lock. An empty result is not
        # permission to write: we recheck under table locks after source validation.
        with data.advisory_lock("sqlite-import", wait=True), data.database() as target:
            if _target_populated(target):
                return _skipped_nonempty()
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Import source must be a SQLite snapshot file.")
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError(
            "Source has a nonempty WAL. Stop the old application and supply a safely checkpointed snapshot; this importer never changes the source."
        )
    fingerprint = _file_hash(path)
    # immutable avoids creation of SQLite SHM/WAL sidecars in the readonly mount.
    source = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    source.row_factory = sqlite3.Row
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Source SQLite integrity check failed.")
        if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Source SQLite has invalid foreign keys.")
        tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = set(TABLES) - {"documents", "chat_messages"}
        if not required <= tables:
            raise ValueError(
                "Expected the authoritative server.app practice.sqlite3 schema, not the old Dashboard cache."
            )
        with data.advisory_lock("sqlite-import", wait=True), data.database() as target:
            names = sql.SQL(",").join(
                sql.Identifier(table)
                for table in (*TABLES, "jobs", "sync_runs", "meta", "import_manifests")
            )
            target.execute(
                sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(names)
            )
            previous = target.execute(
                "SELECT counts_json,verification_json FROM import_manifests WHERE fingerprint=%s",
                (fingerprint,),
            ).fetchone()
            if previous:
                return {
                    "status": "already-imported",
                    "fingerprint": fingerprint,
                    "counts": previous["counts_json"],
                    "verification": previous["verification_json"],
                }
            if _target_populated(target):
                if if_empty:
                    return _skipped_nonempty()
                raise ValueError(
                    "Target is not empty. Import refused without changing existing PostgreSQL data."
                )
            counts, verification = {}, {}
            latest_reviews = _latest_review_classifications(source)
            for table, columns in TABLES.items():
                if table not in tables:
                    counts[table] = 0
                    continue
                available = {
                    row[1] for row in source.execute(f'PRAGMA table_info("{table}")')
                }
                if not set(columns) <= available:
                    raise ValueError(
                        f"Source table {table} is missing required columns."
                    )
                order = KEYS.get(table, ("id",))
                quoted = ",".join('"' + column + '"' for column in columns)
                ordering = ",".join('"' + column + '"' for column in order)
                rows = [
                    _normalized(row)
                    for row in source.execute(
                        f'SELECT {quoted} FROM "{table}" ORDER BY {ordering}'
                    )
                ]
                source_hash = _digest_rows(rows)
                derived_changes = 0
                if table == "sessions":
                    normalized = [
                        _activity_identity(row, latest_reviews) for row in rows
                    ]
                    derived_changes = sum(
                        before != after for before, after in zip(rows, normalized)
                    )
                    rows = normalized
                for row in rows:
                    stored = dict(row)
                    if table == "reviews":
                        stored.update(actor_id=None, provenance="legacy-local-import")
                    elif table == "documents":
                        stored["owner_id"] = (
                            legacy_owner if row["kind"] == "coach_note" else None
                        )
                    elif table == "chat_messages":
                        stored["owner_id"] = legacy_owner
                    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(sql.Identifier(key) for key in stored),
                        sql.SQL(",").join(sql.Placeholder() for _ in stored),
                    )
                    target.execute(
                        insert,
                        tuple(
                            data.jsonb(value)
                            if key in JSONS and value is not None
                            else value
                            for key, value in stored.items()
                        ),
                    )
                selected = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                    sql.SQL(",").join(sql.Identifier(column) for column in columns),
                    sql.Identifier(table),
                    sql.SQL(",").join(sql.Identifier(column) for column in order),
                )
                imported = target.execute(selected).fetchall()
                expected_hash = _digest_rows(rows)
                actual_hash = _digest_rows(imported)
                if len(rows) != len(imported) or expected_hash != actual_hash:
                    raise ValueError(
                        f"Migration parity failed for {table}; destination changes were rolled back."
                    )
                counts[table] = len(rows)
                verification[table] = {
                    "count": len(rows),
                    "canonical_sha256": expected_hash,
                    "matched": True,
                }
                if table == "sessions":
                    # Exact parity still applies to every other session field and
                    # every measurement/audit table. Make this intentional derived
                    # identity conversion visible rather than claiming raw parity.
                    verification[table].update(
                        source_canonical_sha256=source_hash,
                        derived_fields=["classification", "reviewed"],
                        derived_policy="0003_source_activity",
                        derived_rows_changed=derived_changes,
                    )
            # Rebuild vector values only from valid, already-tagged source embeddings.
            # Malformed/zero vectors remain preserved in embedding_json for audit,
            # but are absent from vector retrieval until a successful reindex.
            from .knowledge import vector_literal

            for row in target.execute(
                "SELECT id,embedding_json,embedding_model FROM documents WHERE embedding_json IS NOT NULL"
            ).fetchall():
                try:
                    literal = vector_literal(row["embedding_json"])
                except ValueError:
                    continue
                if row["embedding_model"]:
                    target.execute(
                        "UPDATE documents SET embedding=%s::public.vector,embedding_dimensions=%s WHERE id=%s",
                        (literal, len(row["embedding_json"]), row["id"]),
                    )
            for table in IDENTITIES:
                value = target.execute(
                    sql.SQL("SELECT MAX(id) AS highest FROM {}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()["highest"]
                # RESTART is transactional, unlike setval(). A failed import must
                # not leave even sequence state changed in the empty target.
                target.execute(
                    sql.SQL("ALTER TABLE {} ALTER COLUMN id RESTART WITH {}").format(
                        sql.Identifier(table), sql.Literal(max((value or 0) + 1, 1))
                    )
                )
            if "meta" in tables:
                for row in source.execute(
                    "SELECT key,value FROM meta WHERE key IN ('source','last_sync')"
                ):
                    target.execute(
                        "INSERT INTO meta(key,value) VALUES(%s,%s)", tuple(row)
                    )
            target.execute(
                "INSERT INTO meta(key,value) VALUES('sync_status','imported-awaiting-central-sync')"
            )
            # The predecessor extractor was fixed to this source/team. Never
            # silently merge configurable future sources into the same IDs.
            target.execute(
                "INSERT INTO meta(key,value) VALUES('source_identity',%s)",
                (
                    data.digest(
                        {
                            "base_url": "https://georgia-tech-mccamish.access.kinexon.com",
                            "team_id": 3,
                        }
                    ),
                ),
            )
            if _file_hash(path) != fingerprint or (wal.exists() and wal.stat().st_size):
                raise ValueError(
                    "Source changed during import; destination changes were rolled back."
                )
            target.execute(
                "INSERT INTO import_manifests(fingerprint,source_name,counts_json,verification_json) VALUES(%s,%s,%s,%s)",
                (fingerprint, path.name, data.jsonb(counts), data.jsonb(verification)),
            )
            return {
                "status": "imported",
                "fingerprint": fingerprint,
                "counts": counts,
                "verification": verification,
                "transient_jobs_imported": 0,
            }
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import a checkpointed server.app SQLite snapshot without changing it."
    )
    parser.add_argument("source")
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Safely skip when PostgreSQL already contains data.",
    )
    args = parser.parse_args()
    try:
        result = import_snapshot(args.source, if_empty=args.if_empty)
    except (ValueError, FileNotFoundError) as error:
        parser.exit(1, str(error) + "\n")
    except Exception:
        parser.exit(
            1,
            "Import failed and destination transaction was rolled back. Check schema, source and database configuration. No credentials or athlete values were logged.\n",
        )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "counts", "reason")
                if key in result
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
