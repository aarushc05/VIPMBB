"""Synthetic onboarding never seeds or contacts a private Kinexon source."""

from datetime import date

from fastapi.testclient import TestClient
import pytest

from server.app import analytics, data, demo, knowledge, sync


@pytest.fixture
def demo_target(db, monkeypatch):
    monkeypatch.setenv("VIPMBB_DEMO", "1")
    # The suite's database is an isolated temporary schema in vipmbb_test. Keep
    # the production guard strict; only this test changes its expected name.
    with db.database() as connection:
        monkeypatch.setattr(demo, "DEMO_DATABASE", connection.info.dbname)
    return date(2024, 10, 10)


def test_demo_requires_explicit_opt_in(db):
    with pytest.raises(RuntimeError, match="requires VIPMBB_DEMO=1"):
        demo.seed()
    assert db.status_data()["sessions"] == 0


def test_demo_rejects_actual_non_demo_database(db, monkeypatch):
    monkeypatch.setenv("VIPMBB_DEMO", "1")
    monkeypatch.setenv("PGDATABASE", "vipmbb_demo")
    # DATABASE_URL still resolves to vipmbb_test. Check the actual connection,
    # not an environment hint that could accidentally name a different target.
    with pytest.raises(RuntimeError, match="dedicated vipmbb_demo database"):
        demo.seed()
    assert db.status_data()["sessions"] == 0


def test_demo_never_overwrites_nonempty_unmarked_data(demo_target, seed):
    seed.player(11, "Existing record")
    with pytest.raises(RuntimeError, match="unmarked data"):
        demo.seed(demo_target)
    assert data.list_players()["players"][0]["name"] == "Existing record"
    assert not demo.status()["synthetic"]


def test_demo_preserves_missing_zero_coverage_and_classifications(demo_target):
    result = demo.seed(demo_target)
    assert result["status"] == "seeded"
    assert result["players"] == 8
    assert result["sessions"] == 8
    assert data.status_data()["sessions"] == 8
    assert data.status_data()["records"] == 63
    sessions = data.list_sessions()["sessions"]
    assert sum(item["classification"] == "practice" for item in sessions) == 5
    assert sum(item["classification"] == "game" for item in sessions) == 2
    assert sum(item["classification"] == "unknown" for item in sessions) == 1
    assert not next(item for item in sessions if item["classification"] == "unknown")[
        "reviewed"
    ]
    latest = analytics.get_report(900008)
    assert latest["coverage"]["recorded_players"] == 7
    assert latest["coverage"]["expected_players"] == 8
    assert latest["coverage"]["missing_player_ids"] == [910008]
    assert latest["session"]["status"] == "preliminary"
    first = next(player for player in latest["players"] if player["id"] == 910001)
    second = next(player for player in latest["players"] if player["id"] == 910002)
    assert first["metrics"]["jump_count"] == 0
    assert second["metrics"]["jump_count"] is None
    assert first["baseline"]["sample_count"] == 4
    assert first["baseline"]["change_pct"] is not None
    assert len(latest["drills"]) == 3
    assert latest["drills"][0]["missing_player_ids"] == [910008]
    assert analytics.get_report(900007)["players"][0]["baseline"]["sample_count"] == 0


def test_demo_repeat_seed_preserves_dates_notes_and_report_versions(demo_target):
    initial = demo.seed(demo_target)
    knowledge.add_note("My demo note", "Keep this local experiment.")
    data.review_session(900006, "practice", "Fictional review for testing.")
    with data.database() as connection:
        before = connection.execute("SELECT count(*) AS count FROM reports").fetchone()[
            "count"
        ]
    again = demo.seed(date(2025, 1, 1))
    assert again["status"] == "already-seeded"
    assert again["reference_date"] == initial["reference_date"]
    assert again["seeded_at"] == initial["seeded_at"]
    assert any(
        note["title"] == "My demo note"
        for note in knowledge.list_documents()["documents"]
    )
    # Reviewing a formerly unknown session changes downstream baselines. One
    # seed retry may rebuild derived reports, but never source measurements.
    assert analytics.get_report(900006)["session"]["classification"] == "practice"
    with data.database() as connection:
        assert connection.execute(
            "SELECT max(local_date) AS day FROM sessions"
        ).fetchone()["day"] == demo_target.replace(day=9)
        after = connection.execute("SELECT count(*) AS count FROM reports").fetchone()[
            "count"
        ]
    assert after >= before
    demo.seed()
    with data.database() as connection:
        assert (
            connection.execute("SELECT count(*) AS count FROM reports").fetchone()[
                "count"
            ]
            == after
        )


def test_demo_source_identity_cannot_mix_with_live_import(demo_target):
    demo.seed(demo_target)
    with (
        data.database() as connection,
        pytest.raises(sync.SyncError, match="different Kinexon source"),
    ):
        sync._verify_source(connection)


def test_real_app_status_and_port_remain_private(client):
    assert client.get("/api/status").json()["demo"]["enabled"] is False
    assert (
        client.get("/api/health", headers={"Host": "127.0.0.1:8002"}).status_code == 403
    )


def test_demo_api_port_banner_sync_guard_and_bounded_assistant(demo_target):
    from server.app.api import create_app

    demo.seed(demo_target)
    with TestClient(create_app(), base_url="http://127.0.0.1:8002") as client:
        bootstrap = client.get("/api/bootstrap").json()
        assert bootstrap["demo"]["synthetic"] is True
        assert bootstrap["demo"]["reference_date"] == demo_target.isoformat()
        headers = {
            "X-VIPMBB-CSRF": bootstrap["csrf_token"],
            "Origin": "http://127.0.0.1:8002",
        }
        assert client.get("/api/status").json()["demo"]["enabled"] is True
        assert client.post("/api/sync", json={}, headers=headers).status_code == 409
        assert (
            client.post("/api/model/index", json={}, headers=headers).status_code == 409
        )
        assert data.list_jobs()["jobs"] == []
        printable = client.get("/api/reports/900008/print")
        assert printable.status_code == 200
        assert "SYNTHETIC DEMO" in printable.text
        answer = client.post(
            "/api/chat",
            headers=headers,
            json={
                "message": "Who had the highest load per minute in this session?",
                "session_id": 900008,
            },
        )
        assert answer.status_code == 200
        assert "Demo" in str(answer.json())
        assert "910008" in str(answer.json()) or "missing" in str(answer.json()).lower()
