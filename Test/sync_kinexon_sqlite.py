#!/usr/bin/env python3
"""Incrementally cache Kinexon roster, sessions, and player statistics in SQLite.

Credentials are read from environment variables. The database contains protected
player data, is gitignored, and must not be committed or shared casually.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

from extract_dashboard_data import (
    Client,
    FIELDS,
    TEAM_ID,
    api_time,
    classify_session,
    number,
    parse_api_datetime,
    session_labels,
)


DEFAULT_DB = Path("Dashboard/public/data/kinexon.local.sqlite3")
ATLANTA = ZoneInfo("America/New_York")
SYNC_VERSION = 2


def valid_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    number TEXT,
    position TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL,
    start_session TEXT NOT NULL,
    session_date TEXT NOT NULL,
    end_session TEXT,
    type_label TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL CHECK (category IN ('game', 'practice', 'other')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_session_stats (
    player_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    session_date TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('game', 'practice', 'other')),
    minutes REAL NOT NULL DEFAULT 0,
    distance_m REAL NOT NULL DEFAULT 0,
    mechanical_load REAL NOT NULL DEFAULT 0,
    physiological_load REAL NOT NULL DEFAULT 0,
    accel_load REAL NOT NULL DEFAULT 0,
    metabolic_work REAL NOT NULL DEFAULT 0,
    high_intensity_actions REAL NOT NULL DEFAULT 0,
    acceleration_count REAL NOT NULL DEFAULT 0,
    deceleration_count REAL NOT NULL DEFAULT 0,
    change_of_direction_count REAL NOT NULL DEFAULT 0,
    transition_count REAL NOT NULL DEFAULT 0,
    jump_count REAL NOT NULL DEFAULT 0,
    speed_max REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, session_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    team_id INTEGER PRIMARY KEY,
    last_completed_at TEXT NOT NULL,
    range_start TEXT NOT NULL,
    range_end TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_team_date
ON sessions(team_id, session_date);

CREATE INDEX IF NOT EXISTS idx_stats_date_category
ON player_session_stats(session_date, category);

CREATE INDEX IF NOT EXISTS idx_stats_player_date
ON player_session_stats(player_id, session_date);

CREATE TABLE IF NOT EXISTS session_assignments (
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    player_id INTEGER NOT NULL REFERENCES players(player_id),
    PRIMARY KEY (session_id, player_id)
);
CREATE TABLE IF NOT EXISTS assignment_sync (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(session_id),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from None


def stats_values(record: dict[str, Any]) -> tuple[float, ...]:
    seconds = number(record.get("duration") if record.get("time_on_playing_field") is None
                     else record["time_on_playing_field"])
    acceleration = number(record.get("event_count_acceleration"))
    deceleration = number(record.get("event_count_deceleration"))
    direction = number(record.get("event_count_change_of_direction"))
    transitions = number(record.get("event_count_full_court_transition"))
    transitions += number(record.get("event_count_mid_court_transition"))
    return (
        seconds / 60.0,
        number(record.get("distance_total")),
        number(record.get("mechanical_load")),
        number(record.get("physio_load")),
        number(record.get("accel_load_accum")),
        number(record.get("metabolic_work")),
        acceleration + deceleration + direction,
        acceleration,
        deceleration,
        direction,
        transitions,
        number(record.get("event_count_jump")),
        number(record.get("speed_max")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Incrementally cache Kinexon data in SQLite.")
    parser.add_argument("--team-id", type=int, default=TEAM_ID)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start", type=parse_date,
                        help="Override sync start date (YYYY-MM-DD).")
    parser.add_argument("--end", type=parse_date, default=datetime.now(ATLANTA).date(),
                        help="Inclusive sync end date; defaults to today.")
    parser.add_argument("--initial-lookback-days", type=int, default=1460)
    parser.add_argument("--overlap-days", type=int, default=14,
                        help="Re-read this many days through the last successful checkpoint.")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.initial_lookback_days < 1 or args.overlap_days < 1:
        parser.error("lookback and overlap days must be positive")

    credentials = {name: os.environ.get(name) for name in
                   ("KINEXON_USER", "KINEXON_PASSWORD", "KINEXON_API_KEY")}
    missing = [name for name, value in credentials.items() if not value]
    if missing:
        print(f"Missing required credentials: {', '.join(missing)}", file=sys.stderr)
        return 1

    args.database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.database)
    if args.database.stat().st_size:
        with sqlite3.connect(str(args.database) + ".bak") as backup:
            connection.backup(backup)
    connection.executescript(SCHEMA)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(player_session_stats)")}
    for column, definition in (("missing_fields", "TEXT"), ("data_quality", "REAL")):
        if column not in columns:
            connection.execute(f"ALTER TABLE player_session_stats ADD COLUMN {column} {definition}")
    cache_version = connection.execute("SELECT value FROM cache_metadata WHERE key='sync_version'").fetchone()
    requires_backfill = not cache_version or cache_version[0] != str(SYNC_VERSION)
    checkpoint = connection.execute(
        "SELECT range_end FROM sync_state WHERE team_id = ?", (args.team_id,)
    ).fetchone()
    if args.start:
        start_date = args.start
    elif checkpoint and not requires_backfill:
        start_date = date.fromisoformat(checkpoint[0]) - timedelta(days=args.overlap_days - 1)
    else:
        start_date = args.end - timedelta(days=args.initial_lookback_days - 1)
        if requires_backfill:
            earliest = connection.execute(
                "SELECT MIN(session_date) FROM sessions WHERE team_id=?", (args.team_id,)
            ).fetchone()[0]
            if earliest:
                # Include the entire already-cached history, not just a rolling window.
                start_date = min(start_date, date.fromisoformat(earliest) - timedelta(days=1))
    if start_date > args.end:
        parser.error("--start must not be after --end")

    # API timestamps are UTC; date ranges are inclusive Atlanta calendar days.
    start_dt = datetime.combine(start_date, time.min, tzinfo=ATLANTA)
    end_dt = datetime.combine(args.end + timedelta(days=1), time.min, tzinfo=ATLANTA)
    client = Client(credentials["KINEXON_USER"], credentials["KINEXON_PASSWORD"],
                    credentials["KINEXON_API_KEY"], args.timeout)
    with connection:
        connection.execute("INSERT OR REPLACE INTO cache_metadata VALUES ('sync_status', 'incomplete')")
    print(f"Syncing {start_date} through {args.end} into {args.database}...")
    try:
        roster = client.get(f"/public/v1/teams/{args.team_id}/players")
        sessions = client.get(
            f"/public/v1/teams/{args.team_id}/sessions-and-phases",
            {"min": api_time(start_dt), "max": api_time(end_dt)},
        )
    except RuntimeError as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        connection.close()
        return 2
    if not isinstance(roster, list) or not isinstance(sessions, list):
        print("[FAIL] Unexpected roster or session response.", file=sys.stderr)
        connection.close()
        return 2
    if any(not isinstance(player, dict) or type(player.get('id')) is not int for player in roster) or any(
        not isinstance(session, dict) or type(session.get('session_id')) is not int
        or not parse_api_datetime(session.get('start_session')) for session in sessions
    ):
        print('[FAIL] Malformed roster/calendar response; existing records retained.', file=sys.stderr)
        connection.close()
        return 2

    updated_at = datetime.now(timezone.utc).isoformat()
    all_players: dict[int, dict[str, Any]] = {}
    failed_requests = 0
    with connection:
        for player in roster:
            if not isinstance(player, dict) or not isinstance(player.get("id"), int):
                continue
            deleted = bool(player.get("deleted"))
            connection.execute(
                """INSERT INTO players
                   (player_id, team_id, first_name, last_name, number, position, deleted, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(player_id) DO UPDATE SET
                     team_id=excluded.team_id, first_name=excluded.first_name,
                     last_name=excluded.last_name, number=excluded.number,
                     position=excluded.position, deleted=excluded.deleted,
                     updated_at=excluded.updated_at""",
                (player["id"], args.team_id, str(player.get("first_name") or ""),
                 str(player.get("last_name") or ""), player.get("number"),
                 str(player.get("function") or ""), int(deleted), updated_at),
            )
            all_players[player['id']] = player

        sessions_by_id: dict[str, tuple[dict[str, Any], datetime]] = {}
        for session in sessions:
            if not isinstance(session, dict) or not isinstance(session.get("session_id"), int):
                continue
            stamp = parse_api_datetime(session.get("start_session"))
            if not stamp:
                continue
            if not start_dt <= stamp < end_dt:
                continue
            category = classify_session(session)
            type_label = ", ".join(session_labels(session))
            connection.execute(
                """INSERT INTO sessions
                   (session_id, team_id, start_session, session_date, end_session,
                    type_label, category, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     team_id=excluded.team_id, start_session=excluded.start_session,
                     session_date=excluded.session_date, end_session=excluded.end_session,
                     type_label=excluded.type_label, category=excluded.category,
                     updated_at=excluded.updated_at""",
                (session["session_id"], args.team_id, str(session.get("start_session")),
                 stamp.astimezone(ATLANTA).date().isoformat(), session.get("end_session"), type_label,
                 category, updated_at),
            )
            sessions_by_id[str(session["session_id"])] = (session, stamp)

    # Assignment records can include former players absent from today's roster.
    for index, (session, _) in enumerate(sessions_by_id.values(), start=1):
        print(f"Checking assigned players for session {index}/{len(sessions_by_id)}...", end="\r", flush=True)
        try:
            assignments = client.get(f"/public/v1/sensor-assignment/{session['session_id']}")
            if not isinstance(assignments, list):
                raise RuntimeError("Unexpected assignment response")
            if any(not isinstance(assignment, dict) or (
                assignment.get('player') is not None and (
                    not isinstance(assignment['player'], dict) or type(assignment['player'].get('id')) is not int
                )
            ) for assignment in assignments):
                raise RuntimeError('Malformed assigned-player response')
        except RuntimeError as error:
            failed_requests += 1
            print(f"\n[WARN] Assignment request {index} failed: {error}", file=sys.stderr)
            continue
        with connection:
            connection.execute("DELETE FROM session_assignments WHERE session_id=?", (session['session_id'],))
            for assignment in assignments:
                player = assignment.get('player') if isinstance(assignment, dict) else None
                if not isinstance(player, dict) or not isinstance(player.get('id'), int):
                    continue
                if player['id'] not in all_players:
                    all_players[player['id']] = player
                    connection.execute(
                        """INSERT OR IGNORE INTO players
                        (player_id, team_id, first_name, last_name, number, position, deleted, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (player['id'], args.team_id, player.get('first_name') or '',
                         player.get('last_name') or '', player.get('number'),
                         player.get('function') or '', int(player.get('deleted', True)), updated_at),
                    )
                connection.execute("INSERT OR IGNORE INTO session_assignments VALUES (?, ?)",
                                   (session['session_id'], player['id']))
            connection.execute("INSERT OR REPLACE INTO assignment_sync VALUES (?, ?)",
                               (session['session_id'], updated_at))

    saved_records = 0
    for index, player in enumerate(all_players.values(), start=1):
        print(f"Fetching roster/assigned player {index}/{len(all_players)}...", end="\r", flush=True)
        try:
            records = client.get(
                f"/public/v1/statistics/player/{player['id']}/sessions",
                {"min": api_time(start_dt), "max": api_time(end_dt),
                 "fields": ",".join([*FIELDS, 'data_quality'])},
            )
        except RuntimeError as error:
            failed_requests += 1
            print(f"\n[WARN] Player request {index} failed: {error}", file=sys.stderr)
            continue
        if not isinstance(records, list) or any(
            not isinstance(record, dict) or type(record.get('session_id')) is not int
            for record in records
        ):
            failed_requests += 1
            print(f"\n[WARN] Player request {index} returned malformed statistics; cached rows retained.", file=sys.stderr)
            continue
        with connection:
            # A successful range response replaces that player's cached range,
            # including records subsequently removed or corrected in Kinexon.
            connection.execute("""DELETE FROM player_session_stats WHERE player_id=?
                AND session_date BETWEEN ? AND ?""", (player['id'], start_date.isoformat(), args.end.isoformat()))
            for record in records:
                if not isinstance(record, dict):
                    continue
                session_id = record.get("session_id")
                session_info = sessions_by_id.get(str(session_id))
                if not isinstance(session_id, int) or not session_info:
                    continue
                record = {**record, **{field: record.get(field) if valid_number(record.get(field)) else None
                                      for field in [*FIELDS, 'data_quality']}}
                session, stamp = session_info
                category = classify_session(session)
                connection.execute(
                    """INSERT INTO player_session_stats
                       (player_id, session_id, session_date, category, minutes, distance_m,
                        mechanical_load, physiological_load, accel_load, metabolic_work,
                        high_intensity_actions, acceleration_count, deceleration_count,
                        change_of_direction_count, transition_count, jump_count, speed_max,
                        updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(player_id, session_id) DO UPDATE SET
                         session_date=excluded.session_date, category=excluded.category,
                         minutes=excluded.minutes, distance_m=excluded.distance_m,
                         mechanical_load=excluded.mechanical_load,
                         physiological_load=excluded.physiological_load,
                         accel_load=excluded.accel_load, metabolic_work=excluded.metabolic_work,
                         high_intensity_actions=excluded.high_intensity_actions,
                         acceleration_count=excluded.acceleration_count,
                         deceleration_count=excluded.deceleration_count,
                         change_of_direction_count=excluded.change_of_direction_count,
                         transition_count=excluded.transition_count,
                         jump_count=excluded.jump_count, speed_max=excluded.speed_max,
                         updated_at=excluded.updated_at""",
                    (player["id"], session_id, stamp.astimezone(ATLANTA).date().isoformat(), category,
                     *stats_values(record), updated_at),
                )
                connection.execute("""UPDATE player_session_stats SET missing_fields=?, data_quality=?
                    WHERE player_id=? AND session_id=?""",
                    (json.dumps([field for field in FIELDS if record.get(field) is None]),
                     record.get('data_quality'), player['id'], session_id))
                saved_records += 1

    if failed_requests:
        connection.close()
        print(f"\n[PARTIAL] {failed_requests} requests failed. Checkpoint unchanged; rerun to retry.", file=sys.stderr)
        return 2
    with connection:
        # Reconcile sessions removed upstream only after the complete range succeeds.
        # Local backup taken above makes this cache reconciliation recoverable.
        cached_ids = connection.execute('SELECT session_id FROM sessions WHERE team_id=? AND session_date BETWEEN ? AND ?',
                                        (args.team_id, start_date.isoformat(), args.end.isoformat())).fetchall()
        for (session_id,) in cached_ids:
            if str(session_id) in sessions_by_id:
                continue
            for table in ('player_session_stats', 'session_assignments', 'assignment_sync'):
                connection.execute(f'DELETE FROM {table} WHERE session_id=?', (session_id,))
            connection.execute('DELETE FROM sessions WHERE session_id=?', (session_id,))
        connection.execute(
            """INSERT INTO sync_state (team_id, last_completed_at, range_start, range_end)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(team_id) DO UPDATE SET
                 last_completed_at=excluded.last_completed_at,
                 range_start=excluded.range_start, range_end=excluded.range_end""",
            (args.team_id, updated_at, start_date.isoformat(), args.end.isoformat()),
        )
        if not args.start:
            connection.execute("INSERT OR REPLACE INTO cache_metadata VALUES ('sync_version', ?)", (str(SYNC_VERSION),))
        connection.execute("INSERT OR REPLACE INTO cache_metadata VALUES ('timezone', 'America/New_York')")
        connection.execute("INSERT OR REPLACE INTO cache_metadata VALUES ('sync_status', 'complete')")
        connection.execute("PRAGMA optimize")
    totals = connection.execute(
        """SELECT COUNT(*), MIN(session_date), MAX(session_date)
           FROM player_session_stats"""
    ).fetchone()
    connection.close()
    print(" " * 72, end="\r")
    print(f"[OK] Cached {saved_records} player-session records in this sync.")
    print(f"     Database now has {totals[0]} records spanning {totals[1] or 'n/a'} to {totals[2] or 'n/a'}.")
    print("     Re-run this command later; subsequent syncs only refresh a recent overlap.")
    print("     This database contains protected player data and is excluded from Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
