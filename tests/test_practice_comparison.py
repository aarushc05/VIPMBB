"""Practice trends use observed exposure and explicit eligibility, not a review gate."""

from datetime import date, timedelta

import pytest


@pytest.fixture
def practices(db, seed):
    seed.player(11, "Synthetic Practice Player")

    def create(
        session_id,
        day,
        *,
        minutes=60,
        load=1200,
        basis="on_playing_field",
        legacy=False,
        classification="practice",
        reviewed=False,
        labels=None,
        start=None,
        end=None,
        status="complete",
        removed=False,
    ):
        seed.session(
            session_id,
            day,
            start=start,
            end=end,
            classification=classification,
            reviewed=reviewed,
            labels=labels,
            expected_players=1,
            status=status,
        )
        seed.stats(
            session_id,
            11,
            minutes=minutes,
            mechanical_load=load,
            exposure_basis=basis,
            legacy=legacy,
        )
        if removed:
            with db.database() as connection:
                connection.execute(
                    "UPDATE sessions SET removed_upstream=TRUE WHERE id=%s",
                    (session_id,),
                )
        return session_id

    return create


def baseline(db, session_id):
    result = db.get_report(session_id)
    return next(row for row in result["players"] if row["id"] == 11)["baseline"]


def prior_three(practices, **kwargs):
    for session_id, day in [(1, "2026-10-01"), (2, "2026-10-02"), (3, "2026-10-03")]:
        practices(session_id, day, **kwargs)


def test_unreviewed_practices_produce_weighted_baseline_and_chronological_points(
    db, practices
):
    practices(3, "2026-10-03", minutes=60, load=1800)
    practices(1, "2026-10-01", minutes=30, load=300)
    practices(2, "2026-10-02", minutes=60, load=1200)
    practices(4, "2026-10-06", minutes=60, load=2400)
    result = baseline(db, 4)
    assert result["sample_count"] == 3
    assert result["load_per_minute"] == pytest.approx(22)
    assert result["change_pct"] == pytest.approx((40 / 22 - 1) * 100)
    assert result["reason_code"] is None
    assert result["lookback_days"] == 90
    assert result["min_samples"] == 3 and result["max_samples"] == 5
    assert result["season_start"] == "2026-07-01"
    assert result["session_ids"] == [3, 2, 1]
    assert [point["session_id"] for point in result["history"]] == [1, 2, 3]
    assert [point["date"] for point in result["history"]] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
    ]
    assert [point["load_per_minute"] for point in result["history"]] == [10, 20, 30]
    assert all(point["reviewed"] is False for point in result["history"])
    assert all(
        point["start"].startswith(point["date"] + "T18:00:00")
        for point in result["history"]
    )
    assert result["current_point"]["session_id"] == 4
    assert result["current_point"]["load_per_minute"] == 40
    assert result["current_point"]["minutes"] == 60
    assert result["current_point"]["mechanical_load"] == 2400


def test_only_five_most_recent_eligible_practices_enter_history(db, practices):
    for session_id in range(1, 8):
        practices(
            session_id,
            (date(2026, 9, 20) + timedelta(days=session_id)).isoformat(),
            minutes=60,
            load=session_id * 60,
        )
    practices(20, "2026-10-06", load=600)
    result = baseline(db, 20)
    assert result["sample_count"] == 5
    assert result["session_ids"] == [7, 6, 5, 4, 3]
    assert [point["session_id"] for point in result["history"]] == [3, 4, 5, 6, 7]
    assert result["load_per_minute"] == pytest.approx(5)


def test_previous_season_is_excluded_even_inside_ninety_days(db, practices):
    practices(99, "2026-06-30", load=99999)
    for session_id, day in [(1, "2026-07-01"), (2, "2026-07-02"), (3, "2026-07-03")]:
        practices(session_id, day)
    practices(4, "2026-07-05", load=2400)
    result = baseline(db, 4)
    assert set(result["session_ids"]) == {1, 2, 3}
    assert result["season_start"] == "2026-07-01"
    assert result["change_pct"] == 100


