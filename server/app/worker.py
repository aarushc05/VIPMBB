"""PostgreSQL-backed ingest worker with renewable leases and gap-aware scheduling."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import os
import signal
import socket
import threading
from uuid import uuid4

from . import data
from .sync import LeaseLost, SourceBusy, SyncError, credentials_configured, sync_range

MAX_ATTEMPTS = 3
LEASE_SECONDS = 120
WORKER_LOCK = "ingest-worker"
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"


def _meta(connection, key, value):
    connection.execute(
        "INSERT INTO meta(key,value) VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def heartbeat(worker_id=None):
    with data.database() as connection:
        _meta(connection, "worker_heartbeat", data.utcnow())
        _meta(connection, "worker_id", worker_id or WORKER_ID)
        # This non-secret flag lets the API report setup without mounting credentials.
        _meta(
            connection,
            "credentials_configured",
            "true" if credentials_configured() else "false",
        )


def worker_status():
    with data.database() as connection:
        rows = connection.execute(
            "SELECT key,value FROM meta WHERE key IN ('worker_heartbeat','credentials_configured')"
        ).fetchall()
    values = {row["key"]: row["value"] for row in rows}
    stamp = data.timestamp(values.get("worker_heartbeat"))
    return {
        "running": bool(
            stamp and (datetime.now(timezone.utc) - stamp).total_seconds() < 90
        ),
        "last_heartbeat": values.get("worker_heartbeat"),
        "credentials_configured": values.get("credentials_configured") == "true",
    }


def claim_job(worker_id=None, lease_seconds=LEASE_SECONDS):
    """Claim in a short transaction; no network or report work holds this lock."""
    with data.database() as connection:
        row = connection.execute(
            """SELECT id FROM jobs WHERE status='queued' AND attempts<%s
               ORDER BY CASE kind WHEN 'sync' THEN 1 ELSE 0 END,id
               FOR UPDATE SKIP LOCKED LIMIT 1""",
            (MAX_ATTEMPTS,),
        ).fetchone()
        if row is None:
            return None
        return connection.execute(
            """UPDATE jobs SET status='running',attempts=attempts+1,worker_id=%s,claim_token=%s,
               lease_until=clock_timestamp()+(%s * INTERVAL '1 second'),updated_at=clock_timestamp(),
               message='Processing.' WHERE id=%s RETURNING *""",
            (worker_id or WORKER_ID, uuid4().hex, lease_seconds, row["id"]),
        ).fetchone()


def renew_lease(job, lease_seconds=LEASE_SECONDS):
    with data.database() as connection:
        cursor = connection.execute(
            """UPDATE jobs SET lease_until=clock_timestamp()+(%s * INTERVAL '1 second'),updated_at=clock_timestamp()
               WHERE id=%s AND status='running' AND worker_id=%s AND claim_token=%s
               AND lease_until>clock_timestamp()""",
            (lease_seconds, job["id"], job["worker_id"], job["claim_token"]),
        )
        return cursor.rowcount == 1


def finish_job(job, state, message, result=None):
    if state not in {"completed", "failed"}:
        raise ValueError("Unsupported final job state.")
    with data.database() as connection:
        cursor = connection.execute(
            """UPDATE jobs SET status=%s,message=%s,result_json=%s,lease_until=NULL,updated_at=clock_timestamp()
               WHERE id=%s AND status='running' AND worker_id=%s AND claim_token=%s
               AND lease_until>clock_timestamp()""",
            (
                state,
                message,
                data.jsonb(result) if result is not None else None,
                job["id"],
                job["worker_id"],
                job["claim_token"],
            ),
        )
        return cursor.rowcount == 1


@contextmanager
def _renewing(job, lease_seconds=LEASE_SECONDS):
    done, lost = threading.Event(), threading.Event()

    def renew():
        while not done.wait(min(15, lease_seconds / 3)):
            try:
                if not renew_lease(job, lease_seconds):
                    lost.set()
                    return
            except Exception:
                # Fail closed on uncertain ownership. A future worker can retry safely.
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        done.set()
        thread.join(timeout=2)


def process_one(worker_id=None):
    job = claim_job(worker_id)
    if job is None:
        return False
    with _renewing(job) as lost:
        try:
            payload = data.json_value(job["payload_json"])
            if job["kind"] == "sync":
                result = sync_range(payload.get("start"), payload.get("end"), job=job)
                if not result["complete"]:
                    message = f"Partial sync: {result['failure_count']} requests failed. Available data was saved; this range remains pending in coverage history."
                    state = "failed"
                else:
                    message, state = (
                        f"Synced {result['sessions']} recordings. Report jobs are queued.",
                        "completed",
                    )
            elif job["kind"] == "report":
                result = data.get_report(int(payload["session_id"]), job=job)
                result = {
                    "session_id": result["session"]["id"],
                    "version": result["version"],
                }
                message, state = "Report snapshot is ready.", "completed"
            elif job["kind"] in {"index", "embeddings"}:
                from .knowledge import index_documents

                result = index_documents()
                message, state = "Knowledge index was refreshed.", "completed"
            else:
                raise ValueError("Unsupported job kind.")
        except LeaseLost:
            return True
        except SourceBusy:
            # Source contention is not an extraction failure. Release this claim;
            # the singleton worker retries it on the next pass.
            with data.database() as connection:
                connection.execute(
                    """UPDATE jobs SET status='queued',attempts=GREATEST(attempts-1,0),worker_id=NULL,
                       claim_token=NULL,lease_until=NULL,updated_at=clock_timestamp(),message='Waiting for the source importer.'
                       WHERE id=%s AND status='running' AND worker_id=%s AND claim_token=%s
                       AND lease_until>clock_timestamp()""",
                    (job["id"], job["worker_id"], job["claim_token"]),
                )
            return False
        except (SyncError, ValueError, KeyError) as error:
            result, state = None, "failed"
            message = (
                str(error)
                if isinstance(error, SyncError)
                else "The job could not complete because its input or source record was invalid."
            )
        except Exception:
            result, state = None, "failed"
            message = "The job failed unexpectedly. Existing data was retained; retry or inspect setup."
        if not lost.is_set():
            finish_job(job, state, message, result)
    return True


def recover_interrupted_jobs():
    """Only expired owners are recoverable; never steal another worker's live job."""
    with data.database() as connection:
        connection.execute(
            """UPDATE jobs SET status=CASE WHEN attempts<%s THEN 'queued' ELSE 'failed' END,
               message='Expired worker lease recovered; safe retry queued when the attempt budget permits.',
               worker_id=NULL,claim_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
               WHERE status='running' AND (lease_until IS NULL OR lease_until<=clock_timestamp())""",
            (MAX_ATTEMPTS,),
        )
        connection.execute(
            """UPDATE sync_runs SET status='failed',finished_at=clock_timestamp(),failure_count=1
               WHERE status='running' AND NOT EXISTS (
                 SELECT 1 FROM jobs WHERE jobs.id=sync_runs.job_id AND jobs.status='running'
                 AND jobs.claim_token=sync_runs.claim_token AND jobs.lease_until>clock_timestamp())
               AND job_id IS NOT NULL""",
        )


