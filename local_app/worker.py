"""Single laptop worker with durable jobs and catch-up polling after sleep/restart."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
import signal
import time

from . import data
from .sync import SyncError, credentials_configured, sync_range


def heartbeat():
    with data.database() as connection:
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('worker_heartbeat',?)", (data.utcnow(),))


def worker_status():
    with data.database() as connection:
        row = connection.execute("SELECT value FROM meta WHERE key='worker_heartbeat'").fetchone()
    stamp = data.timestamp(row[0]) if row else None
    return {"running": bool(stamp and (datetime.now(timezone.utc) - stamp).total_seconds() < 90), "last_heartbeat": row[0] if row else None}


def process_one():
    """Claim one queued job atomically. Returns False when the queue is empty."""
    with data.database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY CASE kind WHEN 'sync' THEN 1 ELSE 0 END,id LIMIT 1").fetchone()
        if row is None:
            return False
        job = dict(row)
        connection.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated_at=?,message=? WHERE id=?", (data.utcnow(), "Processing locally.", job["id"]))
    payload = json.loads(job["payload_json"])
    try:
        if job["kind"] == "sync":
            result = sync_range(payload.get("start"), payload.get("end"))
            if not result["complete"]:
                message = f"Partial sync: {result['failure_count']} requests failed. Available data was saved; retry this date range."
                state = "failed"
            else:
                message = f"Synced {result['sessions']} recordings. Report jobs are queued."
                state = "completed"
        elif job["kind"] == "report":
            result = data.get_report(int(payload["session_id"]))
            result = {"session_id": result["session"]["id"], "version": result["version"]}
            message, state = "Report snapshot is ready.", "completed"
        elif job["kind"] in ("index", "embeddings"):
            from .knowledge import index_documents
            result = index_documents()
            message, state = "Knowledge index was refreshed locally.", "completed"
        else:
            raise ValueError("Unsupported job kind.")
    except (SyncError, ValueError, KeyError) as error:
        # Only our bounded application exceptions are safe; never stringify request
        # exceptions, which can include URLs containing the API key.
        result, state = None, "failed"
        message = str(error) if isinstance(error, SyncError) else "The job could not complete because its input or source record was invalid."
    except Exception:
        result, state = None, "failed"
        message = "The local job failed unexpectedly. Existing data was retained; retry or inspect local setup."
    with data.database() as connection:
        connection.execute("UPDATE jobs SET status=?,message=?,updated_at=?,result_json=? WHERE id=?", (state, message, data.utcnow(), data.canonical(result) if result is not None else None, job["id"]))
    return True


def recover_interrupted_jobs():
    """Called only after obtaining the exclusive worker lock."""
    with data.database() as connection:
        connection.execute("UPDATE jobs SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,message='Interrupted worker recovered; safe retry queued when retry budget permits.',updated_at=? WHERE status='running'", (data.utcnow(),))


def schedule_poll(interval_seconds=900):
    if not credentials_configured():
        return False
    with data.database() as connection:
        row = connection.execute("SELECT value FROM meta WHERE key='worker_last_poll'").fetchone()
        stamp = data.timestamp(row[0]) if row else None
        checkpoint = connection.execute("SELECT value FROM meta WHERE key='last_sync_end'").fetchone()
    now = datetime.now(timezone.utc)
    if stamp and (now - stamp).total_seconds() < interval_seconds:
        return False
    # Recent overlap catches late uploads/corrections; historical backfill is explicit.
    today = now.astimezone(data.ATLANTA).date()
    start = today - timedelta(days=13)
    if checkpoint:
        from datetime import date
        try:
            start = min(start, date.fromisoformat(checkpoint[0]) - timedelta(days=13))
        except ValueError:
            pass
    # A laptop can be offline for more than a year. Queue bounded consecutive
    # slices instead of silently discarding the older part of a catch-up gap.
    while start <= today:
        end = min(start + timedelta(days=366), today)
        data.enqueue_job("sync", {"start": start.isoformat(), "end": end.isoformat()})
        start = end + timedelta(days=1)
    with data.database() as connection:
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('worker_last_poll',?)", (now.isoformat(),))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process the current queue without scheduling a network poll.")
    args = parser.parse_args()
    data.initialize()
    lock = open(data.data_dir() / "worker.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("A local worker is already running.")
        return 0
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    recover_interrupted_jobs()
    # Heartbeats must continue during long imports so the UI doesn't misreport a
    # healthy worker as stopped. This thread writes only small metadata transactions.
    import threading
    done = threading.Event()
    def keep_alive():
        while not done.wait(15):
            try:
                heartbeat()
            except Exception:
                pass
    thread = threading.Thread(target=keep_alive, daemon=True)
    heartbeat()
    thread.start()
    try:
        while not stopped:
            if not args.once and os.environ.get("VIPMBB_DISABLE_AUTO_SYNC") != "1":
                schedule_poll()
            if process_one():
                continue
            if args.once:
                break
            done.wait(2)
    finally:
        done.set()
        with data.database() as connection:
            connection.execute("DELETE FROM meta WHERE key='worker_heartbeat'")
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