def test_ninety_day_boundary_is_inclusive_but_older_same_season_is_excluded(
    db, practices
):
    practices(99, "2026-07-07", load=99999)
    practices(1, "2026-07-08")  # Exactly 90 days before the target.
    practices(2, "2026-10-02")
    practices(3, "2026-10-03")
    practices(4, "2026-10-06", load=2400)
    result = baseline(db, 4)
    assert set(result["session_ids"]) == {1, 2, 3}
    assert result["change_pct"] == 100


def test_prior_must_finish_before_target_not_just_start_before_it(db, practices):
    prior_three(practices)
    practices(
        99,
        "2026-10-06",
        start="2026-10-06T17:00:00Z",
        end="2026-10-06T18:30:00Z",
        load=99999,
    )
    practices(4, "2026-10-06", start="2026-10-06T18:00:00Z", end="2026-10-06T20:00:00Z")
    result = baseline(db, 4)
    # A source overlap may conservatively exclude the target itself; under no
    # policy may a partially future predecessor contaminate the comparison.
    assert 99 not in result["session_ids"]
    assert all(point["session_id"] != 99 for point in result["history"])


def test_back_to_back_finished_practice_is_allowed(db, practices):
    practices(1, "2026-10-01")
    practices(2, "2026-10-02")
    practices(3, "2026-10-06", start="2026-10-06T16:00:00Z", end="2026-10-06T18:00:00Z")
    practices(
        4,
        "2026-10-06",
        start="2026-10-06T18:00:00Z",
        end="2026-10-06T20:00:00Z",
        load=2400,
    )
    assert baseline(db, 4)["change_pct"] == 100


def test_exact_six_hour_recording_is_within_comparison_bounds(db, practices):
    prior_three(practices)
    practices(
        4,
        "2026-10-06",
        start="2026-10-06T14:00:00Z",
        end="2026-10-06T20:00:00Z",
        load=2400,
    )
    assert baseline(db, 4)["change_pct"] == 100


def test_missing_end_is_not_synthesized_for_comparison(db, practices):
    prior_three(practices)
    practices(4, "2026-10-06")
    with db.database() as connection:
        connection.execute("UPDATE sessions SET end_utc=NULL WHERE id=4")
    result = baseline(db, 4)
    assert result["reason_code"] == "invalid_bounds"
    assert result["change_pct"] is None


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-03T18:00:00Z", "2026-10-04T01:00:00Z"),
        ("2026-10-03T18:00:00Z", "2026-10-03T17:00:00Z"),
        ("2026-10-03T18:00:00Z", "2026-10-03T18:00:00Z"),
    ],
)
def test_prior_invalid_bounds_do_not_enter_history(db, practices, start, end):
    practices(1, "2026-10-01")
    practices(2, "2026-10-02")
    practices(3, "2026-10-03", start=start, end=end, load=99999)
    practices(4, "2026-10-06")
    result = baseline(db, 4)
    assert result["sample_count"] == 2
    assert result["change_pct"] is None
    assert result["reason_code"] == "insufficient_history"


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-06T18:00:00Z", "2026-10-07T01:00:00Z"),
        ("2026-10-06T18:00:00Z", "2026-10-06T17:00:00Z"),
        ("2099-10-06T18:00:00Z", "2099-10-06T20:00:00Z"),
    ],
)
def test_current_invalid_bounds_block_comparison_with_explicit_reason(
    db, practices, start, end
):
    prior_three(practices)
    practices(4, "2026-10-06", start=start, end=end)
    result = baseline(db, 4)
    assert result["change_pct"] is None
    assert result["reason_code"] == "invalid_bounds"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"legacy": True}, "legacy_current"),
        ({"basis": "legacy_unknown"}, "unknown_exposure_basis"),
        ({"minutes": None}, "missing_current_measurements"),
        ({"load": None}, "missing_current_measurements"),
        ({"minutes": 0}, "nonpositive_current_exposure"),
        ({"removed": True}, "removed_upstream"),
        ({"classification": "game", "labels": ["Game"]}, "not_practice"),
        (
            {"classification": "unknown", "labels": ["Training", "Match"]},
            "not_practice",
        ),
    ],
)
def test_current_record_guard_has_reason_without_fabricated_percentage(
    db, practices, change, reason
):
    prior_three(practices)
    practices(4, "2026-10-06", **change)
    result = baseline(db, 4)
    assert result["change_pct"] is None
    assert result["reason_code"] == reason
    assert result["reason"]


