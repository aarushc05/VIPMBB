"""Authoritative PostgreSQL repository. SQLite is only an explicit import source."""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo
from .db import advisory_lock, connect, database, jsonb, schema_name, connection_settings

ROOT = Path(__file__).resolve().parents[1]
ATLANTA = ZoneInfo("America/New_York")
HOME_GAME_DATES = frozenset("2025-11-03 2025-11-07 2025-11-10 2025-11-18 2025-11-23 2025-12-03 2025-12-06 2025-12-16 2025-12-20 2025-12-28 2026-01-03 2026-01-06 2026-01-14 2026-01-24 2026-01-31 2026-02-11 2026-02-18 2026-02-28 2026-03-04".split())
METRICS = ("minutes", "distance_m", "mechanical_load", "load_per_minute", "accel_load", "metabolic_work", "speed_max", "acceleration_count", "deceleration_count", "change_of_direction_count", "jump_count")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def data_dir():
    path = Path(os.environ.get("VIPMBB_DATA_DIR", ROOT / ".local")).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def canonical(value):
    return json.dumps(public_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.replace(tzinfo=result.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def labels(value):
    if isinstance(value, str):
        try:
            decoded = ast.literal_eval(value)
            if isinstance(decoded, (dict, list, tuple)):
                value = list(decoded) if isinstance(decoded, tuple) else decoded
        except (ValueError, SyntaxError):
            # A historical extractor joined dictionary reprs with commas. If its
            # surrounding syntax was damaged, recover only explicit label fields,
            # never display a raw dictionary or metadata as an activity label.
            if value.lstrip().startswith(("{", "[")):
                recovered = re.findall(r"['\"]label['\"]\s*:\s*['\"]([^'\"]+)['\"]", value)
                value = recovered
    if not isinstance(value, list):
        value = [value]
    result = []
    for item in value:
        item = item.get("label") if isinstance(item, dict) else item
        if isinstance(item, str):
            result.extend(part.strip() for part in item.split(",") if part.strip())
    return list(dict.fromkeys(result))


def source_classification(source_labels, start, end):
    """A suggestion only. A schedule date or dubious bounds prevents auto-practice."""
    begin, finish = timestamp(start), timestamp(end)
    if not begin or not finish or not 0 < (finish - begin).total_seconds() <= 21600:
        return "unknown"
    source = {label.casefold() for label in labels(source_labels)}
    if source & {"game", "match"}:
        return "unknown"  # Unverified Match could be a scrimmage or mixed recording.
    if begin.astimezone(ATLANTA).date().isoformat() in HOME_GAME_DATES:
        return "unknown"
    return "practice" if source & {"practice", "training", "shootaround"} else "unknown"



def initialize():
    """Check explicit migrations; application startup never imports or mutates schema."""
    from .migrate import HEAD
    from psycopg.errors import UndefinedTable
    try:
        with database() as connection:
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except UndefinedTable:
        raise RuntimeError("Database schema is not initialized. Run python -m local_app.migrate upgrade.") from None
    if {row["version_num"] for row in rows} != {HEAD}:
        raise RuntimeError("Database schema needs migration. Run python -m local_app.migrate upgrade.")


def iso(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value.isoformat() if isinstance(value, date) else value


def public_value(value):
    """Project native date/time database values at JSON API boundaries only."""
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    return iso(value)


def json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def validate_range(start=None, end=None):
    for value in (start, end):
        if value is not None:
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError("Dates must use YYYY-MM-DD.")
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Dates must use YYYY-MM-DD.") from None
    if start and end and start > end:
        raise ValueError("Start date must not be after end date.")


def session_dict(row):
    item = public_value(dict(row))
    return {"id": item["id"], "title": item["title"], "date": item["local_date"], "start": item["start_utc"], "end": item["end_utc"], "classification": item["classification"], "reviewed": bool(item["reviewed"]), "status": item["status"], "source_labels": json_value(item["source_labels"]), "player_count": item.get("player_count", 0), "report_version": item.get("report_version"), "updated_at": item["updated_at"]}


def list_sessions(start=None, end=None, classification=None, q=None, limit=100, offset=0):
    validate_range(start, end)
    if classification not in (None, "", "all", "practice", "game", "unknown", "reviewed"):
        raise ValueError("Unknown classification filter.")
    if not isinstance(limit, int) or not 1 <= limit <= 1000 or not isinstance(offset, int) or offset < 0:
        raise ValueError("Invalid pagination.")
    where, params = ["NOT s.removed_upstream"], []
    if start:
        where.append("s.local_date>=%s"); params.append(start)
    if end:
        where.append("s.local_date<=%s"); params.append(end)
    if classification == "reviewed":
        where.append("s.reviewed")
    elif classification not in (None, "", "all"):
        where.append("s.classification=%s"); params.append(classification)
    if q:
        where.append("(s.title ILIKE %s ESCAPE '\\' OR CAST(s.id AS TEXT)=%s)")
        params.extend(["%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", q])
    clause = " AND ".join(where)
    with database() as connection:
        total = connection.execute("SELECT COUNT(*) AS total FROM sessions s WHERE " + clause, params).fetchone()["total"]
        bounds = connection.execute("SELECT MIN(local_date) AS earliest,MAX(local_date) AS latest FROM sessions WHERE NOT removed_upstream").fetchone()
        rows = connection.execute("SELECT s.*,(SELECT COUNT(*) FROM stats t WHERE t.session_id=s.id) player_count,(SELECT MAX(version) FROM reports r WHERE r.session_id=s.id) report_version FROM sessions s WHERE " + clause + " ORDER BY s.start_utc DESC,s.id DESC LIMIT %s OFFSET %s", [*params, limit, offset]).fetchall()
    return {"sessions": [session_dict(row) for row in rows], "total": total, "earliest": iso(bounds["earliest"]), "latest": iso(bounds["latest"])}


def list_players():
    with database() as connection:
        return {"players": [{**dict(row), "active": bool(row["active"])} for row in connection.execute("SELECT id,name,number,active FROM players ORDER BY name,id")]}



def status_data():
    with database() as connection:
        counts = connection.execute("SELECT COUNT(*) AS sessions,MIN(local_date) AS earliest,MAX(local_date) AS latest FROM sessions WHERE NOT removed_upstream").fetchone()
        meta = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM meta")}
        records = connection.execute("SELECT COUNT(*) AS records,COUNT(*) FILTER(WHERE legacy) AS legacy_records FROM stats").fetchone()
        return public_value({**counts, **records, "players": connection.execute("SELECT COUNT(*) AS total FROM players").fetchone()["total"], "fresh_records": records["records"]-records["legacy_records"], "last_sync": meta.get("last_sync"), "source": meta.get("source","empty"), "sync_status": meta.get("sync_status","not-synced"), "database": "postgresql"})


def review_session(session_id, classification, reason, actor_id="local"):
    if classification not in ("practice", "game", "unknown"):
        raise ValueError("Classification must be practice, game or unknown.")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("A review reason between 1 and 2000 characters is required.")
    with database() as connection:
        if not connection.execute("SELECT id FROM sessions WHERE id=%s FOR UPDATE", (session_id,)).fetchone():
            raise KeyError("Session not found.")
        connection.execute("INSERT INTO reviews(session_id,classification,reason,created_at,actor_id) VALUES (%s,%s,%s,%s,%s)", (session_id, classification, reason.strip(), utcnow(), actor_id))
        connection.execute("UPDATE sessions SET classification=%s,reviewed=%s,updated_at=%s WHERE id=%s", (classification, classification != "unknown", utcnow(), session_id))
    return get_report(session_id)


def get_report(session_id, job=None):
    from .analytics import get_report as generate
    return generate(session_id, job=job)


def regenerate_report(session_id):
    return get_report(session_id)



def job_dict(row):
    result = public_value(dict(row))
    result["payload"] = json_value(result.pop("payload_json"))
    result["result"] = json_value(result.pop("result_json", None))
    result.pop("claim_token", None)
    return result


def enqueue_job(kind, payload=None, connection=None):
    if kind not in ("sync", "report", "index", "embeddings"):
        raise ValueError("Unsupported job kind.")
    payload = payload or {}
    if kind == "sync":
        validate_range(payload.get("start"), payload.get("end"))
    if connection is None:
        with database() as owned:
            return enqueue_job(kind, payload, connection=owned)
    row = connection.execute("""INSERT INTO jobs(kind,payload_json,dedupe_key,message)
        VALUES (%s,%s,%s,'Waiting for the shared worker.')
        ON CONFLICT (kind,dedupe_key) WHERE status IN ('queued','running')
        DO UPDATE SET dedupe_key=EXCLUDED.dedupe_key RETURNING *""", (kind, jsonb(payload), digest(payload))).fetchone()
    return job_dict(row)


def list_jobs():
    with database() as connection:
        # A large backfill emits hundreds of report jobs. Never hide its active or
        # waiting sync jobs behind the latest completed report snapshots.
        return {"jobs": [job_dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,id DESC LIMIT 100")]}



def backup():
    """Consistent custom-format backup; credentials never enter subprocess argv."""
    from psycopg.conninfo import conninfo_to_dict
    folder = data_dir() / "backups"
    folder.mkdir(exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc)
    filename = f"practice-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.dump"
    path = folder / filename
    settings = connection_settings()
    if "conninfo" in settings:
        settings = conninfo_to_dict(settings["conninfo"])
    environment = dict(os.environ)
    for key, value in settings.items():
        variable = {"dbname":"PGDATABASE","user":"PGUSER","password":"PGPASSWORD","host":"PGHOST","port":"PGPORT","sslmode":"PGSSLMODE"}.get(key)
        if variable:
            environment[variable] = str(value)
    try:
        with path.open("xb") as output:
            path.chmod(0o600)
            result = subprocess.run(["pg_dump","--format=custom","--no-owner","--no-acl","--schema",schema_name()], stdout=output, stderr=subprocess.PIPE, env=environment, timeout=300, check=False)
        if result.returncode:
            raise RuntimeError("PostgreSQL backup failed.")
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        path.unlink(missing_ok=True)
        raise RuntimeError("PostgreSQL backup failed; no incomplete backup was retained. Check access and pg_dump version.") from None
    return {"filename": filename, "created_at": stamp.isoformat(), "format": "postgresql-custom"}
