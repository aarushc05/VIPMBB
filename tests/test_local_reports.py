"""Numerical and provenance regressions using tiny, inspectable datasets."""

import pytest


def report(session_id):
    from server.app.analytics import get_report

    return get_report(session_id)


def rows_by_id(result):
    return {row["id"]: row for row in result["players"]}


def test_report_aggregates_every_recorded_player(practice):
    result = report(practice)
    assert result["summary"]["players"] == 2
    assert result["summary"]["minutes_total"] == pytest.approx(90)
    assert result["summary"]["distance_m"] == pytest.approx(4800)
    assert result["summary"]["mechanical_load"] == pytest.approx(1500)
    assert set(rows_by_id(result)) == {11, 22}
    assert rows_by_id(result)[11]["metrics"]["load_per_minute"] == pytest.approx(20)
    assert rows_by_id(result)[22]["metrics"]["load_per_minute"] == pytest.approx(10)


def test_missing_does_not_become_zero_and_real_zero_is_preserved(practice):
    result = report(practice)
    players = rows_by_id(result)
    assert result["summary"]["accel_load"] is None
    assert players[11]["metrics"]["jump_count"] == 0
    assert "jump_count" not in players[11]["missing_metrics"]
    assert players[22]["metrics"]["jump_count"] is None
    assert "jump_count" in players[22]["missing_metrics"]


def test_zero_exposure_never_divides_or_invents_intensity(seed):
    seed.player(11, "Alex Rivera")
    seed.session(101, expected_players=1)
    seed.stats(101, 11, minutes=0, mechanical_load=100)
    result = report(101)
    assert rows_by_id(result)[11]["metrics"]["load_per_minute"] is None


def test_unrecorded_assigned_player_is_visible_as_coverage_gap(seed):
    seed.player(11, "Alex Rivera")
    seed.player(22, "Blake Chen")
    seed.session(101, expected_players=2, status="partial")
    seed.stats(101, 11)
    result = report(101)
    assert result["coverage"]["recorded_players"] == 1
    assert result["coverage"]["expected_players"] == 2
    assert result["coverage"]["complete"] is False
    assert result["warnings"] or result["coverage"]["notes"]


def test_inactive_historical_player_is_not_removed_from_report(seed):
    seed.player(11, "Former Player", active=False)
    seed.session(101, expected_players=1)
    seed.stats(101, 11)
    assert 11 in rows_by_id(report(101))


def test_human_classification_does_not_claim_complete_data(seed):
    from server.app.data import review_session

    seed.player(11, "Alex Rivera")
    seed.session(
        101,
        classification="unknown",
        reviewed=False,
        expected_players=2,
        status="partial",
    )
    seed.stats(101, 11)
    review_session(101, "practice", "Staff confirmed this was practice")
    result = report(101)
    assert result["session"]["classification"] == "practice"
    assert result["session"]["reviewed"]
    assert result["coverage"]["complete"] is False


def test_report_generation_is_idempotent_and_source_change_revises(practice, seed):
    from server.app.data import regenerate_report

    first = report(practice)
    again = report(practice)
    manual = regenerate_report(practice)
    assert first["version"] == again["version"] == manual["version"]
    seed.stats(practice, 11, minutes=60, mechanical_load=2400, distance_m=3000)
    revised = regenerate_report(practice)
    assert revised["version"] == first["version"] + 1
    assert revised["summary"]["mechanical_load"] == pytest.approx(2700)


def test_review_change_revises_report(practice):
    from server.app.data import review_session, regenerate_report

    initial = report(practice)
    review_session(practice, "game", "Staff identified mislabeled game")
    revised = regenerate_report(practice)
    assert revised["version"] == initial["version"] + 1
    assert revised["session"]["classification"] == "game"


