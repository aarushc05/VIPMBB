"""Deterministic Kinexon contract tests: all network calls are substituted."""
from copy import deepcopy
import json

import pytest


@pytest.fixture
def fake_kinexon(db, monkeypatch):
    from local_app import sync

    calls = []
    responses = {
        "/public/v1/teams/3/players": [
            {"id": 11, "first_name": "Alex", "last_name": "Rivera", "deleted": False},
            {"id": 22, "first_name": "Blake", "last_name": "Chen", "deleted": False},
        ],
        "/public/v1/teams/3/sessions-and-phases": [{
            "session_id": 101, "start_session": "2026-10-06T18:00:00Z", "end_session": "2026-10-06T20:00:00Z",
            "types": ["Training"], "description": "Team practice", "phases": [],
        }],
        "/public/v1/sensor-assignment/101": [
            {"player": {"id": 11, "first_name": "Alex", "last_name": "Rivera"}},
            {"player": {"id": 22, "first_name": "Blake", "last_name": "Chen"}},
            {"player": {"id": 33, "first_name": "Historical", "last_name": "Player", "deleted": True}},
        ],
    }
    for pid in (11, 22, 33):
        responses[f"/public/v1/statistics/player/{pid}/sessions"] = [{
            "session_id": 101, "duration": 7200, "time_on_playing_field": 3600,
            "distance_total": 3000, "mechanical_load": pid * 100, "event_count_jump": 0,
        }]

    class FakeClient:
        def get(self, path, params=None):
            calls.append((path, deepcopy(params)))
            if path not in responses:
                raise AssertionError(f"Unmocked Kinexon endpoint: {path}")
            value = responses[path]
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)

    monkeypatch.setattr(sync, "Client", FakeClient)
    return responses, calls


def test_sync_fetches_all_roster_and_assigned_historical_players(db, fake_kinexon):
    from local_app import sync
    result = sync.sync_range("2026-10-06", "2026-10-06")
    assert result["complete"] is True
    assert result["players_requested"] == 3
    paths = {path for path, _ in fake_kinexon[1]}
    for pid in (11, 22, 33):
        assert f"/public/v1/statistics/player/{pid}/sessions" in paths
    with db.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stats WHERE session_id=101").fetchone()[0] == 3
        session = conn.execute("SELECT * FROM sessions WHERE id=101").fetchone()
        assert session["expected_players"] == 3
        assert session["assignment_complete"] == 1
        assert session["sync_complete"] == 1
        assert session["reviewed"] == 0
    report = db.get_report(101)
    assert report["coverage"]["complete"] is True
    assert report["session"]["reviewed"] is False


def test_failed_player_preserves_old_measurement_and_does_not_advance_success(db, seed, fake_kinexon):
    from local_app import sync
    seed.player(22, "Blake Chen")
    seed.session(101, status="complete", expected_players=3)
    seed.stats(101, 22, mechanical_load=777)
    with db.database() as conn:
        conn.execute("INSERT INTO meta VALUES ('last_sync','old-success')")
    fake_kinexon[0]["/public/v1/statistics/player/22/sessions"] = sync.SyncError("Synthetic HTTP 503")
    result = sync.sync_range("2026-10-06", "2026-10-06")
    assert result["complete"] is False
    assert result["players_succeeded"] == 2
    assert db.status_data()["last_sync"] == "old-success"
    assert db.status_data()["sync_status"] == "partial"
    with db.database() as conn:
        old = conn.execute("SELECT metrics_json FROM stats WHERE player_id=22 AND session_id=101").fetchone()[0]
        assert json.loads(old)["mechanical_load"] == 777
        assert conn.execute("SELECT sync_complete FROM sessions WHERE id=101").fetchone()[0] == 0
    assert db.get_report(101)["coverage"]["complete"] is False


