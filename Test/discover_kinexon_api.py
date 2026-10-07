#!/usr/bin/env python3
"""Create a privacy-conscious inventory of the Kinexon Sport App API.

Credentials are read from environment variables and are never written to the
report. The script makes read-only GET requests and records schemas, counts,
available metric/event names, and coarse team-level summaries. It intentionally
does not download position or inertial CSV exports.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


DEFAULT_BASE_URL = "https://georgia-tech-mccamish.access.kinexon.com"
DEFAULT_TEAM_ID = 3
DEFAULT_FIELDS = [
    "duration",
    "time_on_playing_field",
    "distance_total",
    "distance_total_avg_per_minute",
    "speed_avg",
    "speed_max",
    "accel_load_accum",
    "mechanical_load",
    "mechanical_intensity",
    "physio_load",
    "physio_intensity",
    "metabolic_work",
    "time_high_metabolic_load",
    "jump_load",
    "jump_height_max",
    "event_count_acceleration",
    "event_count_deceleration",
    "event_count_change_of_direction",
    "event_count_jump",
    "event_count_full_court_transition",
    "event_count_mid_court_transition",
    "event_count_rspct_shot",
]
DEFAULT_EVENTS = [
    "acceleration",
    "deceleration",
    "change_of_direction",
    "jump",
    "full_court_transition",
    "mid_court_transition",
    "rspct_shot",
]


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def schema_for_records(records: list[Any]) -> dict[str, Any]:
    """Describe record fields without retaining field values."""
    objects = [record for record in records if isinstance(record, dict)]
    fields: dict[str, dict[str, Any]] = {}
    for record in objects:
        for name, value in record.items():
            entry = fields.setdefault(
                name, {"types": set(), "present_count": 0, "non_null_count": 0}
            )
            entry["types"].add(value_type(value))
            entry["present_count"] += 1
            if value is not None:
                entry["non_null_count"] += 1

    for entry in fields.values():
        entry["types"] = sorted(entry["types"])
    return {
        "record_count": len(records),
        "object_record_count": len(objects),
        "fields": dict(sorted(fields.items())),
    }


def shape(value: Any, depth: int = 0) -> dict[str, Any]:
    """Recursively describe JSON shape while omitting scalar values."""
    kind = value_type(value)
    result: dict[str, Any] = {"type": kind}
    if depth >= 3:
        return result
    if isinstance(value, dict):
        if depth == 0:
            result["fields"] = {
                key: shape(child, depth + 1) for key, child in sorted(value.items())
            }
        else:
            # Nested mappings can use player IDs or names as keys. Keep only
            # their structural characteristics.
            result["field_count"] = len(value)
            result["value_types"] = sorted({value_type(child) for child in value.values()})
    elif isinstance(value, list):
        result["count"] = len(value)
        if value:
            result["items"] = schema_for_records(value) if any(
                isinstance(item, dict) for item in value
            ) else {"types": sorted({value_type(item) for item in value})}
    return result


def counter(values: list[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value).items()))


def extract_player_ids(session_record: dict[str, Any]) -> list[int]:
    """Extract player IDs for requests without ever writing them to the report."""
    found: list[int] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            player_id = value.get("player_id")
            if isinstance(player_id, int):
                found.append(player_id)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    assignments: list[Any] = [session_record.get("group_assignment")]
    phases = session_record.get("phases")
    if isinstance(phases, list):
        assignments.extend(
            phase.get("group_assignment")
            for phase in phases
            if isinstance(phase, dict)
        )
    for assignment in assignments:
        if isinstance(assignment, str):
            try:
                assignment = ast.literal_eval(assignment)
            except (ValueError, SyntaxError):
                assignment = None
        walk(assignment)
    return list(dict.fromkeys(found))


def extract_assignment_player_ids(assignments: Any) -> list[int]:
    """Extract player IDs from sensor assignments without retaining identities."""
    found: list[int] = []
    if not isinstance(assignments, list):
        return found
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        player = assignment.get("player")
        if isinstance(player, dict) and isinstance(player.get("id"), int):
            found.append(player["id"])
    return list(dict.fromkeys(found))


def select_session_samples(sessions: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Choose recent sessions across coaching-relevant session categories."""
    if limit == 0:
        return []
    selected: list[dict[str, Any]] = []

    def add_first(predicate) -> None:
        for session in sessions:
            if predicate(session) and session not in selected:
                selected.append(session)
                return

    add_first(lambda session: True)
    add_first(lambda session: str(session.get("type", "")).lower() in {"game", "match"})
    add_first(lambda session: str(session.get("type", "")).lower() in {"practice", "training"})
    add_first(lambda session: str(session.get("type", "")).lower() == "shootaround")
    add_first(lambda session: str(session.get("type", "")).lower() == "testing")
    add_first(lambda session: bool(session.get("phases")))
    for session in sessions:
        if len(selected) >= limit:
            break
        if session not in selected:
            selected.append(session)
    return selected[:limit]


