"""Source configuration remains private and cannot mix unrelated team IDs."""

import pytest


@pytest.mark.parametrize(
    "url",
    [
        "http://source.example",
        "https://user:secret@source.example",
        "https://source.example/path",
        "https://source.example?apiKey=secret",
        "https://source.example#fragment",
        "https://source.example:bad",
        "https://source.example:99999",
    ],
)
def test_source_rejects_unsafe_origin_without_echoing_secrets(db, monkeypatch, url):
    from server.app import sync

    monkeypatch.setenv("KINEXON_BASE_URL", url)
    with pytest.raises(sync.SyncError) as error:
        sync.source_settings()
    assert "secret" not in str(error.value) and url not in str(error.value)


@pytest.mark.parametrize("team", ["0", "-1", "abc", "3.5"])
def test_source_team_requires_positive_integer(db, monkeypatch, team):
    from server.app import sync

    monkeypatch.setenv("KINEXON_TEAM_ID", team)
    with pytest.raises(sync.SyncError):
        sync.source_settings()


def test_equivalent_source_origins_have_same_identity(db, monkeypatch):
    from server.app import sync

    monkeypatch.setenv("KINEXON_BASE_URL", "https://SOURCE.example:443/")
    monkeypatch.setenv("KINEXON_TEAM_ID", "7")
    first = sync.source_identity()
    assert sync.source_settings() == ("https://source.example", 7)
    monkeypatch.setenv("KINEXON_BASE_URL", "https://source.example")
    assert sync.source_identity() == first


def test_populated_legacy_database_refuses_different_source(db, seed, monkeypatch):
    from server.app import sync

    seed.player(11, "Existing Synthetic Player")
    monkeypatch.setenv("KINEXON_TEAM_ID", "7")
    with pytest.raises(sync.SyncError, match="original Kinexon source/team"):
        with db.database() as connection:
            sync._verify_source(connection)
    with db.database() as connection:
        assert connection.execute("SELECT id FROM players").fetchone()["id"] == 11
        assert (
            connection.execute(
                "SELECT value FROM meta WHERE key='source_identity'"
            ).fetchone()
            is None
        )


def test_bound_database_refuses_source_change_and_preserves_records(
    db, seed, monkeypatch
):
    from server.app import sync

    with db.database() as connection:
        sync._verify_source(connection)
    seed.player(11, "Existing Synthetic Player")
    monkeypatch.setenv("KINEXON_BASE_URL", "https://other-source.example")
    with pytest.raises(sync.SyncError, match="different Kinexon source/team"):
        with db.database() as connection:
            sync._verify_source(connection)
    with db.database() as connection:
        assert connection.execute("SELECT id FROM players").fetchone()["id"] == 11


def test_empty_database_can_bind_alternate_source_and_team(db, monkeypatch):
    from server.app import sync

    monkeypatch.setenv("KINEXON_BASE_URL", "https://synthetic-source.example")
    monkeypatch.setenv("KINEXON_TEAM_ID", "7")
    with db.database() as connection:
        sync._verify_source(connection)
    with db.database() as connection:
        assert (
            connection.execute(
                "SELECT value FROM meta WHERE key='source_identity'"
            ).fetchone()["value"]
            == sync.source_identity()
        )
