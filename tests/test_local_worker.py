from datetime import datetime, timedelta, timezone


def job(db, job_id):
    return next(row for row in db.list_jobs()["jobs"] if row["id"] == job_id)


def test_empty_queue_does_not_claim_work(db):
    from server.app import worker

    assert worker.process_one() is False


def test_report_job_completes_once_and_persists_version(db, practice):
    from server.app import worker

    pending = db.enqueue_job("report", {"session_id": practice})
    assert worker.process_one() is True
    result = job(db, pending["id"])
    assert result["status"] == "completed"
    assert result["attempts"] == 1
    assert result["result"]["version"] == 1
    assert worker.process_one() is False


def test_partial_sync_job_is_not_marked_success(db, monkeypatch):
    from server.app import worker

    monkeypatch.setattr(
        worker,
        "sync_range",
        lambda *args, **kwargs: {"complete": False, "failure_count": 1, "sessions": 2},
    )
    pending = db.enqueue_job("sync", {})
    worker.process_one()
    result = job(db, pending["id"])
    assert result["status"] == "failed"
    assert "Partial" in result["message"]
    assert result["result"]["complete"] is False
    retry = db.enqueue_job("sync", {})
    assert retry["id"] != pending["id"]
    monkeypatch.setattr(
        worker, "sync_range", lambda *args, **kwargs: {"complete": True, "sessions": 2}
    )
    worker.process_one()
    assert job(db, retry["id"])["status"] == "completed"


def test_interrupted_jobs_recover_with_bounded_attempts(db):
    from server.app import worker

    first = db.enqueue_job("report", {"session_id": 1})
    exhausted = db.enqueue_job("report", {"session_id": 2})
    with db.database() as conn:
        conn.execute(
            "UPDATE jobs SET status='running',attempts=1 WHERE id=%s", (first["id"],)
        )
        conn.execute(
            "UPDATE jobs SET status='running',attempts=3 WHERE id=%s",
            (exhausted["id"],),
        )
    worker.recover_interrupted_jobs()
    assert job(db, first["id"])["status"] == "queued"
    assert job(db, exhausted["id"])["status"] == "failed"


def test_unexpected_failure_message_redacts_exception_text(db, monkeypatch):
    from server.app import worker

    def fail(*args, **kwargs):
        raise RuntimeError("https://host/?apiKey=private-secret")

    monkeypatch.setattr(worker, "sync_range", fail)
    pending = db.enqueue_job("sync", {})
    worker.process_one()
    result = job(db, pending["id"])
    assert result["status"] == "failed"
    assert "private-secret" not in result["message"]


def test_poll_without_credentials_does_not_create_fake_sync(db, monkeypatch):
    from server.app import worker

    monkeypatch.setattr(worker, "credentials_configured", lambda: False)
    assert worker.schedule_poll() is False
    assert db.list_jobs()["jobs"] == []


def test_poll_catches_up_from_checkpoint_and_does_not_reschedule_immediately(
    db, monkeypatch
):
    from server.app import worker

    monkeypatch.setattr(worker, "credentials_configured", lambda: True)
    today = datetime.now(db.ATLANTA).date()
    checkpoint = today - timedelta(days=40)
    with db.database() as conn:
        conn.execute(
            "INSERT INTO meta VALUES('last_sync_end',%s)", (checkpoint.isoformat(),)
        )
    assert worker.schedule_poll() is True
    pending = db.list_jobs()["jobs"][0]
    assert pending["payload"]["start"] == (checkpoint - timedelta(days=13)).isoformat()
    assert pending["payload"]["end"] == today.isoformat()
    assert worker.schedule_poll() is False
    assert len(db.list_jobs()["jobs"]) == 1


def test_stale_worker_heartbeat_is_reported_offline(db):
    from server.app import worker

    assert worker.worker_status()["running"] is False
    worker.heartbeat()
    assert worker.worker_status()["running"] is True
    with db.database() as conn:
        conn.execute(
            "UPDATE meta SET value=%s WHERE key='worker_heartbeat'",
            ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),),
        )
    assert worker.worker_status()["running"] is False


def test_long_catchup_chunks_cover_entire_gap_without_overlap(db, monkeypatch):
    from server.app import worker

    monkeypatch.setattr(worker, "credentials_configured", lambda: True)
    today = datetime.now(db.ATLANTA).date()
    checkpoint = today - timedelta(days=800)
    with db.database() as conn:
        conn.execute(
            "INSERT INTO meta VALUES('last_sync_end',%s)", (checkpoint.isoformat(),)
        )
    assert worker.schedule_poll() is True
    ranges = sorted(
        (row["payload"] for row in db.list_jobs()["jobs"]),
        key=lambda value: value["start"],
    )
    assert len(ranges) == 3
    assert ranges[0]["start"] == (checkpoint - timedelta(days=13)).isoformat()
    assert ranges[-1]["end"] == today.isoformat()
    for index, span in enumerate(ranges):
        first = datetime.fromisoformat(span["start"]).date()
        last = datetime.fromisoformat(span["end"]).date()
        assert 0 <= (last - first).days <= 366
        if index:
            previous_end = datetime.fromisoformat(ranges[index - 1]["end"]).date()
            assert first == previous_end + timedelta(days=1)