def test_repeated_sync_is_idempotent_but_late_source_data_revises_report(db, fake_kinexon):
    from local_app import sync
    sync.sync_range("2026-10-06", "2026-10-06")
    first = db.get_report(101)
    sync.sync_range("2026-10-06", "2026-10-06")
    assert db.get_report(101)["version"] == first["version"]
    assert db.status_data()["records"] == 3
    fake_kinexon[0]["/public/v1/statistics/player/11/sessions"][0]["mechanical_load"] += 100
    sync.sync_range("2026-10-06", "2026-10-06")
    changed = db.get_report(101)
    assert changed["version"] == first["version"] + 1
    assert changed["summary"]["mechanical_load"] == first["summary"]["mechanical_load"] + 100


def test_authoritative_empty_player_result_removes_stale_stats_but_shows_gap(db, seed, fake_kinexon):
    from local_app import sync
    seed.player(22, "Blake Chen")
    seed.session(101)
    seed.stats(101, 22)
    fake_kinexon[0]["/public/v1/statistics/player/22/sessions"] = []
    sync.sync_range("2026-10-06", "2026-10-06")
    result = db.get_report(101)
    assert result["coverage"]["recorded_players"] == 2
    assert result["coverage"]["missing_player_ids"] == [22]
    assert result["coverage"]["complete"] is False


def test_missing_measurement_and_true_zero_survive_api_normalization():
    from local_app.sync import normalize_metrics
    metrics = normalize_metrics({"time_on_playing_field": 0, "duration": 600,
                                 "mechanical_load": 0, "event_count_jump": 0})
    assert metrics["minutes"] == 0
    assert metrics["exposure_basis"] == "on_playing_field"
    assert metrics["mechanical_load"] == 0
    assert metrics["jump_count"] == 0
    assert metrics["distance_m"] is None
    assert metrics["load_per_minute"] is None


def test_malformed_or_duplicate_statistics_are_not_summed(db, seed, fake_kinexon):
    from local_app import sync
    seed.player(11, "Alex Rivera")
    seed.session(101)
    seed.stats(101, 11, mechanical_load=123)
    path = "/public/v1/statistics/player/11/sessions"
    fake_kinexon[0][path] *= 2
    result = sync.sync_range("2026-10-06", "2026-10-06")
    assert result["complete"] is False
    with db.database() as conn:
        values = json.loads(conn.execute("SELECT metrics_json FROM stats WHERE player_id=11").fetchone()[0])
        assert values["mechanical_load"] == 123


def test_sync_respects_atlanta_day_window_not_utc_midnight(db, fake_kinexon):
    from local_app import sync
    sync.sync_range("2026-10-06", "2026-10-06")
    calendar_params = next(params for path, params in fake_kinexon[1] if path.endswith("sessions-and-phases"))
    assert calendar_params["min"] == "2026-10-06 04:00:00"
    assert calendar_params["max"] == "2026-10-07 04:00:00"


def test_sync_respects_dst_fall_back_25_hour_day(db, fake_kinexon):
    from local_app import sync
    sync.sync_range("2026-11-01", "2026-11-01")
    calendar_params = next(params for path, params in fake_kinexon[1] if path.endswith("sessions-and-phases"))
    assert calendar_params["min"] == "2026-11-01 04:00:00"
    assert calendar_params["max"] == "2026-11-02 05:00:00"


def test_successful_calendar_removal_retains_audit_but_excludes_session(db, seed, fake_kinexon):
    from local_app import sync
    seed.session(999, date="2026-10-06")
    sync.sync_range("2026-10-06", "2026-10-06")
    assert 999 not in {row["id"] for row in db.list_sessions()["sessions"]}
    with db.database() as conn:
        assert conn.execute("SELECT removed_upstream FROM sessions WHERE id=999").fetchone()[0] == 1