class KinexonClient:
    def __init__(self, base_url: str, username: str, password: str, api_key: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "vipmbb-api-discovery/1.0"}
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], Any]:
        query = dict(params or {})
        query["apiKey"] = self.api_key
        started = datetime.now(timezone.utc)
        try:
            response = self.session.get(
                self.base_url + path,
                params=query,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return {
                "path": path,
                "ok": False,
                "error_type": type(exc).__name__,
            }, None

        elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        metadata: dict[str, Any] = {
            "path": redact_identifiers_from_path(path),
            "status": response.status_code,
            "ok": response.ok,
            "content_type": response.headers.get("Content-Type", "").split(";", 1)[0],
            "elapsed_ms": elapsed_ms,
        }
        if response.status_code == 401:
            metadata["www_authenticate"] = response.headers.get("WWW-Authenticate")
        try:
            payload = response.json()
        except ValueError:
            payload = None
            metadata["response_type"] = "non_json"
        if not response.ok and isinstance(payload, dict):
            # Kinexon error messages are useful; never retain arbitrary response data.
            metadata["api_error"] = {
                key: payload[key] for key in ("success", "status", "message", "error") if key in payload
            }
        return metadata, payload


def redact_identifiers_from_path(path: str) -> str:
    """Remove player/session identifiers from paths retained in the report."""
    path = re.sub(r"(/player/)[^/]+", r"\1<player-id>", path)
    path = re.sub(r"(/session/)[^/]+", r"\1<session-id>", path)
    path = re.sub(r"(/sensor-assignment/)[^/]+", r"\1<session-id>", path)
    path = re.sub(r"(/statistics/)[^/]+(/categories)$", r"\1<session-id>\2", path)
    return path


def add_call(report: dict[str, Any], label: str, metadata: dict[str, Any]) -> None:
    report["calls"].append({"label": label, **metadata})
    status = metadata.get("status", metadata.get("error_type", "error"))
    print(f"[{status}] {label}: {metadata['path']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a credential-free inventory of available Kinexon API data."
    )
    parser.add_argument("--team-id", type=int, default=DEFAULT_TEAM_ID)
    parser.add_argument("--days", type=int, default=730, help="Session lookback window (default: 730).")
    parser.add_argument(
        "--sample-sessions",
        type=int,
        default=3,
        help="Number of recent sessions to inspect safely (default: 3, maximum: 10).",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/kinexon_api_inventory.json"),
    )
    args = parser.parse_args()

    username = os.environ.get("KINEXON_USER")
    password = os.environ.get("KINEXON_PASSWORD")
    api_key = os.environ.get("KINEXON_API_KEY")
    missing = [
        name
        for name, value in (
            ("KINEXON_USER", username),
            ("KINEXON_PASSWORD", password),
            ("KINEXON_API_KEY", api_key),
        )
        if not value
    ]
    if missing:
        print(f"Missing required credentials: {', '.join(missing)}", file=sys.stderr)
        return 1
    if args.days < 1:
        parser.error("--days must be at least 1")
    if not 0 <= args.sample_sessions <= 10:
        parser.error("--sample-sessions must be between 0 and 10")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    client = KinexonClient(args.base_url, username, password, api_key, args.timeout)
    report: dict[str, Any] = {
        "report_version": 1,
        "generated_at_utc": utc_text(end),
        "base_url": args.base_url.rstrip("/"),
        "team_id": args.team_id,
        "range_utc": {"min": utc_text(start), "max": utc_text(end)},
        "privacy": {
            "mode": "schema_only",
            "credentials_included": False,
            "raw_player_records_included": False,
            "raw_session_records_included": False,
            "raw_performance_values_included": False,
        },
        "calls": [],
        "inventory": {},
        "notes": [
            "Only read-only JSON endpoints were queried.",
            "Position and inertial CSV exports were intentionally not downloaded.",
            "Player IDs may be used in memory for sampling but are not saved.",
        ],
    }

    meta, availability = client.get("/public/v1/statistics/list")
    add_call(report, "available_metrics_and_events", meta)
    if not meta.get("ok"):
        print("Authentication or API availability check failed; no report was written.", file=sys.stderr)
        return 2
    if not isinstance(availability, dict):
        print("Unexpected statistics-list response; no report was written.", file=sys.stderr)
        return 2
    available_metrics = sorted(str(item) for item in availability.get("metrics", []))
    available_events = sorted(str(item) for item in availability.get("events", []))
    report["inventory"]["availability"] = {
        "metric_count": len(available_metrics),
        "metrics": available_metrics,
        "event_count": len(available_events),
        "events": available_events,
    }

    meta, players = client.get(f"/public/v1/teams/{args.team_id}/players")
    add_call(report, "team_players", meta)
    players = players if isinstance(players, list) else []
    report["inventory"]["players"] = {
        **schema_for_records(players),
        "active_count": sum(
            1 for player in players if isinstance(player, dict) and player.get("deleted") is False
        ),
        "historical_or_deleted_count": sum(
            1 for player in players if isinstance(player, dict) and player.get("deleted") is True
        ),
        "position_counts": counter(
            [player.get("function") for player in players if isinstance(player, dict)]
        ),
    }

    meta, sessions = client.get(
        f"/public/v1/teams/{args.team_id}/sessions-and-phases",
        {"min": utc_text(start), "max": utc_text(end)},
    )
    add_call(report, "sessions_and_phases", meta)
    sessions = sessions if isinstance(sessions, list) else []
    session_objects = [item for item in sessions if isinstance(item, dict)]
    session_objects.sort(key=lambda item: str(item.get("start_session", "")), reverse=True)
    phases = [
        phase
        for session in session_objects
        for phase in session.get("phases", [])
        if isinstance(phase, dict)
    ]
    starts = sorted(
        str(session["start_session"])
        for session in session_objects
        if session.get("start_session")
    )
    report["inventory"]["sessions"] = {
        **schema_for_records(session_objects),
        "first_start": starts[0] if starts else None,
        "last_start": starts[-1] if starts else None,
        "session_type_counts": counter([session.get("type") for session in session_objects]),
        "phase_count": len(phases),
        "phase_type_counts": counter([phase.get("type") for phase in phases]),
        "phase_schema": schema_for_records(phases),
    }

    fallback_player_ids = [
        player["id"]
        for player in players
        if isinstance(player, dict) and isinstance(player.get("id"), int)
    ]
    sampled: list[dict[str, Any]] = []
    selected_sessions = select_session_samples(session_objects, args.sample_sessions)
    for index, session_record in enumerate(selected_sessions, start=1):
        session_id = session_record.get("session_id")
        if session_id is None:
            continue
        player_ids = extract_player_ids(session_record)
        sample: dict[str, Any] = {
            "sample_number": index,
            "session_type": session_record.get("type"),
            "phase_count": len(session_record.get("phases", []))
            if isinstance(session_record.get("phases"), list)
            else 0,
        }

        meta, categories = client.get(f"/public/v1/statistics/{session_id}/categories")
        add_call(report, f"sample_{index}_categories", meta)
        sample["categories"] = {"call_status": meta.get("status"), "shape": shape(categories)}

        meta, assignments = client.get(f"/public/v1/sensor-assignment/{session_id}")
        add_call(report, f"sample_{index}_sensor_assignment", meta)
        sample["sensor_assignments"] = {
            "call_status": meta.get("status"),
            "schema": schema_for_records(assignments) if isinstance(assignments, list) else shape(assignments),
        }

        player_ids = (
            player_ids
            or extract_assignment_player_ids(assignments)
            or fallback_player_ids
        )

        if not player_ids:
            sample["player_sampling"] = "skipped_no_player_identifier"
            sampled.append(sample)
            continue
        fields = [field for field in DEFAULT_FIELDS if field in available_metrics]
        statistics: Any = []
        meta: dict[str, Any] = {"status": None}
        player_id = player_ids[0]
        attempts = 0
        for attempts, candidate_player_id in enumerate(player_ids[:3], start=1):
            player_id = candidate_player_id
            meta, statistics = client.get(
                f"/public/v1/statistics/player/{player_id}/session/{session_id}",
                {"fields": ",".join(fields)},
            )
            add_call(report, f"sample_{index}_player_statistics_attempt_{attempts}", meta)
            if isinstance(statistics, list) and statistics:
                break
        statistic_records = statistics if isinstance(statistics, list) else []
        populated_fields = sorted(
            {
                key
                for record in statistic_records
                if isinstance(record, dict)
                for key, value in record.items()
                if value is not None
            }
        )
        sample["player_statistics"] = {
            "call_status": meta.get("status"),
            "players_tried": attempts,
            "requested_fields": fields,
            "populated_fields": populated_fields,
            "schema": schema_for_records(statistic_records),
        }

        meta, event_counts = client.get(
            f"/public/v1/events/count-per-event-type/player/{player_id}/session/{session_id}"
        )
        add_call(report, f"sample_{index}_event_counts", meta)
        sample["event_counts"] = {
            "call_status": meta.get("status"),
            "shape": shape(event_counts),
        }

        sample["event_schemas"] = {}
        for event_name in (event for event in DEFAULT_EVENTS if event in available_events):
            meta, events = client.get(
                f"/public/v1/events/{event_name}/player/{player_id}/session/{session_id}"
            )
            add_call(report, f"sample_{index}_event_{event_name}", meta)
            sample["event_schemas"][event_name] = {
                "call_status": meta.get("status"),
                "schema": schema_for_records(events) if isinstance(events, list) else shape(events),
            }

        sampled.append(sample)

    report["inventory"]["sampled_sessions"] = sampled
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nWrote credential-free report: {args.output}")
    print("Review it before sharing; the default report excludes raw records and values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
