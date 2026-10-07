"""Private SQLite store. No network activity, model calls or public data assets."""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ATLANTA = ZoneInfo("America/New_York")
HOME_GAME_DATES = frozenset("2025-11-03 2025-11-07 2025-11-10 2025-11-18 2025-11-23 2025-12-03 2025-12-06 2025-12-16 2025-12-20 2025-12-28 2026-01-03 2026-01-06 2026-01-14 2026-01-24 2026-01-31 2026-02-11 2026-02-18 2026-02-28 2026-03-04".split())
METRICS = ("minutes", "distance_m", "mechanical_load", "load_per_minute", "accel_load", "metabolic_work", "speed_max", "acceleration_count", "deceleration_count", "change_of_direction_count", "jump_count")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS players(id INTEGER PRIMARY KEY,name TEXT NOT NULL,number TEXT,active INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY,title TEXT NOT NULL DEFAULT '',start_utc TEXT NOT NULL,end_utc TEXT,local_date TEXT NOT NULL,source_labels TEXT NOT NULL DEFAULT '[]',classification TEXT NOT NULL DEFAULT 'unknown' CHECK(classification IN ('practice','game','unknown')),reviewed INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'preliminary',expected_players INTEGER,source_hash TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL DEFAULT '',source_json TEXT NOT NULL DEFAULT '{}',assignment_complete INTEGER NOT NULL DEFAULT 0,sync_complete INTEGER NOT NULL DEFAULT 0,removed_upstream INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stats(session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,player_id INTEGER NOT NULL REFERENCES players(id),metrics_json TEXT NOT NULL,missing_json TEXT NOT NULL DEFAULT '[]',legacy INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT '',raw_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(session_id,player_id));
CREATE TABLE IF NOT EXISTS assignments(session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,player_id INTEGER NOT NULL REFERENCES players(id),source_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(session_id,player_id));
CREATE TABLE IF NOT EXISTS phases(id INTEGER PRIMARY KEY,session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,title TEXT NOT NULL DEFAULT '',start_utc TEXT,end_utc TEXT,source_labels TEXT NOT NULL DEFAULT '[]',source_json TEXT NOT NULL DEFAULT '{}',valid INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS phase_stats(phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,player_id INTEGER NOT NULL REFERENCES players(id),metrics_json TEXT NOT NULL,missing_json TEXT NOT NULL DEFAULT '[]',raw_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL DEFAULT '',PRIMARY KEY(phase_id,player_id));
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL REFERENCES sessions(id),classification TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL REFERENCES sessions(id),version INTEGER NOT NULL,source_hash TEXT NOT NULL,payload_json TEXT NOT NULL,generated_at TEXT NOT NULL,UNIQUE(session_id,version));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'queued',message TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,result_json TEXT);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(local_date,start_utc);
CREATE INDEX IF NOT EXISTS idx_stats_player ON stats(player_id,session_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status,id);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def data_dir():
    path = Path(os.environ.get("VIPMBB_DATA_DIR", ROOT / ".local")).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def connect():
    path = data_dir() / "practice.sqlite3"
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return connection


@contextmanager
def database():
    connection = connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


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
    with database() as connection:
        connection.executescript(SCHEMA)
        report_schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='reports'").fetchone()[0]
        if "UNIQUE(session_id,source_hash)" in report_schema:
            # A source correction may revert to earlier values; preserve every revision.
            connection.execute("ALTER TABLE reports RENAME TO reports_v0")
            connection.execute("CREATE TABLE reports(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL REFERENCES sessions(id),version INTEGER NOT NULL,source_hash TEXT NOT NULL,payload_json TEXT NOT NULL,generated_at TEXT NOT NULL,UNIQUE(session_id,version))")
            connection.execute("INSERT INTO reports SELECT * FROM reports_v0")
            connection.execute("DROP TABLE reports_v0")
        connection.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','1')")
        # Serialize first-run imports across backend and worker startup.
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        if os.environ.get("VIPMBB_IMPORT_LEGACY", "1") != "0":
            _import_legacy(connection)
        _repair_legacy_labels(connection)