def test_phase_statistics_retrieved_for_every_assigned_player_without_double_counting(db, fake_kinexon):
    from local_app import sync
    responses, calls = fake_kinexon
    responses["/public/v1/teams/3/sessions-and-phases"][0]["phases"] = [
        {"id": 201, "description": "First drill", "start_phase": "2026-10-06T18:00:00Z", "end_phase": "2026-10-06T19:00:00Z"},
        {"id": 202, "description": "Overlapping drill", "start_phase": "2026-10-06T18:30:00Z", "end_phase": "2026-10-06T19:30:00Z"},
    ]
    for phase in (201, 202):
        for player in (11, 22, 33):
            responses[f"/public/v1/statistics/player/{player}/phase/{phase}"] = [
                {"phase_id": phase, "time_on_playing_field": 3600, "mechanical_load": 100000},
            ]
    for player in (11, 22, 33):
        responses[f"/public/v1/statistics/player/{player}/phases"] = [
            {"phase_id": phase, "session_id": 101, "time_on_playing_field": 3600, "mechanical_load": 100000}
            for phase in (201, 202)
        ]
    result = sync.sync_range("2026-10-06", "2026-10-06")
    assert result["phase_records"] == 6
    report = db.get_report(101)
    assert report["summary"]["mechanical_load"] == 6600
    assert len(report["drills"]) == 2
    assert all(not drill["valid"] for drill in report["drills"])
    assert all(drill["player_count"] == 3 for drill in report["drills"])
    assert sum(path.endswith("/phases") for path, _ in calls) == 3
    assert not any("/phase/" in path for path, _ in calls)


def test_human_review_survives_later_source_label_change(db, fake_kinexon):
    from local_app import sync
    sync.sync_range("2026-10-06", "2026-10-06")
    db.review_session(101, "game", "Staff reviewed recording")
    sync.sync_range("2026-10-06", "2026-10-06")
    assert db.get_report(101)["session"]["classification"] == "game"


def test_request_failures_never_echo_secrets(monkeypatch):
    from local_app import sync
    import requests
    monkeypatch.setattr(sync, "credentials", lambda: {"KINEXON_USER": "private-user", "KINEXON_PASSWORD": "private-password", "KINEXON_API_KEY": "private-key"})
    monkeypatch.setattr(sync.clock, "sleep", lambda duration: None)
    client = sync.Client()
    def fail(*args, **kwargs):
        raise requests.ConnectionError("https://host/?apiKey=private-key private-password")
    monkeypatch.setattr(client.session, "get", fail)
    with pytest.raises(sync.SyncError) as error:
        client.get("/public/v1/test")
    assert "private-key" not in str(error.value)
    assert "private-password" not in str(error.value)


def test_cli_missing_credentials_does_not_enqueue(db, monkeypatch, capsys):
    from local_app import sync
    monkeypatch.setattr(sync, "credentials_configured", lambda: False)
    assert sync.main(["--start", "2026-10-01", "--end", "2026-10-06"]) == 1
    assert db.list_jobs()["jobs"] == []
    assert "not configured" in capsys.readouterr().out


def test_cli_rejects_invalid_range_before_queue(db, monkeypatch):
    from local_app import sync
    monkeypatch.setattr(sync, "credentials_configured", lambda: True)
    assert sync.main(["--start", "2026-10-06", "--end", "2026-10-01"]) == 2
    assert db.list_jobs()["jobs"] == []


def test_cli_queues_without_running_when_worker_owns_lock(db, monkeypatch, capsys):
    import fcntl
    from local_app import sync, worker
    monkeypatch.setattr(sync, "credentials_configured", lambda: True)
    def lock_busy(*args):
        raise BlockingIOError()
    def must_not_process():
        raise AssertionError("CLI must not run alongside the existing worker")
    monkeypatch.setattr(fcntl, "flock", lock_busy)
    monkeypatch.setattr(worker, "process_one", must_not_process)
    assert sync.main(["--start", "2026-10-01", "--end", "2026-10-06"]) == 0
    assert db.list_jobs()["jobs"][0]["status"] == "queued"
    assert "running app worker" in capsys.readouterr().out


