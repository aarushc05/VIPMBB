"""Read-only Kinexon importer. API secrets never appear in errors or stored jobs."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
import time as clock
from urllib.parse import urlparse
from uuid import uuid4
import warnings

import requests
from dotenv import dotenv_values

from . import data

BASE_URL = "https://georgia-tech-mccamish.access.kinexon.com"
TEAM_ID = 3
FIELD_MAP = {
    "distance_m": "distance_total",
    "mechanical_load": "mechanical_load",
    "accel_load": "accel_load_accum",
    "metabolic_work": "metabolic_work",
    "speed_max": "speed_max",
    "acceleration_count": "event_count_acceleration",
    "deceleration_count": "event_count_deceleration",
    "change_of_direction_count": "event_count_change_of_direction",
    "jump_count": "event_count_jump",
}
FIELDS = ["duration", "time_on_playing_field", *FIELD_MAP.values(), "data_quality"]


class SyncError(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    """The caller no longer owns its job and must stop changing shared state."""


class SourceBusy(RuntimeError):
    """A different importer currently holds the source lock."""


def credentials():
    # Read each time so a user can configure the file without restarting the app.
    path = Path(os.environ.get("KINEXON_ENV_FILE", data.ROOT / ".env"))
    config = dotenv_values(path, interpolate=False) if path.is_file() else {}
    return {
        key: os.environ.get(key) or config.get(key) or ""
        for key in ("KINEXON_USER", "KINEXON_PASSWORD", "KINEXON_API_KEY")
    }


def credentials_configured():
    return all(credentials().values())


def source_settings():
    base = os.environ.get("KINEXON_BASE_URL", BASE_URL).rstrip("/")
    try:
        parsed = urlparse(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in base)
        ):
            raise ValueError
        parsed.port  # Validate malformed/out-of-range ports without echoing configuration.
        team = int(os.environ.get("KINEXON_TEAM_ID", str(TEAM_ID)))
        if team <= 0:
            raise ValueError
    except ValueError:
        raise SyncError(
            "Configure an HTTPS Kinexon origin and a positive KINEXON_TEAM_ID."
        ) from None
    hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    base = f"https://{hostname}" + (
        f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    )
    return base, team


def source_identity():
    base, team = source_settings()
    return data.digest({"base_url": base, "team_id": team})


def _verify_source(connection):
    """External numeric IDs are scoped to one source/team per database."""
    identity = source_identity()
    row = connection.execute(
        "SELECT value FROM meta WHERE key='source_identity' FOR UPDATE"
    ).fetchone()
    if row is not None and row["value"] != identity:
        raise SyncError(
            "This database belongs to a different Kinexon source/team. Use a separate database for the new source."
        )
    if row is None:
        populated = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM sessions) OR EXISTS(SELECT 1 FROM players) AS populated"
        ).fetchone()["populated"]
        legacy_identity = data.digest({"base_url": BASE_URL, "team_id": TEAM_ID})
        if populated and identity != legacy_identity:
            raise SyncError(
                "Existing records use the original Kinexon source/team. Use a separate database for another source."
            )
        _meta(connection, "source_identity", identity)


@contextmanager
def _database(job=None, source_token=None):
    """Fence each import transaction so an expired owner cannot publish late data."""
    with data.database() as connection:
        if job is not None:
            owned = connection.execute(
                """SELECT id FROM jobs WHERE id=%s AND status='running'
                   AND worker_id=%s AND claim_token=%s AND lease_until>clock_timestamp()
                   FOR UPDATE""",
                (job["id"], job["worker_id"], job["claim_token"]),
            ).fetchone()
            if owned is None:
                raise LeaseLost(
                    "The sync job lease expired; a current worker will recover it."
                )
        if source_token is not None:
            owner = connection.execute(
                "SELECT value FROM meta WHERE key='source_sync_token' FOR UPDATE"
            ).fetchone()
            if owner is None or owner["value"] != source_token:
                raise LeaseLost("The source import is now owned by another worker.")
        yield connection
        if job is not None:
            # A slow transaction can outlast its lease while holding the job row.
            # Check again before committing, so its changes roll back on expiry.
            owned = connection.execute(
                """SELECT id FROM jobs WHERE id=%s AND status='running'
                   AND worker_id=%s AND claim_token=%s AND lease_until>clock_timestamp()""",
                (job["id"], job["worker_id"], job["claim_token"]),
            ).fetchone()
            if owned is None:
                raise LeaseLost(
                    "The sync job lease expired before its transaction committed."
                )


def _meta(connection, key, value):
    connection.execute(
        "INSERT INTO meta(key,value) VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


class Client:
    def __init__(self, timeout=30):
        config = credentials()
        if not all(config.values()):
            raise SyncError(
                "Kinexon credentials are not configured. Set KINEXON_USER, KINEXON_PASSWORD and KINEXON_API_KEY locally."
            )
        self.base_url, self.team_id = source_settings()
        self.api_key = config["KINEXON_API_KEY"]
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = (config["KINEXON_USER"], config["KINEXON_PASSWORD"])
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "VIPMBB-local/1.0"}
        )
        self.timeout = timeout

    def get(self, path, params=None):
        if not path.startswith("/public/v1/"):
            raise SyncError("Unsupported Kinexon request path.")
        for attempt in range(3):
            try:
                response = self.session.get(
                    self.base_url + path,
                    params={**(params or {}), "apiKey": self.api_key},
                    timeout=(10, self.timeout),
                    allow_redirects=False,
                )
            except requests.RequestException:
                if attempt < 2:
                    clock.sleep(1 + attempt)
                    continue
                raise SyncError(
                    "Kinexon could not be reached within the request timeout. Existing data was retained."
                ) from None
            if response.status_code in (429, 502, 503, 504) and attempt < 2:
                clock.sleep(1 + attempt)
                continue
            if response.status_code != 200:
                raise SyncError(
                    f"Kinexon returned HTTP {response.status_code}. Check access and retry; existing data is retained."
                )
            try:
                return response.json()
            except ValueError:
                raise SyncError(
                    "Kinexon returned an unexpected non-JSON response."
                ) from None
        raise SyncError("Kinexon request failed after bounded retries.")


def normalize_metrics(record):
    metrics = {key: data.finite(record.get(field)) for key, field in FIELD_MAP.items()}
    exposure = data.finite(record.get("time_on_playing_field"))
    basis = "on_playing_field"
    if exposure is None:
        exposure = data.finite(record.get("duration"))
        basis = "session_duration" if exposure is not None else "unknown"
    metrics["minutes"] = exposure / 60 if exposure is not None else None
    metrics["load_per_minute"] = (
        metrics["mechanical_load"] / metrics["minutes"]
        if metrics["mechanical_load"] is not None
        and metrics["minutes"]
        and metrics["minutes"] > 0
        else None
    )
    metrics["exposure_basis"] = basis
    metrics["data_quality"] = data.finite(record.get("data_quality"))
    return metrics


def api_time(stamp):
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def assignment_ids(value):
    found = set()
    if isinstance(value, str):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return found

    def walk(item):
        if isinstance(item, dict):
            if type(item.get("player_id")) is int:
                found.add(item["player_id"])
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def fetch_phase_records(client, player_id, phases):
    """Read exact phase records in bounded batches, falling back without inventing data.

    Live comparison verified that the range endpoint returns matching measurements
    but omits duration. Records needing that fallback exposure use the individual
    endpoint. Invalid ranges, duplicate identities and source errors also fall back.
    """
    wanted = {phase["id"]: phase for phase in phases}
    output = {phase_id: None for phase_id in wanted}
    direct = set()
    dated = []
    for phase in phases:
        begin, end = (
            data.timestamp(phase.get("start_phase")),
            data.timestamp(phase.get("end_phase")),
        )
        if begin and end and 0 < (end - begin).total_seconds() <= 21600:
            dated.append((begin, end, phase["id"]))
        else:
            direct.add(phase["id"])
    dated.sort()
    while dated:
        boundary = dated[0][0] + timedelta(days=31)
        batch = [item for item in dated if item[0] < boundary]
        dated = [item for item in dated if item[0] >= boundary]
        ids = {item[2] for item in batch}
        try:
            records = client.get(
                f"/public/v1/statistics/player/{player_id}/phases",
                {
                    "min": api_time(
                        min(item[0] for item in batch) - timedelta(seconds=1)
                    ),
                    "max": api_time(
                        max(item[1] for item in batch) + timedelta(seconds=1)
                    ),
                    "fields": ",".join(FIELDS),
                },
            )
            if not isinstance(records, list) or any(
                not isinstance(record, dict) or type(record.get("phase_id")) is not int
                for record in records
            ):
                raise SyncError("Malformed bulk phase statistics response.")
            selected = {}
            for record in records:
                phase_id = record["phase_id"]
                if phase_id not in ids:
                    continue
                if record.get("session_id") not in (
                    None,
                    wanted[phase_id].get("session_id"),
                ):
                    raise SyncError("Bulk phase statistics session identity mismatch.")
                if phase_id in selected:
                    raise SyncError("Duplicate phase identity in bulk statistics.")
                selected[phase_id] = record
            for phase_id, record in selected.items():
                if data.finite(record.get("time_on_playing_field")) is None:
                    direct.add(phase_id)
                else:
                    output[phase_id] = record
        except SyncError:
            direct.update(ids)
    for phase_id in sorted(direct):
        result = client.get(
            f"/public/v1/statistics/player/{player_id}/phase/{phase_id}",
            {"fields": ",".join(FIELDS)},
        )
        if (
            not isinstance(result, list)
            or len(result) > 1
            or any(not isinstance(record, dict) for record in result)
        ):
            raise SyncError("Malformed individual phase statistics response.")
        if result and (
            result[0].get("phase_id") not in (None, phase_id)
            or result[0].get("session_id")
            not in (None, wanted[phase_id].get("session_id"))
        ):
            raise SyncError("Individual phase statistics identity mismatch.")
        output[phase_id] = result[0] if result else None
    return output


def upsert_player(connection, player, stamp):
    player_id = player["id"]
    name = " ".join(
        str(player.get(key) or "").strip() for key in ("first_name", "last_name")
    ).strip()
    existing = connection.execute(
        "SELECT name,number,active FROM players WHERE id=%s", (player_id,)
    ).fetchone()
    name = name or (existing["name"] if existing else f"Player {player_id}")
    active = (
        not player["deleted"]
        if "deleted" in player
        else (existing["active"] if existing else False)
    )
    connection.execute(
        "INSERT INTO players(id,name,number,active,updated_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name,number=COALESCE(excluded.number,players.number),active=excluded.active,updated_at=excluded.updated_at",
        (player_id, name, player.get("number"), active, stamp),
    )


def _calendar(client, start_dt, end_dt):
    sessions = {}
    cursor = start_dt
    while cursor < end_dt:
        boundary = min(cursor + timedelta(days=31), end_dt)
        rows = client.get(
            f"/public/v1/teams/{source_settings()[1]}/sessions-and-phases",
            {"min": api_time(cursor), "max": api_time(boundary)},
        )
        if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or type(row.get("session_id")) is not int
            or not data.timestamp(row.get("start_session"))
            for row in rows
        ):
            raise SyncError(
                "Malformed session calendar response; existing cache was retained."
            )
        for row in rows:
            if start_dt <= data.timestamp(row["start_session"]) < end_dt:
                sessions[row["session_id"]] = row
        cursor = boundary
    return sessions


def sync_range(start=None, end=None, *, job=None):
    data.validate_range(start, end)
    end_date = date.fromisoformat(end) if end else datetime.now(data.ATLANTA).date()
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=13)
    if start_date > end_date or (end_date - start_date).days > 366:
        raise ValueError(
            "Sync ranges must be ordered and no longer than 367 days. Use successive ranges for a historical backfill."
        )
    with data.advisory_lock("kinexon-source-sync") as acquired:
        if not acquired:
            raise SourceBusy("Another worker is already importing this source.")
        source_token = uuid4().hex
        with _database(job) as connection:
            # Fence the source independently of its advisory connection: if that
            # connection dies, the next owner invalidates all prior import writes.
            _meta(connection, "source_sync_token", source_token)
            _verify_source(connection)
            # An abandoned run remains visible and retryable in the coverage ledger.
            connection.execute("""UPDATE sync_runs SET status='failed',finished_at=clock_timestamp(),failure_count=1
                                  WHERE status='running' AND NOT EXISTS (
                                    SELECT 1 FROM jobs WHERE jobs.id=sync_runs.job_id AND jobs.status='running'
                                    AND jobs.claim_token=sync_runs.claim_token AND jobs.lease_until>clock_timestamp())""")
            row = connection.execute(
                """INSERT INTO sync_runs(job_id,start_date,end_date,status,worker_id,claim_token)
                   VALUES (%s,%s,%s,'running',%s,%s) RETURNING id""",
                (
                    job["id"] if job else None,
                    start_date,
                    end_date,
                    job["worker_id"] if job else None,
                    job["claim_token"] if job else None,
                ),
            ).fetchone()
            run_id = row["id"]
            _meta(connection, "sync_status", "running")
        try:
            result = _import_range(
                start_date, end_date, job=job, source_token=source_token
            )
            with _database(job, source_token) as connection:
                connection.execute(
                    """UPDATE sync_runs SET status=%s,finished_at=clock_timestamp(),
                                      failure_count=%s,result_json=%s WHERE id=%s""",
                    (
                        "completed" if result["complete"] else "partial",
                        result["failure_count"],
                        data.jsonb(result),
                        run_id,
                    ),
                )
            return result
        except LeaseLost:
            raise  # The replacement owner is responsible for the abandoned ledger row.
        except Exception:
            with _database(job, source_token) as connection:
                connection.execute(
                    "UPDATE sync_runs SET status='failed',finished_at=clock_timestamp(),failure_count=1 WHERE id=%s",
                    (run_id,),
                )
                _meta(connection, "sync_status", "failed")
            raise


def _import_range(start_date, end_date, *, job=None, source_token=None):
    start_dt = datetime.combine(start_date, time.min, tzinfo=data.ATLANTA)
    end_dt = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=data.ATLANTA
    )
    client = Client()
    stamp = data.utcnow()
    try:
        roster = client.get(f"/public/v1/teams/{source_settings()[1]}/players")
        if not isinstance(roster, list) or any(
            not isinstance(player, dict) or type(player.get("id")) is not int
            for player in roster
        ):
            raise SyncError("Malformed roster response; existing cache was retained.")
        sessions = _calendar(client, start_dt, end_dt)
        failures = []
        incomplete_session_ids = set()
        player_ids = {player["id"] for player in roster}
        session_players = {}
        assignments_ok = set()
        with _database(job, source_token) as connection:
            for player in roster:
                upsert_player(connection, player, stamp)
            for session_id, source in sessions.items():
                begin, finish = (
                    data.timestamp(source["start_session"]),
                    data.timestamp(source.get("end_session")),
                )
                local_date = begin.astimezone(data.ATLANTA).date().isoformat()
                source_labels = data.labels(source.get("types") or source.get("type"))
                classification = data.source_classification(
                    source_labels,
                    begin.isoformat(),
                    finish.isoformat() if finish else None,
                )
                title = (
                    str(
                        source.get("description")
                        or " / ".join(source_labels)
                        or "Unlabeled session"
                    ).strip()
                    + f" · {local_date}"
                )
                connection.execute(
                    """INSERT INTO sessions(id,title,start_utc,end_utc,local_date,source_labels,classification,updated_at,source_json,source_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET title=excluded.title,start_utc=excluded.start_utc,end_utc=excluded.end_utc,local_date=excluded.local_date,source_labels=excluded.source_labels,classification=CASE WHEN sessions.reviewed THEN sessions.classification ELSE excluded.classification END,updated_at=excluded.updated_at,source_json=excluded.source_json,source_hash=excluded.source_hash,sync_complete=FALSE,removed_upstream=FALSE""",
                    (
                        session_id,
                        title,
                        begin,
                        finish,
                        local_date,
                        data.jsonb(source_labels),
                        classification,
                        stamp,
                        data.jsonb(source),
                        data.digest(source),
                    ),
                )
                phase_list = source.get("phases", [])
                if not isinstance(phase_list, list) or any(
                    not isinstance(phase, dict) or type(phase.get("id")) is not int
                    for phase in phase_list
                ):
                    failures.append(f"Session {session_id}: malformed phase metadata.")
                    incomplete_session_ids.add(session_id)
                    phase_list = []
                source["_phases_validated"] = phase_list
                ids = assignment_ids(source.get("group_assignment"))
                for phase in phase_list:
                    ids.update(assignment_ids(phase.get("group_assignment")))
                session_players[session_id] = ids
                player_ids.update(ids)
                # Replace phases only when the returned metadata has a valid shape.
                if phase_list == source.get("phases", []):
                    keep = {phase["id"] for phase in phase_list}
                    for old in connection.execute(
                        "SELECT id FROM phases WHERE session_id=%s", (session_id,)
                    ).fetchall():
                        if old["id"] not in keep:
                            connection.execute(
                                "DELETE FROM phases WHERE id=%s", (old["id"],)
                            )
                intervals = [
                    (
                        phase["id"],
                        data.timestamp(phase.get("start_phase")),
                        data.timestamp(phase.get("end_phase")),
                    )
                    for phase in phase_list
                ]
                for phase in phase_list:
                    pstart, pend = (
                        data.timestamp(phase.get("start_phase")),
                        data.timestamp(phase.get("end_phase")),
                    )
                    valid = bool(
                        pstart and pend and finish and begin <= pstart < pend <= finish
                    )
                    if valid and any(
                        other_id != phase["id"]
                        and left
                        and right
                        and left < pend
                        and right > pstart
                        for other_id, left, right in intervals
                    ):
                        valid = False
                    connection.execute(
                        "INSERT INTO phases(id,session_id,title,start_utc,end_utc,source_labels,source_json,valid) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,title=excluded.title,start_utc=excluded.start_utc,end_utc=excluded.end_utc,source_labels=excluded.source_labels,source_json=excluded.source_json,valid=excluded.valid",
                        (
                            phase["id"],
                            session_id,
                            str(
                                phase.get("description")
                                or phase.get("type")
                                or f"Phase {phase['id']}"
                            ),
                            pstart,
                            pend,
                            data.jsonb(data.labels(phase.get("type"))),
                            data.jsonb(phase),
                            valid,
                        ),
                    )
        for session_id, source in sessions.items():
            try:
                assigned = client.get(f"/public/v1/sensor-assignment/{session_id}")
                if not isinstance(assigned, list) or any(
                    not isinstance(row, dict)
                    or (
                        row.get("player") is not None
                        and (
                            not isinstance(row["player"], dict)
                            or type(row["player"].get("id")) is not int
                        )
                    )
                    for row in assigned
                ):
                    raise SyncError("Malformed sensor assignment response.")
                with _database(job, source_token) as connection:
                    connection.execute(
                        "DELETE FROM assignments WHERE session_id=%s", (session_id,)
                    )
                    for assignment in assigned:
                        player = assignment.get("player")
                        if player is None:
                            continue
                        upsert_player(connection, player, stamp)
                        session_players[session_id].add(player["id"])
                        player_ids.add(player["id"])
                    for player_id in session_players[session_id]:
                        upsert_player(connection, {"id": player_id}, stamp)
                        connection.execute(
                            "INSERT INTO assignments(session_id,player_id,source_json) VALUES (%s,%s,%s) ON CONFLICT(session_id,player_id) DO UPDATE SET source_json=excluded.source_json",
                            (
                                session_id,
                                player_id,
                                data.jsonb(
                                    {
                                        "sources": "sensor and session/phase group assignments"
                                    }
                                ),
                            ),
                        )
                    connection.execute(
                        "UPDATE sessions SET expected_players=%s,assignment_complete=TRUE WHERE id=%s",
                        (len(session_players[session_id]), session_id),
                    )
                assignments_ok.add(session_id)
            except SyncError as error:
                failures.append(f"Session {session_id}: {error}")
                with _database(job, source_token) as connection:
                    connection.execute(
                        "UPDATE sessions SET assignment_complete=FALSE WHERE id=%s",
                        (session_id,),
                    )
        successful_players = set()
        requested_players = player_ids if sessions else set()
        for player_id in sorted(requested_players):
            with _database(job, source_token) as connection:
                upsert_player(connection, {"id": player_id}, stamp)
            try:
                records = client.get(
                    f"/public/v1/statistics/player/{player_id}/sessions",
                    {
                        "min": api_time(start_dt),
                        "max": api_time(end_dt),
                        "fields": ",".join(FIELDS),
                    },
                )
                if not isinstance(records, list) or any(
                    not isinstance(record, dict)
                    or type(record.get("session_id")) is not int
                    for record in records
                ):
                    raise SyncError("Malformed player statistics response.")
                if len({record["session_id"] for record in records}) != len(records):
                    raise SyncError(
                        "Duplicate player-session statistics; cannot safely aggregate."
                    )
                with _database(job, source_token) as connection:
                    # Successful authoritative response can remove obsolete rows; failed
                    # requests never erase prior measurements.
                    connection.execute(
                        "DELETE FROM stats WHERE player_id=%s AND session_id=ANY(%s)",
                        (player_id, list(sessions)),
                    )
                    for record in records:
                        if record["session_id"] not in sessions:
                            continue
                        metrics = normalize_metrics(record)
                        connection.execute(
                            "INSERT INTO stats(session_id,player_id,metrics_json,missing_json,legacy,updated_at,raw_json) VALUES (%s,%s,%s,%s,FALSE,%s,%s)",
                            (
                                record["session_id"],
                                player_id,
                                data.jsonb(metrics),
                                data.jsonb(
                                    [
                                        key
                                        for key in data.METRICS
                                        if metrics[key] is None
                                    ]
                                ),
                                stamp,
                                data.jsonb(record),
                            ),
                        )
                successful_players.add(player_id)
            except SyncError as error:
                failures.append(f"Player {player_id}: {error}")
        phase_count = 0
        phase_failed_sessions = incomplete_session_ids
        phases_by_player = {}
        phase_assignments = {}
        for session_id, source in sessions.items():
            for phase in source["_phases_validated"]:
                phase_players = (
                    assignment_ids(phase.get("group_assignment"))
                    or session_players[session_id]
                )
                phase_assignments[phase["id"]] = (session_id, phase_players)
                for player_id in phase_players:
                    phases_by_player.setdefault(player_id, []).append(
                        {**phase, "session_id": session_id}
                    )
        for player_id, player_phases in sorted(phases_by_player.items()):
            try:
                results = fetch_phase_records(client, player_id, player_phases)
                with _database(job, source_token) as connection:
                    for phase_id, record in results.items():
                        connection.execute(
                            "DELETE FROM phase_stats WHERE phase_id=%s AND player_id=%s",
                            (phase_id, player_id),
                        )
                        if record is not None:
                            metrics = normalize_metrics(record)
                            connection.execute(
                                "INSERT INTO phase_stats(phase_id,player_id,metrics_json,missing_json,raw_json,updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
                                (
                                    phase_id,
                                    player_id,
                                    data.jsonb(metrics),
                                    data.jsonb(
                                        [
                                            key
                                            for key in data.METRICS
                                            if metrics[key] is None
                                        ]
                                    ),
                                    data.jsonb(record),
                                    stamp,
                                ),
                            )
                            phase_count += 1
            except SyncError as error:
                phase_failed_sessions.update(
                    phase["session_id"] for phase in player_phases
                )
                failures.append(f"Phase statistics for player {player_id}: {error}")
        with _database(job, source_token) as connection:
            for phase_id, (session_id, phase_players) in phase_assignments.items():
                if session_id not in phase_failed_sessions and phase_players:
                    connection.execute(
                        "DELETE FROM phase_stats WHERE phase_id=%s AND NOT(player_id=ANY(%s))",
                        (phase_id, list(phase_players)),
                    )
        with _database(job, source_token) as connection:
            for session_id in sessions:
                complete_requests = (
                    session_id in assignments_ok
                    and session_id not in phase_failed_sessions
                    and session_players[session_id].issubset(successful_players)
                )
                connection.execute(
                    "UPDATE sessions SET sync_complete=%s WHERE id=%s",
                    (complete_requests, session_id),
                )
            if not failures:
                present = set(sessions)
                for row in connection.execute(
                    "SELECT id FROM sessions WHERE local_date BETWEEN %s AND %s",
                    (start_date, end_date),
                ).fetchall():
                    if row["id"] not in present:
                        connection.execute(
                            "UPDATE sessions SET removed_upstream=TRUE WHERE id=%s",
                            (row["id"],),
                        )
                _meta(connection, "last_sync", stamp)
            _meta(connection, "source", "kinexon-api")
            _meta(connection, "sync_status", "partial" if failures else "complete")
            for session_id in sessions:
                data.enqueue_job(
                    "report", {"session_id": session_id}, connection=connection
                )
        return {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "sessions": len(sessions),
            "players_requested": len(requested_players),
            "players_succeeded": len(successful_players),
            "phase_records": phase_count,
            "complete": not failures,
            "failures": failures[:100],
            "failure_count": len(failures),
        }
    except Exception:
        # The outer ledger transaction records a bounded failure and checks ownership.
        raise


def main(argv=None):
    """Queue a bounded import; optionally drain work when no ingest worker is active."""
    import argparse
    from . import worker

    parser = argparse.ArgumentParser(
        description="Queue private Kinexon imports and deterministic reports."
    )
    parser.add_argument(
        "--start",
        help="First Atlanta calendar day, YYYY-MM-DD. Defaults to 14 days ending at --end.",
    )
    parser.add_argument(
        "--end",
        help="Last inclusive Atlanta calendar day, YYYY-MM-DD. Defaults to today.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Also process queued jobs if this database has no active ingest worker.",
    )
    args = parser.parse_args(argv)
    try:
        data.validate_range(args.start, args.end)
        finish = (
            date.fromisoformat(args.end)
            if args.end
            else datetime.now(data.ATLANTA).date()
        )
        begin = (
            date.fromisoformat(args.start)
            if args.start
            else finish - timedelta(days=13)
        )
        if begin > finish or (finish - begin).days > 366:
            raise ValueError("Choose an ordered range no longer than 367 days.")
        data.initialize()
        configured = credentials_configured()
        if not configured and (
            args.run or not worker.worker_status()["credentials_configured"]
        ):
            print(
                "Kinexon credentials are not configured. Mount the worker's private credentials file or set KINEXON_USER, KINEXON_PASSWORD and KINEXON_API_KEY."
            )
            return 1
        job = data.enqueue_job(
            "sync", {"start": begin.isoformat(), "end": finish.isoformat()}
        )
        if not args.run:
            print(
                f"Queued sync job {job['id']} for {begin} through {finish}. The running app worker will process it; follow System status in the app."
            )
            return 0
        print(
            f"Processing queued work for {begin} through {finish}. Private data and credentials are not printed."
        )
        worker.run_worker(once=True)
        with data.database() as connection:
            finished_job = connection.execute(
                "SELECT status,result_json FROM jobs WHERE id=%s", (job["id"],)
            ).fetchone()
        if finished_job and finished_job["status"] in {"queued", "running"}:
            print(
                f"Sync job {job['id']} remains queued or running with the ingest worker. Follow System status in the app."
            )
            return 0
        if not finished_job or finished_job["status"] != "completed":
            print(
                f"Sync job {job['id']} did not complete fully. Existing records were retained. Review System status and retry the same range."
            )
            return 2
        result = data.json_value(finished_job["result_json"]) or {}
        print(
            f"Sync complete: {result.get('sessions', 0)} recordings; {result.get('players_succeeded', 0)} player requests succeeded; {result.get('phase_records', 0)} phase-player records. Report jobs processed."
        )
        return 0
    except ValueError as error:
        print(str(error))
        return 2
    except KeyboardInterrupt:
        print(
            "Sync interrupted. Saved records are retained and unfinished jobs resume when the local worker starts."
        )
        return 130
    except Exception:
        print(
            "The local sync could not complete. Existing records were retained. Check setup and retry; no credential details are shown."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