@pytest.mark.parametrize(
    "change",
    [
        {"legacy": True},
        {"basis": "session_duration"},
        {"minutes": None},
        {"load": None},
        {"minutes": 0},
        {"removed": True},
        {"classification": "game", "labels": ["Game"]},
        {"classification": "unknown", "labels": ["Training", "Match"]},
    ],
)
def test_ineligible_prior_record_is_not_replaced_by_zero(db, practices, change):
    practices(1, "2026-10-01")
    practices(2, "2026-10-02")
    practices(3, "2026-10-03", **change)
    practices(4, "2026-10-06")
    result = baseline(db, 4)
    assert result["sample_count"] == 2
    assert result["load_per_minute"] is None
    assert result["change_pct"] is None
    assert set(result["session_ids"]) == {1, 2}


@pytest.mark.parametrize("current_load", [0, 600])
def test_real_zero_baseline_has_rate_zero_but_no_undefined_percentage(
    db, practices, current_load
):
    prior_three(practices, load=0)
    practices(4, "2026-10-06", load=current_load)
    result = baseline(db, 4)
    assert result["sample_count"] == 3
    assert result["load_per_minute"] == 0
    assert result["change_pct"] is None
    assert result["reason_code"] == "zero_baseline"


def test_real_zero_current_load_produces_minus_one_hundred_percent(db, practices):
    prior_three(practices)
    practices(4, "2026-10-06", load=0)
    assert baseline(db, 4)["change_pct"] == -100


def test_individual_validity_does_not_require_whole_session_completeness(db, practices):
    prior_three(practices, status="partial")
    practices(4, "2026-10-06", status="partial", load=2400)
    report = db.get_report(4)
    assert report["coverage"]["complete"] is False
    assert report["players"][0]["baseline"]["change_pct"] == 100


def test_imported_session_provenance_blocks_even_if_player_legacy_flag_is_missing(
    db, practices
):
    prior_three(practices)
    practices(4, "2026-10-06")
    with db.database() as connection:
        connection.execute(
            "UPDATE sessions SET source_json=%s WHERE id=4",
            (db.jsonb({"import": "legacy"}),),
        )
    assert baseline(db, 4)["reason_code"] == "legacy_current"


def test_finite_input_whose_rate_overflows_never_publishes_infinite_comparison(
    db, practices, seed
):
    prior_three(practices)
    practices(4, "2026-10-06")
    seed.stats(4, 11, minutes=1e-308, mechanical_load=1e308, load_per_minute=0)
    result = baseline(db, 4)
    assert result["change_pct"] is None
    assert result["reason_code"] == "measurement_out_of_range"


def test_comparison_history_changes_revise_report_once_deterministically(db, practices):
    prior_three(practices)
    practices(4, "2026-10-06", load=2400)
    initial = db.get_report(4)
    assert db.get_report(4)["version"] == initial["version"]
    practices(3, "2026-10-03", load=2400)
    changed = db.get_report(4)
    assert changed["version"] == initial["version"] + 1
    assert changed["players"][0]["baseline"]["load_per_minute"] == pytest.approx(80 / 3)
    assert db.get_report(4)["version"] == changed["version"]


def test_gt_game_interval_is_separate_from_source_practice_label(db, practices):
    identity = db.digest(
        {"base_url": "https://georgia-tech-mccamish.access.kinexon.com", "team_id": 3}
    )
    with db.database() as connection:
        connection.execute(
            "INSERT INTO meta(key,value) VALUES('source_identity',%s)", (identity,)
        )
    for session_id, day in [(1, "2026-03-01"), (2, "2026-03-02"), (3, "2026-03-03")]:
        practices(session_id, day, start=day + "T14:00:00Z", end=day + "T16:00:00Z")
    practices(
        4,
        "2026-03-04",
        start="2026-03-04T14:00:00Z",
        end="2026-03-04T16:00:00Z",
        load=2400,
    )
    morning = db.get_report(4)
    assert morning["session"]["classification"] == "practice"
    assert morning["players"][0]["baseline"]["change_pct"] == 100
    practices(
        5,
        "2026-03-04",
        start="2026-03-04T22:28:00Z",
        end="2026-03-05T02:10:00Z",
        load=2400,
    )
    evening = db.get_report(5)
    assert evening["session"]["classification"] == "practice"
    assert evening["players"][0]["baseline"]["change_pct"] is None
    assert evening["players"][0]["baseline"]["reason_code"] == "schedule_conflict"


