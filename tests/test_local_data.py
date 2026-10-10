import pytest


@pytest.mark.parametrize(
    "value", [None, True, False, -1, float("nan"), float("inf"), "bad"]
)
def test_nonmeasurements_are_not_numbers(db, value):
    assert db.finite(value) is None


def test_known_zero_is_a_measurement(db):
    assert db.finite(0) == 0
    assert db.finite("0") == 0


@pytest.mark.parametrize(
    "utc,local",
    [
        ("2026-03-05T00:30:00Z", "2026-03-04"),
        ("2026-07-02T03:30:00Z", "2026-07-01"),
        ("2026-11-01T05:30:00Z", "2026-11-01"),
        ("2026-11-01T06:30:00Z", "2026-11-01"),
    ],
)
def test_atlanta_calendar_dates_respect_dst(db, utc, local):
    assert db.timestamp(utc).astimezone(db.ATLANTA).date().isoformat() == local


def test_home_game_evening_training_is_not_auto_practice(db):
    assert (
        db.source_classification(
            ["Training"], "2026-03-04T22:28:00Z", "2026-03-05T02:10:00Z"
        )
        == "unknown"
    )


@pytest.mark.parametrize(
    "labels,start,end",
    [
        (["Training", "Match"], "2026-03-03T18:00:00Z", "2026-03-03T20:00:00Z"),
        (["Match"], "2026-03-03T18:00:00Z", "2026-03-03T20:00:00Z"),
        (["Training"], "2026-03-03T18:00:00Z", "2026-03-04T20:00:00Z"),
        (["Training"], "2026-03-03T18:00:00Z", None),
        (["Training"], "2026-03-03T18:00:00Z", "2026-03-03T17:00:00Z"),
    ],
)
def test_ambiguous_or_invalid_recording_abstains(db, labels, start, end):
    assert db.source_classification(labels, start, end) == "unknown"


def test_training_label_can_suggest_but_not_verify_practice(db):
    assert (
        db.source_classification(
            ["Training"], "2026-03-03T18:00:00Z", "2026-03-03T20:00:00Z"
        )
        == "practice"
    )


def test_calendar_filter_is_inclusive_and_empty_range_is_empty(db, seed):
    for sid, day in [(1, "2026-03-01"), (2, "2026-03-02"), (3, "2026-03-03")]:
        seed.session(sid, day)
    assert {
        s["id"]
        for s in db.list_sessions(start="2026-03-01", end="2026-03-02")["sessions"]
    } == {1, 2}
    assert db.list_sessions(start="2026-09-01", end="2026-09-30")["sessions"] == []
    assert db.list_sessions(start="2026-09-01", end="2026-09-30")["total"] == 0


def test_search_is_parameterized_and_wildcards_are_literal(db, seed):
    seed.session(1, title="100% effort")
    seed.session(2, title="Other session")
    assert db.list_sessions(q="%")["total"] == 1
    assert db.list_sessions(q="' OR 1=1 --")["total"] == 0
    assert db.status_data()["sessions"] == 2


def test_enqueued_identical_job_is_deduplicated(db):
    first = db.enqueue_job("sync", {"start": "2026-10-01", "end": "2026-10-06"})
    second = db.enqueue_job("sync", {"end": "2026-10-06", "start": "2026-10-01"})
    assert first["id"] == second["id"]
    assert len(db.list_jobs()["jobs"]) == 1


def test_job_payload_cannot_inject_arbitrary_job_kind(db):
    with pytest.raises(ValueError):
        db.enqueue_job("shell", {"command": "rm -rf /"})


@pytest.mark.parametrize(
    "source,expected",
    [
        ("{'id': 3, 'label': 'Training'}", ["Training"]),
        ("{'label': 'Training'}, {'label': 'Match'}", ["Training", "Match"]),
        ([{"label": "Drill"}, {"label": "Training"}], ["Drill", "Training"]),
        ("[{'label': 'Training'}, {'label': 'Training'}]", ["Training"]),
    ],
)
def test_legacy_dictionary_label_shapes_are_normalized(db, source, expected):
    assert db.labels(source) == expected


def test_initialize_never_reclassifies_existing_reviewed_data(db, seed):
    seed.session(101, classification="game", reviewed=True, labels=["Training"])
    seed.session(102, classification="unknown", reviewed=False, labels=["Training"])
    db.initialize()
    with db.database() as conn:
        rows = {row["id"]: row for row in conn.execute("SELECT * FROM sessions")}
    assert rows[101]["source_labels"] == ["Training"]
    assert rows[101]["classification"] == "game"
    assert rows[101]["reviewed"] is True
    assert rows[102]["classification"] == "unknown"
    assert rows[102]["reviewed"] is False
