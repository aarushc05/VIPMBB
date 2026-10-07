"""Hermetic fixtures: never touch the user's cache or contact Kinexon/Ollama."""
from __future__ import annotations

import json
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VIPMBB_DATA_DIR", str(tmp_path / "private-data"))
    monkeypatch.setenv("VIPMBB_IMPORT_LEGACY", "0")
    monkeypatch.setenv("VIPMBB_DISABLE_MODEL", "1")
    for name in ("KINEXON_USER", "KINEXON_USERNAME", "KINEXON_PASSWORD", "KINEXON_API_KEY", "KINEXON_APIKEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def db(isolated_runtime):
    from local_app import data

    data.initialize()
    return data


@pytest.fixture
def seed(db):
    """Insert deterministic synthetic records through the published schema."""
    now = "2026-10-07T12:00:00+00:00"

    def insert(table, values):
        with db.database() as conn:
            names = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            conn.execute(f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({placeholders})", tuple(values.values()))

    def player(id, name, number="1", active=True):
        insert("players", {"id": id, "name": name, "number": number, "active": int(active)})

    def session(id, date="2026-10-06", *, start=None, end=None, classification="practice", reviewed=True,
                status="complete", expected_players=2, labels=None, title=None):
        insert("sessions", {
            "id": id, "title": title or f"Practice {id}",
            "start_utc": start or f"{date}T18:00:00+00:00",
            "end_utc": end or f"{date}T20:00:00+00:00", "local_date": date,
            "source_labels": json.dumps(labels or ["Training"]),
            "classification": classification, "reviewed": int(reviewed), "status": status,
            "expected_players": expected_players, "source_hash": f"session-{id}", "updated_at": now,
        })

    def stats(session_id, player_id, *, minutes=60.0, mechanical_load=1200.0,
              distance_m=3000.0, legacy=False, exposure_basis="on_playing_field", **extra):
        metrics = {"minutes": minutes, "mechanical_load": mechanical_load, "distance_m": distance_m,
                   "load_per_minute": mechanical_load / minutes if mechanical_load is not None and minutes else None,
                   "accel_load": None, "metabolic_work": None, "speed_max": None,
                   "acceleration_count": None, "deceleration_count": None,
                   "change_of_direction_count": None, "jump_count": None,
                   "exposure_basis": exposure_basis}
        metrics.update(extra)
        insert("stats", {"session_id": session_id, "player_id": player_id,
                         "metrics_json": json.dumps(metrics),
                         "missing_json": json.dumps([key for key, value in metrics.items() if value is None]),
                         "legacy": int(legacy), "updated_at": now})

    return type("Seed", (), {"player": staticmethod(player), "session": staticmethod(session),
                              "stats": staticmethod(stats), "insert": staticmethod(insert)})()


@pytest.fixture
def practice(seed):
    seed.player(11, "Alex Rivera", "11")
    seed.player(22, "Blake Chen", "22")
    seed.session(101)
    seed.stats(101, 11, minutes=60, mechanical_load=1200, distance_m=3000, jump_count=0)
    seed.stats(101, 22, minutes=30, mechanical_load=300, distance_m=1800, jump_count=None)
    return 101


@pytest.fixture
def client(isolated_runtime):
    from fastapi.testclient import TestClient
    from local_app.app import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test_client:
        yield test_client


@pytest.fixture
def csrf(client):
    response = client.get("/api/bootstrap")
    assert response.status_code == 200, response.text
    token = response.json()["csrf_token"]
    return {"X-VIPMBB-CSRF": token, "Origin": "http://127.0.0.1:8000"}