def test_gt_schedule_is_never_applied_to_different_source_identity(db, practices):
    identity = db.digest({"base_url": "https://synthetic-source.example", "team_id": 7})
    with db.database() as connection:
        connection.execute(
            "INSERT INTO meta(key,value) VALUES('source_identity',%s)", (identity,)
        )
    for session_id, day in [(1, "2026-03-01"), (2, "2026-03-02"), (3, "2026-03-03")]:
        practices(session_id, day, start=day + "T14:00:00Z", end=day + "T16:00:00Z")
    practices(
        4,
        "2026-03-04",
        start="2026-03-04T22:28:00Z",
        end="2026-03-05T02:10:00Z",
        load=2400,
    )
    assert baseline(db, 4)["change_pct"] == 100


def test_explicit_practice_review_resolves_label_conflict_but_not_invalid_bounds(
    db, practices
):
    prior_three(practices)
    practices(4, "2026-10-06", labels=["Training", "Match"], load=2400)
    assert baseline(db, 4)["reason_code"] == "source_conflict"
    resolved = db.review_session(
        4, "practice", "Synthetic staff confirmed the recording was practice"
    )
    assert resolved["players"][0]["baseline"]["change_pct"] == 100
    with db.database() as connection:
        connection.execute(
            "UPDATE sessions SET end_utc='2026-10-07T01:00:00Z' WHERE id=4"
        )
    assert baseline(db, 4)["reason_code"] == "invalid_bounds"


def test_source_policy_migration_preserves_latest_manual_unknown_and_overrides(
    db, seed, monkeypatch
):
    from alembic import command
    from alembic.config import Config
    from pg_helpers import isolated_schema
    from server.app import migrate

    # Build an earlier revision inside a second generated synthetic schema. Never
    # downgrade the application, public schema, or even this test's normal schema.
    with isolated_schema() as (url, schema), monkeypatch.context() as scoped:
        scoped.setenv("DATABASE_URL", url)
        scoped.setenv("VIPMBB_DB_SCHEMA", schema)
        config = Config(str(db.ROOT / "server" / "alembic.ini"))
        command.upgrade(config, "0002_unknown_timestamps")
        seed.session(
            1,
            reviewed=False,
            classification="unknown",
            labels=["Training"],
            start="2026-03-04T22:28:00Z",
            end="2026-03-05T02:10:00Z",
        )
        seed.session(2, reviewed=False, classification="unknown", labels=["Match"])
        seed.session(
            3, reviewed=False, classification="practice", labels=["Training", "Match"]
        )
        seed.session(4, reviewed=True, classification="game", labels=["Training"])
        seed.session(5, reviewed=False, classification="unknown", labels=["Training"])
        with db.database() as connection:
            connection.execute(
                "INSERT INTO reviews(session_id,classification,reason) VALUES(5,'game','Earlier synthetic review')"
            )
            connection.execute(
                "INSERT INTO reviews(session_id,classification,reason) VALUES(5,'unknown','Latest synthetic review is deliberately unresolved')"
            )
        migrate.upgrade()
        with db.database() as connection:
            rows = {
                row["id"]: row
                for row in connection.execute(
                    "SELECT id,classification,reviewed FROM sessions"
                )
            }
        assert rows[1]["classification"] == "practice" and rows[1]["reviewed"] is False
        assert rows[2]["classification"] == "game" and rows[2]["reviewed"] is False
        assert rows[3]["classification"] == "unknown" and rows[3]["reviewed"] is False
        assert rows[4]["classification"] == "game" and rows[4]["reviewed"] is True
        assert rows[5]["classification"] == "unknown" and rows[5]["reviewed"] is True