def _merged(ranges):
    output = []
    for first, last in sorted(ranges):
        if first > last:
            continue
        if output and first <= output[-1][1] + timedelta(days=1):
            output[-1] = (output[-1][0], max(output[-1][1], last))
        else:
            output.append((first, last))
    return output


def _uncovered(ranges, coverage):
    output = []
    covered = _merged(coverage)
    for first, last in _merged(ranges):
        cursor = first
        for left, right in covered:
            if right < cursor:
                continue
            if left > last:
                break
            if left > cursor:
                output.append((cursor, min(last, left - timedelta(days=1))))
            cursor = max(cursor, right + timedelta(days=1))
            if cursor > last:
                break
        if cursor <= last:
            output.append((cursor, last))
    return output


def schedule_poll(interval_seconds=900):
    if not credentials_configured():
        return False
    with data.advisory_lock("sync-scheduler") as acquired:
        if not acquired:
            return False
        with data.database() as connection:
            values = {
                r["key"]: r["value"]
                for r in connection.execute(
                    "SELECT key,value FROM meta WHERE key IN ('worker_last_poll','auto_sync_start','last_sync_end')"
                ).fetchall()
            }
            now = datetime.now(timezone.utc)
            stamp = data.timestamp(values.get("worker_last_poll"))
            if stamp and (now - stamp).total_seconds() < interval_seconds:
                return False
            today = now.astimezone(data.ATLANTA).date()
            recent = today - timedelta(days=13)
            try:
                anchor = date.fromisoformat(values["auto_sync_start"])
            except (KeyError, ValueError):
                anchor = recent
                # Conservative one-time upgrade from the former SQLite checkpoint.
                try:
                    anchor = min(
                        anchor,
                        date.fromisoformat(values["last_sync_end"])
                        - timedelta(days=13),
                    )
                except (KeyError, ValueError):
                    pass
                _meta(connection, "auto_sync_start", anchor.isoformat())
            runs = connection.execute(
                "SELECT start_date,end_date,status,started_at,finished_at FROM sync_runs"
            ).fetchall()
            complete = [r for r in runs if r["status"] == "completed"]
            targets = _uncovered(
                [(anchor, today)], [(r["start_date"], r["end_date"]) for r in complete]
            )
            # Older failed backfills remain targets without importing unrelated years.
            for run in runs:
                if run["status"] in {"failed", "partial"}:
                    newer = [
                        (r["start_date"], r["end_date"])
                        for r in complete
                        if r["finished_at"] and r["finished_at"] > run["started_at"]
                    ]
                    targets.extend(
                        _uncovered([(run["start_date"], run["end_date"])], newer)
                    )
            targets.append((recent, today))  # Revisit late uploads and corrections.
            pending = []
            for row in connection.execute(
                "SELECT payload_json FROM jobs WHERE kind='sync' AND status IN ('queued','running')"
            ).fetchall():
                payload = data.json_value(row["payload_json"])
                try:
                    last = (
                        date.fromisoformat(payload["end"])
                        if payload.get("end")
                        else today
                    )
                    first = (
                        date.fromisoformat(payload["start"])
                        if payload.get("start")
                        else last - timedelta(days=13)
                    )
                    pending.append((first, last))
                except (TypeError, ValueError):
                    continue
            queued = False
            for start, finish in _uncovered(targets, pending):
                while start <= finish:
                    end = min(start + timedelta(days=366), finish)
                    data.enqueue_job(
                        "sync",
                        {"start": start.isoformat(), "end": end.isoformat()},
                        connection=connection,
                    )
                    queued = True
                    start = end + timedelta(days=1)
            _meta(connection, "worker_last_poll", now.isoformat())
            return queued


