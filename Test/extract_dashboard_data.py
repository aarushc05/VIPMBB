#!/usr/bin/env python3
"""Build an all-active-player dataset for the local dashboard.

The script is read-only against Kinexon. Credentials come from environment
variables and are never written to disk. The generated JSON contains protected
roster and performance data, is gitignored, and should remain local.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


BASE_URL = "https://georgia-tech-mccamish.access.kinexon.com"
TEAM_ID = 3
FIELDS = [
    "duration", "time_on_playing_field", "distance_total", "speed_max",
    "accel_load_accum", "mechanical_load", "physio_load", "metabolic_work",
    "event_count_acceleration", "event_count_deceleration",
    "event_count_change_of_direction", "event_count_full_court_transition",
    "event_count_mid_court_transition", "event_count_jump",
]
ADDITIVE_FIELDS = {
    "distance_m": "distance_total",
    "mechanical_load": "mechanical_load",
    "physiological_load": "physio_load",
    "accel_load": "accel_load_accum",
    "metabolic_work": "metabolic_work",
    "acceleration_count": "event_count_acceleration",
    "deceleration_count": "event_count_deceleration",
    "change_of_direction_count": "event_count_change_of_direction",
    "jump_count": "event_count_jump",
}


def parse_api_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    cleaned = value.replace("Z", "+00:00")
    for parser in (
        lambda: datetime.fromisoformat(cleaned),
        lambda: datetime.strptime(value, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            parsed = parser()
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        except ValueError:
            continue
    return None


def api_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def friendly_date(value: date, include_year: bool = False) -> str:
    pattern = "%b %d, %Y" if include_year else "%b %d"
    return value.strftime(pattern).replace(" 0", " ")


def friendly_datetime(value: datetime) -> str:
    return value.strftime("%b %d, %Y · %I:%M %p").replace(" 0", " ")


def number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def blank_metrics() -> dict[str, float | int]:
    return {
        "sessions": 0, "minutes": 0.0, "distance_m": 0.0,
        "mechanical_load": 0.0, "physiological_load": 0.0,
        "accel_load": 0.0, "metabolic_work": 0.0,
        "high_intensity_actions": 0.0, "acceleration_count": 0.0,
        "deceleration_count": 0.0, "change_of_direction_count": 0.0,
        "transition_count": 0.0, "jump_count": 0.0, "speed_max": 0.0,
    }


def session_labels(session: dict[str, Any]) -> list[str]:
    values = session.get("types") or [session.get("type")]
    if not isinstance(values, list):
        values = [values]
    labels = []
    for value in values:
        value = value.get("label") if isinstance(value, dict) else value
        if isinstance(value, str):
            labels.extend(part.strip() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(labels))


def classify_session(session: dict[str, Any]) -> str:
    labels = {label.casefold() for label in session_labels(session)}
    game = bool(labels & {"game", "match"})
    practice = bool(labels & {"practice", "training", "shootaround"})
    if game and practice:
        return "other"  # Mixed labels require review; don't assign all workload to games.
    if game:
        return "game"
    if practice:
        return "practice"
    return "other"


def add_record(target: dict[str, float | int], record: dict[str, Any]) -> None:
    target["sessions"] += 1
    seconds = number(record.get("duration") if record.get("time_on_playing_field") is None
                     else record["time_on_playing_field"])
    target["minutes"] += seconds / 60.0
    for output_name, api_name in ADDITIVE_FIELDS.items():
        target[output_name] += number(record.get(api_name))
    target["transition_count"] += number(record.get("event_count_full_court_transition"))
    target["transition_count"] += number(record.get("event_count_mid_court_transition"))
    target["high_intensity_actions"] += number(record.get("event_count_acceleration"))
    target["high_intensity_actions"] += number(record.get("event_count_deceleration"))
    target["high_intensity_actions"] += number(record.get("event_count_change_of_direction"))
    target["speed_max"] = max(number(target["speed_max"]), number(record.get("speed_max")))


def merge_metrics(target: dict[str, float | int], source: dict[str, float | int]) -> None:
    for key, value in source.items():
        if key == "speed_max":
            target[key] = max(number(target[key]), number(value))
        else:
            target[key] += value


class Client:
    def __init__(self, username: str, password: str, api_key: str, timeout: float):
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.headers.update({"Accept": "application/json", "User-Agent": "vipmbb-dashboard-extractor/1.0"})

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = dict(params or {})
        query["apiKey"] = self.api_key
        try:
            response = self.session.get(BASE_URL + path, params=query, timeout=self.timeout)
        except requests.RequestException as error:
            raise RuntimeError(f"request failed ({type(error).__name__}); URL omitted to protect the API key") from None
        if not response.ok:
            challenge = response.headers.get("WWW-Authenticate", "")
            hint = " Basic Auth was rejected." if response.status_code == 401 and "Basic" in challenge else ""
            raise RuntimeError(f"Kinexon returned HTTP {response.status_code}.{hint}")
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Kinexon returned a non-JSON response") from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract all active players for the VIPMBB dashboard.")
    parser.add_argument("--team-id", type=int, default=TEAM_ID)
    parser.add_argument("--days", type=int, choices=(7, 30), default=7,
                        help="Dashboard window: 7 days (default) or 30 days.")
    parser.add_argument("--anchor", choices=("latest", "today"), default="latest",
                        help="End at the latest recorded session (default) or today.")
    parser.add_argument("--lookback-days", type=int, default=730,
                        help="History searched when locating the latest session.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path,
                        help="Output path. Defaults to the matching protected dashboard data file.")
    args = parser.parse_args()
    if args.days < 1 or args.lookback_days < args.days:
        parser.error("--days must be positive and no greater than --lookback-days")
    if args.output is None:
        filename = "team_week.local.json" if args.days == 7 else "team_month.local.json"
        args.output = Path("Dashboard/public/data") / filename

    credentials = {name: os.environ.get(name) for name in
                   ("KINEXON_USER", "KINEXON_PASSWORD", "KINEXON_API_KEY")}
    missing = [name for name, value in credentials.items() if not value]
    if missing:
        print(f"Missing required credentials: {', '.join(missing)}", file=sys.stderr)
        return 1
    client = Client(credentials["KINEXON_USER"], credentials["KINEXON_PASSWORD"],
                    credentials["KINEXON_API_KEY"], args.timeout)

    now = datetime.now(timezone.utc)
    search_start = now - timedelta(days=args.lookback_days)
    print("Reading active roster and session calendar...")
    try:
        roster = client.get(f"/public/v1/teams/{args.team_id}/players")
        sessions = client.get(f"/public/v1/teams/{args.team_id}/sessions-and-phases",
                              {"min": api_time(search_start), "max": api_time(now)})
    except RuntimeError as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 2
    if not isinstance(roster, list) or not isinstance(sessions, list):
        print("[FAIL] Unexpected roster or session response.", file=sys.stderr)
        return 2

    active_players = [player for player in roster if isinstance(player, dict)
                      and player.get("deleted") is False and isinstance(player.get("id"), int)]
    session_objects = [session for session in sessions if isinstance(session, dict)]
    dated_sessions = [(session, parse_api_datetime(session.get("start_session")))
                      for session in session_objects]
    dated_sessions = [(session, stamp) for session, stamp in dated_sessions if stamp]
    if args.anchor == "latest" and dated_sessions:
        end_date = max(stamp.date() for _, stamp in dated_sessions)
        anchor_name = "latest-recorded-session"
    else:
        end_date = now.date()
        anchor_name = "today"
    start_date = end_date - timedelta(days=args.days - 1)
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    selected_sessions = [(session, stamp) for session, stamp in dated_sessions
                         if start_dt <= stamp < end_dt]
    sessions_by_id = {str(session.get("session_id")): (session, stamp)
                      for session, stamp in selected_sessions if session.get("session_id") is not None}
    game_sessions = sum(classify_session(session) == "game" for session, _ in selected_sessions)
    practice_sessions = sum(classify_session(session) == "practice" for session, _ in selected_sessions)
    game_activity_dates = sorted({stamp.date().isoformat() for session, stamp in selected_sessions
                                  if classify_session(session) == "game"})

    player_rows: list[dict[str, Any]] = []
    daily: dict[date, dict[str, dict[str, float | int]]] = {
        start_date + timedelta(days=offset): {
            "all": blank_metrics(), "game": blank_metrics(), "practice": blank_metrics()
        } for offset in range(args.days)
    }
    successful_players = 0
    for index, player in enumerate(active_players, start=1):
        print(f"Fetching player {index}/{len(active_players)}...", end="\r", flush=True)
        try:
            records = client.get(f"/public/v1/statistics/player/{player['id']}/sessions",
                                 {"min": api_time(start_dt), "max": api_time(end_dt),
                                  "fields": ",".join(FIELDS)})
        except RuntimeError as error:
            print(f"\n[WARN] Player {index} skipped: {error}", file=sys.stderr)
            records = []
        if not isinstance(records, list):
            records = []
        sets = {"all": blank_metrics(), "game": blank_metrics(), "practice": blank_metrics()}
        seen: set[tuple[str, str]] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            session_key = str(record.get("session_id"))
            session_info = sessions_by_id.get(session_key)
            if not session_info:
                continue
            session, stamp = session_info
            bucket = classify_session(session)
            if bucket not in {"game", "practice"}:
                continue
            dedupe_key = (session_key, bucket)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            add_record(sets["all"], record)
            add_record(sets[bucket], record)
            add_record(daily[stamp.date()]["all"], record)
            add_record(daily[stamp.date()][bucket], record)
        if records:
            successful_players += 1
        name = " ".join(str(part).strip() for part in
                        (player.get("first_name"), player.get("last_name")) if str(part or "").strip())
        player_rows.append({
            "player_key": str(player["id"]), "name": name or f"Player {index}",
            "number": player.get("number"), "position": str(player.get("function") or ""),
            **sets,
        })
    if active_players:
        print(" " * 70, end="\r")

    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    team_daily = [{
        "date": day.isoformat(), "label": labels[day.weekday()], **daily[day]
    } for day in sorted(daily)]
    generated = datetime.now().astimezone()
    payload = {
        "schema_version": 1,
        "source": "kinexon",
        "generated_at_label": friendly_datetime(generated),
        "window": {
            "start": start_date.isoformat(), "end": end_date.isoformat(),
            "days": args.days,
            "start_label": friendly_date(start_date),
            "end_label": friendly_date(end_date, include_year=True),
            "session_count": len(selected_sessions), "game_sessions": game_sessions,
            "practice_sessions": practice_sessions, "anchor": anchor_name,
            "game_activity_dates": game_activity_dates,
        },
        "players": player_rows,
        "team_daily": team_daily,
        "extraction": {
            "active_player_count": len(active_players),
            "players_with_any_range_response": successful_players,
            "credentials_included": False,
            "classification": {
                "game": "session type contains Game or Match",
                "practice": "session type contains Practice, Training, or Shootaround",
                "other": "excluded from game/practice comparisons",
            },
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] Wrote local dashboard data for {len(active_players)} active players.")
    print(f"     Window: {start_date} through {end_date} ({len(selected_sessions)} sessions)")
    print(f"     Games/matches: {game_sessions}; practices/training/shootarounds: {practice_sessions}")
    print(f"     Output: {args.output}")
    print("     This file contains protected player data and is excluded from Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
