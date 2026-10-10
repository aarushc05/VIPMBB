"""Read-only SQLite migration, PostgreSQL parity and recoverable backups."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import date, datetime

import pytest
from psycopg import sql
from sqlite_snapshot import create_snapshot


def test_explicit_import_preserves_native_types_null_zero_and_audit(db, tmp_path):
    from server.app.import_sqlite import import_snapshot

    source = create_snapshot(tmp_path / "synthetic.sqlite3")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = import_snapshot(source)
    assert result["status"] == "imported"
    assert result["counts"]["stats"] == 2
    assert all(row["matched"] for row in result["verification"].values())
    assert result["transient_jobs_imported"] == 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not source.with_name(source.name + "-wal").exists()
    with db.database() as connection:
        session = connection.execute("SELECT * FROM sessions WHERE id=101").fetchone()
        stats = {
            row["player_id"]: row for row in connection.execute("SELECT * FROM stats")
        }
        review = connection.execute("SELECT * FROM reviews").fetchone()
        note = connection.execute("SELECT * FROM documents").fetchone()
        message = connection.execute("SELECT * FROM chat_messages").fetchone()
        assert (
            connection.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"]
            == 0
        )
    assert session["local_date"] == date(2026, 10, 6)
    assert isinstance(session["start_utc"], datetime) and session["start_utc"].tzinfo
    assert session["reviewed"] is True
    assert session["source_labels"] == ["Training"]
    assert stats[11]["metrics_json"]["jump_count"] == 0
    assert stats[22]["metrics_json"]["jump_count"] is None
    assert stats[22]["metrics_json"]["exposure_basis"] == "legacy_unknown"
    assert stats[22]["legacy"] is True
    assert review["actor_id"] is None
    assert review["provenance"] == "legacy-local-import"
    assert note["owner_id"] == message["owner_id"] == "local"
    assert import_snapshot(source)["status"] == "already-imported"
    assert db.status_data()["sessions"] == 1
    # Identity sequences must continue above retained source IDs.
    with db.database() as connection:
        new = connection.execute(
            "INSERT INTO reviews(session_id,classification,reason) VALUES(101,'game','New synthetic review') RETURNING id"
        ).fetchone()
    assert new["id"] > review["id"]


def test_import_refuses_nonempty_destination_without_overwrite(db, seed, tmp_path):
    from server.app.import_sqlite import import_snapshot

    source = create_snapshot(tmp_path / "synthetic.sqlite3")
    seed.player(77, "Existing Synthetic Player")
    with pytest.raises(ValueError, match="not empty"):
        import_snapshot(source)
    assert import_snapshot(source, if_empty=True)["status"] == "skipped-nonempty"
    with db.database() as connection:
        assert [row["id"] for row in connection.execute("SELECT id FROM players")] == [
            77
        ]


@pytest.mark.parametrize("source_state", ["missing", "corrupt", "wal", "obsolete"])
def test_if_empty_ignores_obsolete_source_after_postgres_cutover(
    db, seed, tmp_path, monkeypatch, source_state
):
    from server.app import import_sqlite

    seed.player(77, "Existing Synthetic Player")
    source = tmp_path / "old-rollback.sqlite3"
    if source_state == "corrupt":
        source.write_bytes(b"synthetic damaged rollback copy")
    elif source_state == "wal":
        create_snapshot(source)
        source.with_name(source.name + "-wal").write_bytes(b"synthetic nonempty WAL")
    elif source_state == "obsolete":
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE player_session_stats(player_id INTEGER)")
    before = {
        path.name: path.read_bytes() for path in tmp_path.glob("old-rollback.sqlite3*")
    }

    def source_must_not_be_read(_path):
        raise AssertionError(
            "Populated PostgreSQL startup must not read the old source"
        )

    monkeypatch.setattr(import_sqlite, "_file_hash", source_must_not_be_read)
    result = import_sqlite.import_snapshot(source, if_empty=True)
    assert result["status"] == "skipped-nonempty"
    assert {
        path.name: path.read_bytes() for path in tmp_path.glob("old-rollback.sqlite3*")
    } == before
    with db.database() as connection:
        assert [row["id"] for row in connection.execute("SELECT id FROM players")] == [
            77
        ]
        assert (
            connection.execute(
                "SELECT COUNT(*) AS count FROM import_manifests"
            ).fetchone()["count"]
            == 0
        )


def test_if_empty_still_imports_valid_source_when_postgres_empty(db, tmp_path):
    from server.app.import_sqlite import import_snapshot

    source = create_snapshot(tmp_path / "synthetic.sqlite3")
    assert import_snapshot(source, if_empty=True)["status"] == "imported"
    assert db.status_data()["sessions"] == 1


def test_fresh_import_applies_source_policy_and_preserves_all_other_values(
    db, tmp_path
):
    from server.app import import_sqlite

    source = create_snapshot(tmp_path / "old-activity-policy.sqlite3")
    with sqlite3.connect(source) as connection:
        connection.row_factory = sqlite3.Row
        base = dict(
            connection.execute("SELECT * FROM sessions WHERE id=101").fetchone()
        )
        connection.execute("DELETE FROM reviews")
        cases = [
            (101, "unknown", False, ["Training"]),
            (102, "unknown", False, ["Match"]),
            (103, "practice", False, ["Training", "Match"]),
            (104, "game", True, ["Training"]),
            (105, "unknown", False, ["Training"]),
            (106, "practice", True, ["Match"]),
            (107, "practice", False, []),
            (108, "unknown", False, ["SHOOTAROUND"]),
        ]
        for sid, classification, reviewed, labels in cases:
            row = {
                **base,
                "id": sid,
                "classification": classification,
                "reviewed": int(reviewed),
                "source_labels": json.dumps(labels),
            }
            columns = ",".join(row)
            placeholders = ",".join("?" for _ in row)
            connection.execute(
                f"INSERT OR REPLACE INTO sessions({columns}) VALUES({placeholders})",
                tuple(row.values()),
            )
        connection.execute(
            "INSERT INTO reviews VALUES(2,105,'game','Earlier synthetic decision','2026-10-06T20:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO reviews VALUES(3,105,'unknown','Latest synthetic decision remains unresolved','2026-10-06T20:00:00+00:00')"
        )
        # Label identity does not depend on a questionable recording duration.
        connection.execute(
            "UPDATE sessions SET end_utc='2026-10-07T08:30:00+00:00' WHERE id=101"
        )
    before = source.read_bytes()
    result = import_sqlite.import_snapshot(source)
    assert source.read_bytes() == before
    with db.database() as connection:
        rows = {r["id"]: r for r in connection.execute("SELECT * FROM sessions")}
    expected = {
        101: ("practice", False),
        102: ("game", False),
        103: ("unknown", False),
        104: ("game", True),
        105: ("unknown", True),
        106: ("practice", True),
        107: ("unknown", False),
        108: ("practice", False),
    }
    assert {
        sid: (row["classification"], row["reviewed"]) for sid, row in rows.items()
    } == expected
    with (
        sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original,
        db.database() as target,
    ):
        original.row_factory = sqlite3.Row
        for table, columns in import_sqlite.TABLES.items():
            order = import_sqlite.KEYS.get(table, ("id",))
            selected = ",".join(columns)
            ordering = ",".join(order)
            old = [
                import_sqlite._normalized(row)
                for row in original.execute(
                    f"SELECT {selected} FROM {table} ORDER BY {ordering}"
                )
            ]
            new = target.execute(
                sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, order)),
                )
            ).fetchall()
            if table == "sessions":
                for row in old + new:
                    row.pop("classification")
                    row.pop("reviewed")
            assert old == new, f"Non-derived values changed in {table}"
    audit = result["verification"]["sessions"]
    assert audit["derived_fields"] == ["classification", "reviewed"]
    assert audit["derived_policy"] == "0003_source_activity"
    assert audit["derived_rows_changed"] == 6
    assert audit["source_canonical_sha256"] != audit["canonical_sha256"]
    assert audit["matched"] is True
    repeated = import_sqlite.import_snapshot(source)
    assert repeated["status"] == "already-imported"
    assert repeated["verification"] == result["verification"]


def test_old_dashboard_schema_requires_authoritative_snapshot(db, tmp_path):
    from server.app.import_sqlite import import_snapshot

    source = tmp_path / "dashboard.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE player_session_stats(player_id INTEGER)")
    with pytest.raises(ValueError, match="authoritative"):
        import_snapshot(source)
    assert db.status_data()["sessions"] == 0


def test_snapshot_with_unknown_updated_at_preserves_unknown(db, tmp_path):
    from server.app.import_sqlite import import_snapshot

    source = create_snapshot(tmp_path / "unknown-timestamp.sqlite3")
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE players SET updated_at=NULL WHERE id=11")
        connection.execute("UPDATE sessions SET updated_at=NULL")
    import_snapshot(source)
    with db.database() as connection:
        assert (
            connection.execute("SELECT updated_at FROM players WHERE id=11").fetchone()[
                "updated_at"
            ]
            is None
        )
        assert (
            connection.execute(
                "SELECT updated_at FROM sessions WHERE id=101"
            ).fetchone()["updated_at"]
            is None
        )


def test_snapshot_wal_is_refused_without_modifying_source(db, tmp_path):
    from server.app.import_sqlite import import_snapshot

    source = create_snapshot(tmp_path / "uncheckpointed.sqlite3")
    before = source.read_bytes()
    wal = source.with_name(source.name + "-wal")
    wal.write_bytes(b"synthetic nonempty WAL")
    with pytest.raises(ValueError, match="nonempty WAL"):
        import_snapshot(source)
    assert source.read_bytes() == before
    assert wal.read_bytes() == b"synthetic nonempty WAL"
    assert db.status_data()["sessions"] == 0


def test_import_rolls_back_every_table_on_invalid_foreign_key(db, tmp_path):
    from server.app.import_sqlite import import_snapshot

    source = create_snapshot(tmp_path / "synthetic.sqlite3")
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE stats SET player_id=999 WHERE player_id=22")
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        import_snapshot(source)
    with db.database() as connection:
        for table in ("players", "sessions", "stats", "reports", "import_manifests"):
            assert (
                connection.execute(
                    sql.SQL("SELECT COUNT(*) AS count FROM {}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()["count"]
                == 0
            )


def test_backup_restores_consistent_complete_postgres_snapshot(db, practice):
    from server.app import db as postgres

    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        pytest.skip(
            "Native pg_dump/pg_restore are required; run the Docker test service for backup/restore verification."
        )
    original = db.get_report(practice)
    result = db.backup()
    path = db.data_dir() / "backups" / result["filename"]
    assert result["format"] == "postgresql-custom"
    assert path.suffix == ".dump" and path.read_bytes().startswith(b"PGDMP")
    assert path.stat().st_mode & 0o077 == 0
    schema = postgres.schema_name()
    assert schema.startswith("test_") and len(schema) == 37
    # Exact generated test schema only; no production/public data can be removed.
    with db.database() as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
        )
    environment = dict(os.environ)
    settings = postgres.connection_settings()
    if "conninfo" in settings:
        from psycopg.conninfo import conninfo_to_dict

        settings = conninfo_to_dict(settings["conninfo"])
    for key, value in settings.items():
        variable = {
            "dbname": "PGDATABASE",
            "user": "PGUSER",
            "password": "PGPASSWORD",
            "host": "PGHOST",
            "port": "PGPORT",
            "sslmode": "PGSSLMODE",
        }.get(key)
        if variable:
            environment[variable] = str(value)
    restore = subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "--dbname",
            environment["PGDATABASE"],
            str(path),
        ],
        env=environment,
        capture_output=True,
        timeout=60,
    )
    assert restore.returncode == 0, "Synthetic PostgreSQL backup could not be restored"
    db.initialize()
    restored = db.get_report(practice)
    assert restored == original
    with db.database() as connection:
        assert (
            connection.execute("SELECT COUNT(*) AS count FROM stats").fetchone()[
                "count"
            ]
            == 2
        )
        rows = {
            r["player_id"]: r["metrics_json"]
            for r in connection.execute("SELECT player_id,metrics_json FROM stats")
        }
    assert rows[11]["jump_count"] == 0 and rows[22]["jump_count"] is None


def test_failed_dump_removes_partial_output_and_hides_credentials(db, monkeypatch):
    def failed(*args, **kwargs):
        kwargs["stdout"].write(b"partial archive")
        return subprocess.CompletedProcess(
            args[0], 1, stderr=b"synthetic-password-must-not-leak"
        )

    monkeypatch.setattr(db.subprocess, "run", failed)
    with pytest.raises(RuntimeError) as error:
        db.backup()
    assert "synthetic-password" not in str(error.value)
    assert list((db.data_dir() / "backups").glob("*.dump")) == []