def test_cli_runs_queue_and_prints_only_aggregate_results(db, monkeypatch, capsys):
    from local_app import sync, worker
    monkeypatch.setattr(sync, "credentials_configured", lambda: True)
    monkeypatch.setattr(worker, "sync_range", lambda *args: {"complete": True, "sessions": 2,
                                                          "players_succeeded": 3, "phase_records": 4})
    assert sync.main(["--start", "2026-10-01", "--end", "2026-10-06"]) == 0
    assert db.list_jobs()["jobs"][0]["status"] == "completed"
    output = capsys.readouterr().out
    assert "2 recordings" in output
    assert "3 player requests" in output


@pytest.fixture
def phase_client():
    phases = [{"id": 201, "session_id": 101, "start_phase": "2026-10-06T18:00:00Z", "end_phase": "2026-10-06T19:00:00Z"},
              {"id": 202, "session_id": 101, "start_phase": "2026-10-06T19:00:00Z", "end_phase": "2026-10-06T20:00:00Z"}]
    class PhaseClient:
        calls = []
        bulk = [{"phase_id": 201, "session_id": 101, "time_on_playing_field": 1200, "mechanical_load": 250},
                {"phase_id": 202, "session_id": 101, "time_on_playing_field": 1800, "mechanical_load": 300}]
        individual = {201: [{"phase_id": 201, "session_id": 101, "duration": 1200, "mechanical_load": 250}],
                      202: [{"phase_id": 202, "session_id": 101, "duration": 1800, "mechanical_load": 300}]}
        def get(self, path, params=None):
            self.calls.append((path, params))
            value = self.bulk if path.endswith("/phases") else self.individual[int(path.rsplit("/", 1)[-1])]
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)
    return PhaseClient(), phases


def test_bulk_phase_success_needs_one_request_and_preserves_values(phase_client):
    from local_app.sync import fetch_phase_records, normalize_metrics
    client, phases = phase_client
    records = fetch_phase_records(client, 11, phases)
    assert len(client.calls) == 1
    assert normalize_metrics(records[201])["load_per_minute"] == 12.5
    assert normalize_metrics(records[202])["load_per_minute"] == 10
    assert client.calls[0][1]["min"] == "2026-10-06 17:59:59"
    assert client.calls[0][1]["max"] == "2026-10-06 20:00:01"


@pytest.mark.parametrize("invalid", ["error", "malformed", "duplicate", "wrong_session"])
def test_invalid_bulk_response_falls_back_without_aggregating_bad_identity(phase_client, invalid):
    from local_app.sync import fetch_phase_records, SyncError
    client, phases = phase_client
    client.bulk = {"error": SyncError("Synthetic 503"), "malformed": {"wrong": "shape"},
                   "duplicate": [client.bulk[0], client.bulk[0]],
                   "wrong_session": [{"phase_id": 201, "session_id": 999, "time_on_playing_field": 1200}]}[invalid]
    records = fetch_phase_records(client, 11, phases)
    assert set(records) == {201, 202}
    assert records[201]["duration"] == 1200
    assert records[202]["duration"] == 1800
    assert sum("/phase/" in path for path, _ in client.calls) == 2


def test_bulk_missing_exposure_uses_individual_duration_fallback(phase_client):
    from local_app.sync import fetch_phase_records, normalize_metrics
    client, phases = phase_client
    client.bulk[0].pop("time_on_playing_field")
    records = fetch_phase_records(client, 11, phases)
    metrics = normalize_metrics(records[201])
    assert metrics["minutes"] == 20
    assert metrics["exposure_basis"] == "session_duration"
    assert sum("/phase/" in path for path, _ in client.calls) == 1