def _repair_legacy_labels(connection):
    if connection.execute("SELECT 1 FROM meta WHERE key='legacy_labels_v2'").fetchone():
        return
    originals = {}
    path = ROOT / "Dashboard/public/data/kinexon.local.sqlite3"
    if path.is_file():
        legacy = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            originals = dict(legacy.execute("SELECT session_id,type_label FROM sessions WHERE team_id=3"))
        finally:
            legacy.close()
    for row in connection.execute("SELECT * FROM sessions").fetchall():
        if json.loads(row["source_json"]).get("import") != "legacy":
            continue
        old = json.loads(row["source_labels"])
        corrected = labels(originals.get(row["id"], ",".join(old)))
        title = f"{' / '.join(corrected) or 'Unlabeled session'} · {row['local_date']}"
        classification = row["classification"] if row["reviewed"] else source_classification(corrected, row["start_utc"], row["end_utc"])
        connection.execute("UPDATE sessions SET source_labels=?,title=?,classification=? WHERE id=?", (canonical(corrected), title, classification, row["id"]))
    connection.execute("INSERT INTO meta VALUES ('legacy_labels_v2',?)", (utcnow(),))


def _import_legacy(connection):
    if connection.execute("SELECT 1 FROM meta WHERE key='legacy_imported'").fetchone():
        return
    # Existing fresh stores are never mixed with a subsequently discovered old cache.
    if connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]:
        return
    path = ROOT / "Dashboard/public/data/kinexon.local.sqlite3"
    if not path.is_file():
        return
    legacy = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    legacy.row_factory = sqlite3.Row
    try:
        players = legacy.execute("SELECT * FROM players").fetchall()
        sessions = legacy.execute("SELECT * FROM sessions").fetchall()
        records = legacy.execute("SELECT * FROM player_session_stats").fetchall()
        for player in players:
            if player["team_id"] != 3:
                continue
            connection.execute("INSERT OR IGNORE INTO players VALUES (?,?,?,?,?)", (player["player_id"], f"{player['first_name']} {player['last_name']}".strip() or f"Player {player['player_id']}", player["number"], int(not player["deleted"]), player["updated_at"]))
        session_ids = set()
        for old in sessions:
            if old["team_id"] != 3:
                continue
            start = timestamp(old["start_session"])
            if start is None:
                continue
            end = timestamp(old["end_session"])
            local_date = start.astimezone(ATLANTA).date().isoformat()
            source = labels(old["type_label"])
            title = f"{' / '.join(source) or 'Unlabeled session'} · {local_date}"
            classification = source_classification(source, start.isoformat(), end.isoformat() if end else None)
            connection.execute("INSERT INTO sessions(id,title,start_utc,end_utc,local_date,source_labels,classification,updated_at,source_json,source_hash) VALUES (?,?,?,?,?,?,?,?,?,?)", (old["session_id"], title, start.isoformat(), end.isoformat() if end else None, local_date, canonical(source), classification, old["updated_at"], canonical({"import": "legacy", "original_category": old["category"]}), digest(dict(old))))
            session_ids.add(old["session_id"])
        for old in records:
            if old["session_id"] not in session_ids:
                continue
            # The old pipeline used zero for absent API values. Preserve only known positive
            # values and disclose the lost provenance; never present ambiguous zeros as fact.
            metrics = {key: (finite(old[key]) or None) if key in old.keys() else None for key in METRICS}
            metrics["exposure_basis"] = "legacy_unknown"
            metrics["load_per_minute"] = metrics["mechanical_load"] / metrics["minutes"] if metrics["mechanical_load"] is not None and metrics["minutes"] else None
            connection.execute("INSERT OR IGNORE INTO players(id,name) VALUES (?,?)", (old["player_id"], f"Player {old['player_id']}"))
            connection.execute("INSERT INTO stats(session_id,player_id,metrics_json,missing_json,legacy,updated_at) VALUES (?,?,?,?,1,?)", (old["session_id"], old["player_id"], canonical(metrics), canonical([key for key in METRICS if metrics[key] is None]), old["updated_at"]))
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('legacy_imported',?)", (utcnow(),))
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('source','legacy-cache')")
    finally:
        legacy.close()


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
    item = dict(row)
    return {"id": item["id"], "title": item["title"], "date": item["local_date"], "start": item["start_utc"], "end": item["end_utc"], "classification": item["classification"], "reviewed": bool(item["reviewed"]), "status": item["status"], "source_labels": json.loads(item["source_labels"]), "player_count": item.get("player_count", 0), "report_version": item.get("report_version"), "updated_at": item["updated_at"]}


