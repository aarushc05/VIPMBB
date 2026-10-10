"""Real PostgreSQL transaction, claim, fencing and scheduling regressions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest


def test_parallel_report_reads_create_one_version(db, practice, seed):
    with ThreadPoolExecutor(max_workers=6) as pool:
        reports = list(pool.map(lambda _: db.get_report(practice), range(12)))
    assert {report["version"] for report in reports} == {1}
    seed.stats(practice, 11, mechanical_load=1500)
    with ThreadPoolExecutor(max_workers=6) as pool:
        reports = list(pool.map(lambda _: db.get_report(practice), range(12)))
    assert {report["version"] for report in reports} == {2}
    with db.database() as connection:
        assert (
            connection.execute("SELECT COUNT(*) AS count FROM reports").fetchone()[
                "count"
            ]
            == 2
        )


def test_parallel_duplicate_enqueue_has_one_active_job(db):
    def enqueue(index):
        payload = (
            {"start": "2026-10-01", "end": "2026-10-06"}
            if index % 2
            else {"end": "2026-10-06", "start": "2026-10-01"}
        )
        return db.enqueue_job("sync", payload)

    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = list(pool.map(enqueue, range(16)))
    assert len({row["id"] for row in pending}) == 1
    assert len(db.list_jobs()["jobs"]) == 1


def test_skip_locked_claims_every_job_exactly_once(db):
    from server.app import worker

    expected = {
        db.enqueue_job("report", {"session_id": sid})["id"] for sid in range(12)
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(
            pool.map(lambda i: worker.claim_job(f"synthetic-worker-{i}"), range(24))
        )
    actual = [row["id"] for row in claimed if row]
    assert len(actual) == len(set(actual)) == len(expected)
    assert set(actual) == expected
    assert all(row["attempts"] == 1 for row in claimed if row)


def test_live_lease_survives_recovery_and_expired_owner_cannot_commit(db):
    from server.app import worker

    pending = db.enqueue_job("report", {"session_id": 101})
    old = worker.claim_job("synthetic-owner-a")
    worker.recover_interrupted_jobs()
    assert worker.claim_job("synthetic-owner-b") is None
    assert worker.renew_lease(old)
    with db.database() as connection:
        connection.execute(
            "UPDATE jobs SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE id=%s",
            (pending["id"],),
        )
    assert worker.renew_lease(old) is False
    assert worker.finish_job(old, "completed", "Stale completion") is False
    worker.recover_interrupted_jobs()
    new = worker.claim_job("synthetic-owner-b")
    assert new["id"] == old["id"] and new["claim_token"] != old["claim_token"]
    assert new["attempts"] == 2
    assert worker.finish_job(old, "failed", "Must not overwrite live owner") is False
    assert worker.finish_job(new, "completed", "Synthetic completed", {"ok": True})


def test_sync_transaction_rolls_back_when_lease_expires_before_commit(db):
    from server.app import worker, sync

    db.enqueue_job("sync", {})
    claim = worker.claim_job("synthetic-owner")
    with pytest.raises(sync.LeaseLost):
        with sync._database(claim) as connection:
            connection.execute(
                "INSERT INTO players(id,name) VALUES(123,'Synthetic fenced player')"
            )
            connection.execute(
                "UPDATE jobs SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE id=%s",
                (claim["id"],),
            )
    with db.database() as connection:
        assert (
            connection.execute("SELECT COUNT(*) AS count FROM players").fetchone()[
                "count"
            ]
            == 0
        )


def test_old_source_token_cannot_write_after_owner_replacement(db):
    from server.app import sync

    with db.database() as connection:
        connection.execute(
            "INSERT INTO meta(key,value) VALUES('source_sync_token','new-owner')"
        )
    with pytest.raises(sync.LeaseLost):
        with sync._database(source_token="old-owner") as connection:
            connection.execute(
                "INSERT INTO players(id,name) VALUES(123,'Synthetic stale source')"
            )
    with db.database() as connection:
        assert (
            connection.execute("SELECT COUNT(*) AS count FROM players").fetchone()[
                "count"
            ]
            == 0
        )


def test_transaction_exception_does_not_leave_partial_rows(db):
    with pytest.raises(RuntimeError):
        with db.database() as connection:
            connection.execute(
                "INSERT INTO players(id,name) VALUES(123,'Synthetic rolled-back player')"
            )
            raise RuntimeError("synthetic interruption")
    with db.database() as connection:
        assert (
            connection.execute("SELECT COUNT(*) AS count FROM players").fetchone()[
                "count"
            ]
            == 0
        )


def test_scheduler_retries_older_gap_even_after_newer_success(db, monkeypatch):
    from server.app import worker

    monkeypatch.setattr(worker, "credentials_configured", lambda: True)
    today = datetime.now(db.ATLANTA).date()
    anchor = today - timedelta(days=60)
    gap_start, gap_end = anchor + timedelta(days=10), anchor + timedelta(days=14)
    with db.database() as connection:
        connection.execute(
            "INSERT INTO meta VALUES('auto_sync_start',%s)", (anchor.isoformat(),)
        )
        for first, last, state in [
            (anchor, gap_start - timedelta(days=1), "completed"),
            (gap_start, gap_end, "partial"),
            (gap_end + timedelta(days=1), today, "completed"),
        ]:
            connection.execute(
                "INSERT INTO sync_runs(start_date,end_date,status,started_at,finished_at,failure_count) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    first,
                    last,
                    state,
                    datetime.now(timezone.utc) - timedelta(hours=1),
                    datetime.now(timezone.utc),
                    int(state != "completed"),
                ),
            )
    assert worker.schedule_poll()
    ranges = [
        (row["payload"]["start"], row["payload"]["end"])
        for row in db.list_jobs()["jobs"]
    ]
    assert (gap_start.isoformat(), gap_end.isoformat()) in ranges
    assert ((today - timedelta(days=13)).isoformat(), today.isoformat()) in ranges
    assert len(ranges) == 2


def test_covered_historical_failure_does_not_requeue(db, monkeypatch):
    from server.app import worker

    monkeypatch.setattr(worker, "credentials_configured", lambda: True)
    today = datetime.now(db.ATLANTA).date()
    start, end = today - timedelta(days=100), today - timedelta(days=90)
    with db.database() as connection:
        connection.execute(
            "INSERT INTO sync_runs(start_date,end_date,status,started_at,finished_at,failure_count) VALUES(%s,%s,'failed',clock_timestamp()-INTERVAL '2 hours',clock_timestamp()-INTERVAL '1 hour',1)",
            (start, end),
        )
        connection.execute(
            "INSERT INTO sync_runs(start_date,end_date,status,started_at,finished_at) VALUES(%s,%s,'completed',clock_timestamp()-INTERVAL '30 minutes',clock_timestamp())",
            (start, end),
        )
    assert worker.schedule_poll()
    ranges = [row["payload"] for row in db.list_jobs()["jobs"]]
    assert len(ranges) == 1
    assert ranges[0]["start"] == (today - timedelta(days=13)).isoformat()