def test_bulk_true_zero_exposure_is_not_replaced_by_duration(phase_client):
    from local_app.sync import fetch_phase_records, normalize_metrics
    client, phases = phase_client
    client.bulk[0]["time_on_playing_field"] = 0
    result = fetch_phase_records(client, 11, phases)
    assert normalize_metrics(result[201])["minutes"] == 0
    assert normalize_metrics(result[201])["load_per_minute"] is None
    assert len(client.calls) == 1


def test_bulk_absent_phase_is_explicit_missing_not_invented_zero(phase_client):
    from local_app.sync import fetch_phase_records
    client, phases = phase_client
    client.bulk = client.bulk[:1]
    assert fetch_phase_records(client, 11, phases)[202] is None


def test_invalid_individual_identity_after_bulk_failure_raises(phase_client):
    from local_app.sync import fetch_phase_records, SyncError
    client, phases = phase_client
    client.bulk = SyncError("Synthetic unavailable endpoint")
    client.individual[201][0]["phase_id"] = 999
    with pytest.raises(SyncError, match="identity"):
        fetch_phase_records(client, 11, phases)


def test_phase_bulk_separates_widely_spaced_dates_and_ignores_other_batch_records(phase_client):
    from local_app.sync import fetch_phase_records
    client, phases = phase_client
    phases[1].update(start_phase="2026-12-01T18:00:00Z", end_phase="2026-12-01T19:00:00Z")
    result = fetch_phase_records(client, 11, phases)
    assert set(result) == {201, 202}
    assert all(result.values())
    assert len(client.calls) == 2
    assert client.calls[0][1]["max"].startswith("2026-10-06")
    assert client.calls[1][1]["min"].startswith("2026-12-01")


def test_invalid_phase_bounds_use_individual_endpoint(phase_client):
    from local_app.sync import fetch_phase_records
    client, phases = phase_client
    phases[0]["start_phase"] = None
    result = fetch_phase_records(client, 11, phases)
    assert result[201]["duration"] == 1200
    assert any(path.endswith("/phase/201") for path, _ in client.calls)


def test_historical_success_does_not_regress_recent_checkpoint(db, fake_kinexon):
    from local_app import sync
    with db.database() as conn:
        conn.execute("INSERT INTO meta VALUES('last_sync_end','2026-10-07')")
    sync.sync_range("2026-10-06", "2026-10-06")
    with db.database() as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='last_sync_end'").fetchone()[0] == "2026-10-07"


def test_bulk_fallback_failure_keeps_all_existing_phase_records_for_player(db, fake_kinexon):
    from local_app import sync
    responses, _ = fake_kinexon
    responses["/public/v1/teams/3/sessions-and-phases"][0]["phases"] = [
        {"id": pid, "start_phase": "2026-10-06T18:00:00Z", "end_phase": "2026-10-06T19:00:00Z"}
        for pid in (201, 202)
    ]
    for player in (11, 22, 33):
        responses[f"/public/v1/statistics/player/{player}/phases"] = [
            {"phase_id": phase, "session_id": 101, "time_on_playing_field": 1200, "mechanical_load": 250}
            for phase in (201, 202)
        ]
    assert sync.sync_range("2026-10-06", "2026-10-06")["complete"] is True
    responses["/public/v1/statistics/player/11/phases"] = sync.SyncError("Synthetic bulk failure")
    responses["/public/v1/statistics/player/11/phase/201"] = [
        {"phase_id": 201, "session_id": 101, "duration": 1200, "mechanical_load": 99999}
    ]
    responses["/public/v1/statistics/player/11/phase/202"] = sync.SyncError("Synthetic individual failure")
    assert sync.sync_range("2026-10-06", "2026-10-06")["complete"] is False
    with db.database() as conn:
        rows = conn.execute("SELECT metrics_json FROM phase_stats WHERE player_id=11").fetchall()
        assert len(rows) == 2
        assert all(json.loads(row[0])["mechanical_load"] == 250 for row in rows)
        assert conn.execute("SELECT sync_complete FROM sessions WHERE id=101").fetchone()[0] == 0
