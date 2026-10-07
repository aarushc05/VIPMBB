"""Offline regression tests for the private SQLite sync (no live API access).

Run from the repository root: .venv/bin/python -m unittest discover -s Test -p test_sync_sqlite.py -v
All names, ids, metrics, credentials, and dates below are synthetic fixtures.
"""

from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import extract_dashboard_data as extractor
import sync_kinexon_sqlite as sync


class FakeClient:
    def __init__(self):
        self.calls = []
        self.failures = set()
        self.responses = {
            "/public/v1/teams/3/players": [
                {"id": 101, "first_name": "Fixture", "last_name": "Active", "deleted": False},
                {"id": 102, "first_name": "Fixture", "last_name": "Former", "deleted": True},
            ],
            "/public/v1/teams/3/sessions-and-phases": [
                {"session_id": 201, "start_session": "2026-03-09 00:30:00", "types": [{"label": "Match"}]},
                {"session_id": 202, "start_session": "2026-03-08 16:30:00", "types": [{"label": "Training"}]},
            ],
            "/public/v1/sensor-assignment/201": [
                {"player": {"id": 101}},
                {"player": {"id": 102}},
                {"player": {"id": 103, "first_name": "Fixture", "last_name": "Assigned"}},
                {"player": {"id": 103}},
            ],
            "/public/v1/sensor-assignment/202": [{"player": {"id": 101}}],
        }
        for player_id in (101, 102, 103):
            self.responses[f"/public/v1/statistics/player/{player_id}/sessions"] = [{
                "session_id": 201,
                **{field: 0 for field in extractor.FIELDS},
                "duration": 3600,
                "time_on_playing_field": 0,
                "distance_total": 1000 + player_id,
                "speed_max": 7.25,
                "data_quality": 98.5,
            }]

    def get(self, path, params=None):
        self.calls.append((path, copy.deepcopy(params)))
        if path in self.failures:
            raise RuntimeError("synthetic test failure")
        if path not in self.responses:
            raise AssertionError(f"Unexpected API call: {path}")
        return copy.deepcopy(self.responses[path])


class SQLiteSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vipmbb-sync-test-")
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "fixture.sqlite3"
        self.client = FakeClient()

    def run_sync(self, *args):
        argv = ["sync_kinexon_sqlite.py", "--database", str(self.database),
                "--end", "2026-03-08", "--initial-lookback-days", "10", *args]
        output = io.StringIO()
        with patch.object(sync, "Client", return_value=self.client), \
             patch("sys.argv", argv), \
             patch.dict(os.environ, {"KINEXON_USER": "test-user", "KINEXON_PASSWORD": "test-password", "KINEXON_API_KEY": "test-key"}), \
             redirect_stdout(output), redirect_stderr(output):
            result = sync.main()
        self.last_output = output.getvalue()
        return result

    def query(self, statement, parameters=()):
        with sqlite3.connect(self.database) as connection:
            return connection.execute(statement, parameters).fetchall()

    def test_fetches_active_deleted_and_assignment_only_players(self):
        self.assertEqual(self.run_sync(), 0)
        statistic_calls = {path for path, _ in self.client.calls if "/statistics/player/" in path}
        self.assertEqual(statistic_calls, {
            f"/public/v1/statistics/player/{player_id}/sessions" for player_id in (101, 102, 103)
        })
        self.assertEqual(self.query("SELECT player_id, deleted FROM players ORDER BY player_id"), [(101, 0), (102, 1), (103, 1)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM session_assignments"), [(4,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM assignment_sync"), [(2,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM player_session_stats"), [(3,)])

    def test_atlanta_day_and_dst_api_boundaries(self):
        self.assertEqual(self.run_sync("--start", "2026-03-08"), 0)
        self.assertEqual(self.query("SELECT session_date FROM sessions WHERE session_id=201"), [("2026-03-08",)])
        self.assertEqual(self.query("SELECT DISTINCT session_date FROM player_session_stats"), [("2026-03-08",)])
        bounded_calls = [params for _, params in self.client.calls if params and "min" in params]
        for params in bounded_calls:
            self.assertEqual(params["min"], "2026-03-08 05:00:00")
            self.assertEqual(params["max"], "2026-03-09 04:00:00")

    def test_zero_minutes_is_not_replaced_by_session_duration(self):
        self.assertEqual(sync.stats_values({"time_on_playing_field": 0, "duration": 3600})[0], 0)
        self.assertEqual(sync.stats_values({"time_on_playing_field": None, "duration": 3600})[0], 60)
        metrics = extractor.blank_metrics()
        extractor.add_record(metrics, {"time_on_playing_field": 0, "duration": 3600})
        self.assertEqual(metrics["minutes"], 0)
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.query("SELECT DISTINCT minutes FROM player_session_stats"), [(0.0,)])

    def test_missing_fields_are_distinct_from_recorded_zero(self):
        record = self.client.responses["/public/v1/statistics/player/101/sessions"][0]
        del record["distance_total"]
        record["speed_max"] = None
        self.assertEqual(self.run_sync(), 0)
        missing, quality = self.query("SELECT missing_fields, data_quality FROM player_session_stats WHERE player_id=101")[0]
        self.assertEqual(set(json.loads(missing)), {"distance_total", "speed_max"})
        self.assertNotIn("time_on_playing_field", json.loads(missing))
        self.assertEqual(quality, 98.5)

    def test_rerun_is_idempotent_and_successful_empty_response_removes_old_rows(self):
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.query("SELECT COUNT(*), SUM(distance_m) FROM player_session_stats"), [(3, 3306.0)])
        self.assertTrue(Path(str(self.database) + ".bak").is_file())
        self.client.responses["/public/v1/statistics/player/101/sessions"] = []
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM player_session_stats WHERE player_id=101"), [(0,)])

    def test_failed_player_request_keeps_checkpoint_and_old_player_rows(self):
        self.assertEqual(self.run_sync(), 0)
        checkpoint = self.query("SELECT * FROM sync_state")
        self.client.failures.add("/public/v1/statistics/player/102/sessions")
        self.assertEqual(self.run_sync("--end", "2026-03-09"), 2)
        self.assertEqual(self.query("SELECT * FROM sync_state"), checkpoint)
        self.assertEqual(self.query("SELECT COUNT(*) FROM player_session_stats WHERE player_id=102"), [(1,)])

    def test_failed_assignment_request_does_not_advance_checkpoint(self):
        self.assertEqual(self.run_sync(), 0)
        checkpoint = self.query("SELECT * FROM sync_state")
        self.client.failures.add("/public/v1/sensor-assignment/201")
        self.assertEqual(self.run_sync("--end", "2026-03-09"), 2)
        self.assertEqual(self.query("SELECT * FROM sync_state"), checkpoint)
        self.assertEqual(self.query("SELECT COUNT(*) FROM session_assignments WHERE session_id=201"), [(3,)])

    def test_first_sync_failure_never_creates_complete_checkpoint(self):
        self.client.failures.add("/public/v1/statistics/player/102/sessions")
        self.assertEqual(self.run_sync(), 2)
        self.assertEqual(self.query("SELECT * FROM sync_state"), [])
        self.assertEqual(self.query("SELECT value FROM cache_metadata WHERE key='sync_version'"), [])

    def test_invalid_response_shape_is_failure_without_checkpoint(self):
        self.client.responses["/public/v1/statistics/player/102/sessions"] = {"error": "synthetic"}
        self.assertEqual(self.run_sync(), 2)
        self.assertEqual(self.query("SELECT * FROM sync_state"), [])

    def test_legacy_checkpoint_forces_full_backfill(self):
        self.assertEqual(self.run_sync(), 0)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM cache_metadata WHERE key='sync_version'")
            connection.execute("UPDATE sync_state SET range_end='2026-03-07'")
        self.client.calls.clear()
        self.assertEqual(self.run_sync("--initial-lookback-days", "365"), 0)
        parameters = next(params for path, params in self.client.calls if path.endswith("sessions-and-phases"))
        self.assertEqual(parameters["min"], "2025-03-09 05:00:00")
        self.assertEqual(self.query("SELECT value FROM cache_metadata WHERE key='sync_version'"), [(str(sync.SYNC_VERSION),)])

    def test_explicit_range_does_not_claim_full_legacy_backfill(self):
        self.assertEqual(self.run_sync("--start", "2026-03-08"), 0)
        self.assertEqual(self.query("SELECT value FROM cache_metadata WHERE key='sync_version'"), [])

    def test_label_objects_and_ambiguous_activity_are_not_misclassified(self):
        cases = [
            ({"types": [{"label": "Match"}]}, "game"),
            ({"types": [{"label": "Training"}]}, "practice"),
            ({"types": [{"label": "Training"}, {"label": "Match"}]}, "other"),
            ({"types": ["Training, Match"]}, "other"),
            ({"types": ["Pregame film"]}, "other"),
            ({"types": [{"label": "Testing"}]}, "other"),
            ({}, "other"),
        ]
        for session, category in cases:
            with self.subTest(session=session):
                self.assertEqual(extractor.classify_session(session), category)

    def test_malformed_record_must_not_erase_cache_and_mark_sync_complete(self):
        self.assertEqual(self.run_sync(), 0)
        checkpoint = self.query("SELECT * FROM sync_state")
        self.client.responses["/public/v1/statistics/player/101/sessions"] = ["invalid-record"]
        self.assertEqual(self.run_sync("--end", "2026-03-09"), 2)
        self.assertEqual(self.query("SELECT * FROM sync_state"), checkpoint)
        self.assertEqual(self.query("SELECT COUNT(*) FROM player_session_stats WHERE player_id=101"), [(1,)])

    def test_migration_backfills_oldest_cached_date_beyond_rolling_lookback(self):
        self.assertEqual(self.run_sync(), 0)
        old_session = {"session_id": 199, "start_session": "2020-11-03 01:00:00", "types": ["Training"]}
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM cache_metadata WHERE key='sync_version'")
            connection.execute(
                """INSERT INTO sessions
                   (session_id, team_id, start_session, session_date, type_label, category, updated_at)
                   VALUES (199, 3, '2020-11-03 01:00:00', '2020-11-02', 'Training', 'practice', 'fixture')"""
            )
        self.client.responses["/public/v1/teams/3/sessions-and-phases"].append(old_session)
        self.client.responses["/public/v1/sensor-assignment/199"] = []
        self.client.calls.clear()
        self.assertEqual(self.run_sync("--initial-lookback-days", "1460"), 0)
        parameters = next(params for path, params in self.client.calls if path.endswith("sessions-and-phases"))
        # An extra overlap day is allowed to repair legacy UTC date boundaries.
        self.assertLessEqual(parameters["min"], "2020-11-02 05:00:00")
        self.assertLessEqual(self.query("SELECT range_start FROM sync_state")[0][0], "2020-11-02")

    def test_malformed_roster_entries_preserve_checkpoint_and_cached_records(self):
        self.assertEqual(self.run_sync(), 0)
        checkpoint = self.query("SELECT * FROM sync_state")
        roster = copy.deepcopy(self.client.responses["/public/v1/teams/3/players"])
        for malformed in ("invalid-player", {}, {"id": "101"}):
            with self.subTest(malformed=malformed):
                self.client.responses["/public/v1/teams/3/players"] = [*roster, malformed]
                self.assertEqual(self.run_sync("--end", "2026-03-09"), 2)
                self.assertEqual(self.query("SELECT * FROM sync_state"), checkpoint)
                self.assertEqual(self.query("SELECT COUNT(*) FROM player_session_stats"), [(3,)])
                self.assertEqual(self.query("SELECT value FROM cache_metadata WHERE key='sync_status'"), [("incomplete",)])

    def test_malformed_session_entries_preserve_checkpoint_and_cached_records(self):
        self.assertEqual(self.run_sync(), 0)
        checkpoint = self.query("SELECT * FROM sync_state")
        for malformed in ("invalid-session", {}, {"session_id": 201},
                          {"session_id": 201, "start_session": "invalid-date"},
                          {"session_id": "201", "start_session": "2026-03-09 00:30:00"}):
            with self.subTest(malformed=malformed):
                self.client.responses["/public/v1/teams/3/sessions-and-phases"] = [malformed]
                self.assertEqual(self.run_sync("--end", "2026-03-09"), 2)
                self.assertEqual(self.query("SELECT * FROM sync_state"), checkpoint)
                self.assertEqual(self.query("SELECT COUNT(*) FROM sessions"), [(2,)])
                self.assertEqual(self.query("SELECT COUNT(*) FROM player_session_stats"), [(3,)])
                self.assertEqual(self.query("SELECT value FROM cache_metadata WHERE key='sync_status'"), [("incomplete",)])

    def test_malformed_assignment_entries_preserve_assignments_and_checkpoint(self):
        self.assertEqual(self.run_sync(), 0)
        checkpoint = self.query("SELECT * FROM sync_state")
        assignments = self.query("SELECT * FROM session_assignments ORDER BY session_id, player_id")
        for malformed in ("invalid-assignment", {"player": "invalid-player"},
                          {"player": {"id": "103"}}, {"player": {}}):
            with self.subTest(malformed=malformed):
                self.client.responses["/public/v1/sensor-assignment/201"] = [malformed]
                self.assertEqual(self.run_sync("--end", "2026-03-09"), 2)
                self.assertEqual(self.query("SELECT * FROM sync_state"), checkpoint)
                self.assertEqual(self.query("SELECT * FROM session_assignments ORDER BY session_id, player_id"), assignments)
                self.assertEqual(self.query("SELECT value FROM cache_metadata WHERE key='sync_status'"), [("incomplete",)])

    def test_unassigned_sensor_is_valid_and_does_not_create_a_player(self):
        self.client.responses["/public/v1/sensor-assignment/201"].append({"player": None, "sensor_id": 901})
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM players"), [(3,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM session_assignments"), [(4,)])

    def test_removed_session_and_dependents_reconciled_after_successful_response(self):
        self.assertEqual(self.run_sync(), 0)
        # Remove the game that had all three performance rows; retain practice.
        self.client.responses["/public/v1/teams/3/sessions-and-phases"] = [
            self.client.responses["/public/v1/teams/3/sessions-and-phases"][1]
        ]
        for player_id in (101, 102, 103):
            self.client.responses[f"/public/v1/statistics/player/{player_id}/sessions"] = []
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.query("SELECT session_id FROM sessions"), [(202,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM player_session_stats"), [(0,)])
        self.assertEqual(self.query("SELECT session_id, player_id FROM session_assignments"), [(202, 101)])
        self.assertEqual(self.query("SELECT session_id FROM assignment_sync"), [(202,)])
        self.assertEqual(self.query("SELECT value FROM cache_metadata WHERE key='sync_status'"), [("complete",)])

    def test_calendar_reconciliation_preserves_sessions_outside_requested_range(self):
        self.assertEqual(self.run_sync(), 0)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO sessions
                   (session_id, team_id, start_session, session_date, type_label, category, updated_at)
                   VALUES (198, 3, '2025-01-02 17:00:00', '2025-01-02', 'Training', 'practice', 'fixture')"""
            )
        self.client.responses["/public/v1/teams/3/sessions-and-phases"] = []
        for player_id in (101, 102, 103):
            self.client.responses[f"/public/v1/statistics/player/{player_id}/sessions"] = []
        self.assertEqual(self.run_sync("--start", "2026-03-08"), 0)
        self.assertEqual(self.query("SELECT session_id FROM sessions"), [(198,)])


if __name__ == "__main__":
    unittest.main()