def test_baseline_excludes_future_current_games_and_unreviewed(seed):
    seed.player(11, "Alex Rivera")
    for id, date, reviewed, classification, load in [
        (1, "2026-10-01", True, "practice", 600),
        (2, "2026-10-02", True, "practice", 1200),
        (3, "2026-10-03", False, "practice", 99999),
        (4, "2026-10-04", True, "game", 99999),
        (5, "2026-10-06", True, "practice", 1800),
        (6, "2026-10-07", True, "practice", 99999),
    ]:
        seed.session(
            id,
            date,
            reviewed=reviewed,
            classification=classification,
            expected_players=1,
        )
        seed.stats(id, 11, minutes=60, mechanical_load=load)
    baseline = rows_by_id(report(5))[11]["baseline"]
    assert baseline["sample_count"] == 2
    assert baseline["load_per_minute"] is None
    assert baseline["change_pct"] is None


def test_future_same_day_practice_cannot_enter_baseline(seed):
    seed.player(11, "Alex Rivera")
    seed.session(
        1,
        start="2026-10-06T12:00:00+00:00",
        end="2026-10-06T13:00:00+00:00",
        expected_players=1,
    )
    seed.session(
        2,
        start="2026-10-06T19:00:00+00:00",
        end="2026-10-06T20:00:00+00:00",
        expected_players=1,
    )
    seed.stats(1, 11, mechanical_load=600)
    seed.stats(2, 11, mechanical_load=99999)
    assert rows_by_id(report(1))[11]["baseline"]["sample_count"] == 0


def test_missing_player_load_never_enters_baseline_as_zero(seed):
    seed.player(11, "Alex Rivera")
    seed.session(1, "2026-10-01", expected_players=1)
    seed.session(2, "2026-10-06", expected_players=1)
    seed.stats(1, 11, mechanical_load=None)
    seed.stats(2, 11)
    assert rows_by_id(report(2))[11]["baseline"]["sample_count"] == 0


def test_baseline_requires_matching_exposure_definition(seed):
    seed.player(11, "Alex Rivera")
    seed.session(1, "2026-10-01", expected_players=1)
    seed.session(2, "2026-10-06", expected_players=1)
    seed.stats(1, 11, exposure_basis="whole_session_wall_clock")
    seed.stats(2, 11, exposure_basis="tracked_player_minutes")
    assert rows_by_id(report(2))[11]["baseline"]["sample_count"] == 0


def test_valid_three_practice_baseline_uses_observed_intensities(seed):
    seed.player(11, "Alex Rivera")
    for sid, day, minutes, load in [
        (1, "2026-10-01", 30, 300),
        (2, "2026-10-02", 60, 1200),
        (3, "2026-10-03", 60, 1800),
        (4, "2026-10-06", 60, 2400),
    ]:
        seed.session(sid, day, expected_players=1)
        seed.stats(sid, 11, minutes=minutes, mechanical_load=load)
    baseline = rows_by_id(report(4))[11]["baseline"]
    assert baseline["sample_count"] == 3
    assert baseline["load_per_minute"] is not None
    assert baseline["load_per_minute"] == pytest.approx(22)
    assert baseline["change_pct"] == pytest.approx(
        (40 / baseline["load_per_minute"] - 1) * 100
    )


def test_unreviewed_report_has_explicit_verification_warning(seed):
    seed.player(11, "Alex Rivera")
    seed.session(101, classification="unknown", reviewed=False, expected_players=1)
    seed.stats(101, 11)
    result = report(101)
    assert result["session"]["reviewed"] is False or result["session"]["reviewed"] == 0
    assert result["warnings"]


def test_unknown_report_is_not_silently_created(db):
    with pytest.raises((ValueError, KeyError, LookupError)):
        report(999999)


def test_complete_flags_do_not_hide_participant_count_inconsistency(db, seed):
    seed.player(11, "Alex Rivera")
    seed.session(101, expected_players=2)
    seed.stats(101, 11)
    with db.database() as conn:
        conn.execute(
            "UPDATE sessions SET assignment_complete=TRUE,sync_complete=TRUE WHERE id=101"
        )
        conn.execute("INSERT INTO assignments(session_id,player_id) VALUES(101,11)")
    assert report(101)["coverage"]["complete"] is False