def run_worker(*, once=False):
    """One ingest worker per shared database; PostgreSQL releases locks on disconnect."""
    with data.advisory_lock(WORKER_LOCK) as acquired:
        if not acquired:
            print("An ingest worker is already running for this database.")
            return 0
        stopped, done = threading.Event(), threading.Event()
        old_handlers = {}

        def stop(*_):
            stopped.set()

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                old_handlers[signum] = signal.signal(signum, stop)

        def keep_alive():
            while not done.wait(15):
                try:
                    heartbeat()
                except Exception:
                    pass

        heartbeat()
        thread = threading.Thread(target=keep_alive, daemon=True)
        thread.start()
        try:
            while not stopped.is_set():
                recover_interrupted_jobs()
                if not once and os.environ.get("VIPMBB_DISABLE_AUTO_SYNC") != "1":
                    schedule_poll()
                if process_one():
                    continue
                if once:
                    break
                stopped.wait(2)
        finally:
            done.set()
            thread.join(timeout=2)
            with data.database() as connection:
                connection.execute(
                    """DELETE FROM meta WHERE key='worker_heartbeat' AND EXISTS (
                                      SELECT 1 FROM meta WHERE key='worker_id' AND value=%s)""",
                    (WORKER_ID,),
                )
            for signum, previous in old_handlers.items():
                signal.signal(signum, previous)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process queued jobs without scheduling a network poll.",
    )
    args = parser.parse_args(argv)
    data.initialize()
    return run_worker(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