def list_sessions(start=None, end=None, classification=None, q=None, limit=100, offset=0):
    validate_range(start, end)
    if classification not in (None, "", "all", "practice", "game", "unknown", "reviewed"):
        raise ValueError("Unknown classification filter.")
    if not isinstance(limit, int) or not 1 <= limit <= 1000 or not isinstance(offset, int) or offset < 0:
        raise ValueError("Invalid pagination.")
    where, params = ["s.removed_upstream=0"], []
    if start:
        where.append("s.local_date>=?"); params.append(start)
    if end:
        where.append("s.local_date<=?"); params.append(end)
    if classification == "reviewed":
        where.append("s.reviewed=1")
    elif classification not in (None, "", "all"):
        where.append("s.classification=?"); params.append(classification)
    if q:
        where.append("(s.title LIKE ? ESCAPE '\\' OR CAST(s.id AS TEXT)=?)")
        params.extend(["%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", q])
    clause = " AND ".join(where)
    with database() as connection:
        total = connection.execute("SELECT COUNT(*) FROM sessions s WHERE " + clause, params).fetchone()[0]
        bounds = connection.execute("SELECT MIN(local_date),MAX(local_date) FROM sessions WHERE removed_upstream=0").fetchone()
        rows = connection.execute("SELECT s.*,(SELECT COUNT(*) FROM stats t WHERE t.session_id=s.id) player_count,(SELECT MAX(version) FROM reports r WHERE r.session_id=s.id) report_version FROM sessions s WHERE " + clause + " ORDER BY s.start_utc DESC,s.id DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
    return {"sessions": [session_dict(row) for row in rows], "total": total, "earliest": bounds[0], "latest": bounds[1]}


def list_players():
    with database() as connection:
        return {"players": [{**dict(row), "active": bool(row["active"])} for row in connection.execute("SELECT id,name,number,active FROM players ORDER BY name,id")]}


def status_data():
    with database() as connection:
        counts = connection.execute("SELECT COUNT(*),MIN(local_date),MAX(local_date) FROM sessions WHERE removed_upstream=0").fetchone()
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        records = connection.execute("SELECT COUNT(*),COALESCE(SUM(legacy),0) FROM stats").fetchone()
        return {"sessions": counts[0], "players": connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], "records": records[0], "legacy_records": records[1], "fresh_records": records[0] - records[1], "earliest": counts[1], "latest": counts[2], "last_sync": meta.get("last_sync"), "source": meta.get("source", "empty"), "sync_status": meta.get("sync_status", "not-synced")}


def review_session(session_id, classification, reason):
    if classification not in ("practice", "game", "unknown"):
        raise ValueError("Classification must be practice, game or unknown.")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("A review reason between 1 and 2000 characters is required.")
    with database() as connection:
        if not connection.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise KeyError("Session not found.")
        connection.execute("INSERT INTO reviews(session_id,classification,reason,created_at) VALUES (?,?,?,?)", (session_id, classification, reason.strip(), utcnow()))
        connection.execute("UPDATE sessions SET classification=?,reviewed=?,updated_at=? WHERE id=?", (classification, int(classification != "unknown"), utcnow(), session_id))
    return get_report(session_id)


def get_report(session_id):
    from .analytics import get_report as generate
    return generate(session_id)


def regenerate_report(session_id):
    return get_report(session_id)


def job_dict(row):
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    raw = result.pop("result_json", None)
    result["result"] = json.loads(raw) if raw else None
    return result


def enqueue_job(kind, payload=None):
    if kind not in ("sync", "report", "index", "embeddings"):
        raise ValueError("Unsupported job kind.")
    payload = payload or {}
    encoded = canonical(payload)
    if kind == "sync":
        validate_range(payload.get("start"), payload.get("end"))
    with database() as connection:
        existing = connection.execute("SELECT * FROM jobs WHERE kind=? AND payload_json=? AND status IN ('queued','running') ORDER BY id LIMIT 1", (kind, encoded)).fetchone()
        if existing:
            return job_dict(existing)
        stamp = utcnow()
        cursor = connection.execute("INSERT INTO jobs(kind,payload_json,created_at,updated_at,message) VALUES (?,?,?,?,?)", (kind, encoded, stamp, stamp, "Waiting for the local worker."))
        return job_dict(connection.execute("SELECT * FROM jobs WHERE id=?", (cursor.lastrowid,)).fetchone())


def list_jobs():
    with database() as connection:
        # A large backfill emits hundreds of report jobs. Never hide its active or
        # waiting sync jobs behind the latest completed report snapshots.
        return {"jobs": [job_dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,id DESC LIMIT 100")]}


def backup():
    folder = data_dir() / "backups"
    folder.mkdir(exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc)
    filename = f"practice-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.sqlite3"
    source = connect()
    target = sqlite3.connect(folder / filename)
    try:
        source.backup(target)
    finally:
        target.close(); source.close()
    (folder / filename).chmod(0o600)
    return {"filename": filename, "created_at": stamp.isoformat()}