def test_source_reverting_to_old_value_still_has_monotonic_revision(practice, seed):
    first = report(practice)
    seed.stats(
        practice, 11, minutes=60, mechanical_load=2400, distance_m=3000, jump_count=0
    )
    second = report(practice)
    seed.stats(
        practice, 11, minutes=60, mechanical_load=1200, distance_m=3000, jump_count=0
    )
    third = report(practice)
    assert second["version"] > first["version"]
    assert third["version"] > second["version"]
    assert third["summary"]["mechanical_load"] == first["summary"]["mechanical_load"]


def test_updating_transport_timestamp_alone_does_not_revise(db, practice):
    first = report(practice)
    with db.database() as conn:
        conn.execute(
            "UPDATE sessions SET updated_at='2026-10-08T12:00:00Z' WHERE id=%s",
            (practice,),
        )
        conn.execute(
            "UPDATE stats SET updated_at='2026-10-08T12:00:00Z' WHERE session_id=%s",
            (practice,),
        )
    assert report(practice)["version"] == first["version"]


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-06T18:00:00Z", "2026-10-07T20:00:00Z"),
        ("2026-10-06T18:00:00Z", "2026-10-06T17:00:00Z"),
        ("2099-10-06T18:00:00Z", "2099-10-06T20:00:00Z"),
    ],
)
def test_invalid_or_future_recording_never_gets_complete_report(db, seed, start, end):
    seed.player(11, "Alex Rivera")
    seed.session(101, start=start, end=end, expected_players=1)
    seed.stats(101, 11)
    with db.database() as conn:
        conn.execute(
            "UPDATE sessions SET assignment_complete=TRUE,sync_complete=TRUE WHERE id=101"
        )
        conn.execute("INSERT INTO assignments(session_id,player_id) VALUES(101,11)")
    result = report(101)
    assert result["coverage"]["complete"] is False
    assert result["session"]["status"] == "preliminary"


@pytest.mark.parametrize("classification", ["game", "unknown"])
def test_nonpractice_target_never_compares_against_practice_baseline(
    seed, classification
):
    seed.player(11, "Alex Rivera")
    for sid, day in [(1, "2026-10-01"), (2, "2026-10-02"), (3, "2026-10-03")]:
        seed.session(sid, day, expected_players=1)
        seed.stats(sid, 11)
    seed.session(4, "2026-10-06", classification=classification, expected_players=1)
    seed.stats(4, 11, mechanical_load=99999)
    baseline = rows_by_id(report(4))[11]["baseline"]
    assert baseline["sample_count"] == 0
    assert baseline["change_pct"] is None
    assert baseline["load_per_minute"] is None


def test_reclassifying_practice_as_game_removes_prior_comparison(db, seed):
    seed.player(11, "Alex Rivera")
    for sid, day in [
        (1, "2026-10-01"),
        (2, "2026-10-02"),
        (3, "2026-10-03"),
        (4, "2026-10-06"),
    ]:
        seed.session(sid, day, expected_players=1)
        seed.stats(sid, 11, mechanical_load=600 if sid < 4 else 1200)
    original = report(4)
    assert rows_by_id(original)[11]["baseline"]["change_pct"] == 100
    changed = db.review_session(4, "game", "Staff corrected activity type")
    assert changed["version"] > original["version"]
    assert rows_by_id(changed)[11]["baseline"]["change_pct"] is None


@pytest.mark.parametrize("supplied_rate", [None, 999])
def test_phase_intensity_is_recomputed_from_load_and_exposure(
    db, practice, supplied_rate
):
    from pg_helpers import json_parameter

    with db.database() as conn:
        conn.execute(
            "INSERT INTO phases(id,session_id,title,valid) VALUES(201,%s,'Synthetic drill',TRUE)",
            (practice,),
        )
        conn.execute(
            "INSERT INTO phase_stats(phase_id,player_id,metrics_json) VALUES(201,11,%s)",
            (
                json_parameter(
                    {
                        "minutes": 20,
                        "mechanical_load": 250,
                        "load_per_minute": supplied_rate,
                        "exposure_basis": "on_playing_field",
                    }
                ),
            ),
        )
    metrics = report(practice)["drills"][0]["players"][0]["metrics"]
    assert metrics["load_per_minute"] == 12.5
