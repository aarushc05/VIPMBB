import json
import math
import sqlite3
from datetime import date

import pytest


@pytest.mark.parametrize("value", [None, True, False, -1, float("nan"), float("inf"), "bad"])
def test_nonmeasurements_are_not_numbers(db, value):
    assert db.finite(value) is None


def test_known_zero_is_a_measurement(db):
    assert db.finite(0) == 0
    assert db.finite("0") == 0


@pytest.mark.parametrize("utc,local", [
    ("2026-03-05T00:30:00Z", "2026-03-04"),
    ("2026-07-02T03:30:00Z", "2026-07-01"),
    ("2026-11-01T05:30:00Z", "2026-11-01"),
    ("2026-11-01T06:30:00Z", "2026-11-01"),
])
def test_atlanta_calendar_dates_respect_dst(db, utc, local):
    assert db.timestamp(utc).astimezone(db.ATLANTA).date().isoformat() == local


def test_home_game_evening_training_is_not_auto_practice(db):
    assert db.source_classification(["Training"], "2026-03-04T22:28:00Z", "2026-03-05T02:10:00Z") == "unknown"


@pytest.mark.parametrize("labels,start,end", [
    (["Training", "Match"], "2026-03-03T18:00:00Z", "2026-03-03T20:00:00Z"),
    (["Match"], "2026-03-03T18:00:00Z", "2026-03-03T20:00:00Z"),
    (["Training"], "2026-03-03T18:00:00Z", "2026-03-04T20:00:00Z"),
    (["Training"], "2026-03-03T18:00:00Z", None),
    (["Training"], "2026-03-03T18:00:00Z", "2026-03-03T17:00:00Z"),
])
def test_ambiguous_or_invalid_recording_abstains(db, labels, start, end):
    assert db.source_classification(labels, start, end) == "unknown"


def test_training_label_can_suggest_but_not_verify_practice(db):
    assert db.source_classification(["Training"], "2026-03-03T18:00:00Z", "2026-03-03T20:00:00Z") == "practice"


def test_calendar_filter_is_inclusive_and_empty_range_is_empty(db, seed):
    for sid, day in [(1, "2026-03-01"), (2, "2026-03-02"), (3, "2026-03-03")]:
        seed.session(sid, day)
    assert {s["id"] for s in db.list_sessions(start="2026-03-01", end="2026-03-02")["sessions"]} == {1, 2}
    assert db.list_sessions(start="2026-09-01", end="2026-09-30")["sessions"] == []
    assert db.list_sessions(start="2026-09-01", end="2026-09-30")["total"] == 0


def test_search_is_parameterized_and_wildcards_are_literal(db, seed):
    seed.session(1, title="100% effort")
    seed.session(2, title="Other session")
    assert db.list_sessions(q="%")['total'] == 1
    assert db.list_sessions(q="' OR 1=1 --")['total'] == 0
    assert db.status_data()["sessions"] == 2


def test_enqueued_identical_job_is_deduplicated(db):
    first = db.enqueue_job("sync", {"start": "2026-10-01", "end": "2026-10-06"})
    second = db.enqueue_job("sync", {"end": "2026-10-06", "start": "2026-10-01"})
    assert first["id"] == second["id"]
    assert len(db.list_jobs()["jobs"]) == 1


def test_job_payload_cannot_inject_arbitrary_job_kind(db):
    with pytest.raises(ValueError):
        db.enqueue_job("shell", {"command": "rm -rf /"})


def test_backup_has_complete_consistent_rows(db, practice):
    result = db.backup()
    path = db.data_dir() / "backups" / result["filename"]
    assert path.is_file()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM stats").fetchone()[0] == 2


def test_legacy_import_rejects_ambiguous_zero_and_preserves_date(isolated_runtime, monkeypatch):
    from local_app import data
    fake_root = isolated_runtime / "old-project"
    cache = fake_root / "Dashboard" / "public" / "data" / "kinexon.local.sqlite3"
    cache.parent.mkdir(parents=True)
    with sqlite3.connect(cache) as old:
        old.executescript("""
            CREATE TABLE players(player_id INTEGER,team_id INTEGER,first_name TEXT,last_name TEXT,number TEXT,deleted INTEGER,updated_at TEXT);
            CREATE TABLE sessions(session_id INTEGER,team_id INTEGER,start_session TEXT,end_session TEXT,type_label TEXT,category TEXT,updated_at TEXT);
            CREATE TABLE player_session_stats(player_id INTEGER,session_id INTEGER,minutes REAL,distance_m REAL,mechanical_load REAL,accel_load REAL,jump_count REAL,updated_at TEXT);
            INSERT INTO players VALUES(11,3,'Alex','Rivera','11',0,'old');
            INSERT INTO sessions VALUES(101,3,'2026-03-05T00:30:00Z','2026-03-05T02:10:00Z','Training','practice','old');
            INSERT INTO player_session_stats VALUES(11,101,60,3000,1200,0,0,'old');
        """)
    monkeypatch.setattr(data, "ROOT", fake_root)
    monkeypatch.setenv("VIPMBB_IMPORT_LEGACY", "1")
    data.initialize()
    with data.database() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE id=101").fetchone()
        row = conn.execute("SELECT * FROM stats WHERE player_id=11").fetchone()
    metrics = json.loads(row["metrics_json"])
    assert metrics["jump_count"] is None
    assert metrics["accel_load"] is None
    assert metrics["mechanical_load"] == 1200
    assert metrics["exposure_basis"] == "legacy_unknown"
    assert session["local_date"] == "2026-03-04"
    assert session["classification"] == "unknown"
    assert session["reviewed"] == 0
    data.initialize()
    assert data.status_data()["sessions"] == 1


@pytest.mark.parametrize("source,expected", [
    ("{'id': 3, 'label': 'Training'}", ["Training"]),
    ("{'label': 'Training'}, {'label': 'Match'}", ["Training", "Match"]),
    ([{"label": "Drill"}, {"label": "Training"}], ["Drill", "Training"]),
    ("[{'label': 'Training'}, {'label': 'Training'}]", ["Training"]),
])
def test_legacy_dictionary_label_shapes_are_normalized(db, source, expected):
    assert db.labels(source) == expected


def test_label_repair_preserves_reviewed_classification(db, seed, monkeypatch, isolated_runtime):
    monkeypatch.setattr(db, "ROOT", isolated_runtime / "unrelated-empty-root")
    seed.session(101, classification="game", reviewed=True,
                 labels=["{'id': 3, 'label': 'Training'}"])
    seed.session(102, classification="unknown", reviewed=False,
                 labels=["{'id': 3, 'label': 'Training'}"])
    with db.database() as conn:
        conn.execute("UPDATE sessions SET source_json=?", (json.dumps({"import": "legacy"}),))
        conn.execute("DELETE FROM meta WHERE key='legacy_labels_v2'")
    db.initialize()
    with db.database() as conn:
        rows = {row["id"]: row for row in conn.execute("SELECT * FROM sessions")}
    assert json.loads(rows[101]["source_labels"]) == ["Training"]
    assert rows[101]["classification"] == "game"
    assert rows[101]["reviewed"] == 1
    assert rows[102]["classification"] == "practice"
    assert rows[102]["reviewed"] == 0
