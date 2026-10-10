"""Deterministic report calculations; a language model never computes these facts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from . import data

REPORT_RULES_VERSION = 1
DEFINITIONS = [
    {
        "key": "minutes",
        "title": "Exposure minutes",
        "unit": "min",
        "body": "API time_on_playing_field / 60 where available. Otherwise API duration / 60, disclosed separately. Legacy records have an unknown denominator. Not official playing minutes.",
    },
    {
        "key": "distance_m",
        "title": "Distance",
        "unit": "m",
        "body": "Recorded distance_total. Session totals may include warmup and breaks.",
    },
    {
        "key": "mechanical_load",
        "title": "Mechanical load",
        "unit": "Kinexon units",
        "body": "Source mechanical_load. Not an effort, fatigue, injury-risk or basketball-efficiency score. Vendor unit requires confirmation.",
    },
    {
        "key": "load_per_minute",
        "title": "Mechanical load per minute",
        "unit": "Kinexon units/min",
        "body": "Mechanical load divided by positive exposure minutes. Comparisons require the same known exposure denominator; baseline is a minutes-weighted rate from up to five prior reviewed practices.",
    },
    {
        "key": "accel_load",
        "title": "Acceleration load",
        "unit": "Kinexon units",
        "body": "Source accel_load_accum; distinct from acceleration event count. Vendor unit requires confirmation.",
    },
    {
        "key": "metabolic_work",
        "title": "Metabolic work",
        "unit": "source units (unverified)",
        "body": "Source metabolic_work. Units and vendor calculation remain unverified; not presented as calories or an efficiency score.",
    },
    {
        "key": "speed_max",
        "title": "Peak speed",
        "unit": "m/s",
        "body": "Maximum source speed_max, never summed across players or recordings.",
    },
    {
        "key": "event_counts",
        "title": "Movement events",
        "unit": "count",
        "body": "Source acceleration, deceleration, change-of-direction and jump event counts; thresholds are vendor/session dependent, not universal intensity cutoffs.",
    },
]


def _metrics(record):
    source = data.json_value(record["metrics_json"])
    metrics = {key: data.finite(source.get(key)) for key in data.METRICS}
    metrics["exposure_basis"] = source.get("exposure_basis", "unknown")
    metrics["load_per_minute"] = (
        metrics["mechanical_load"] / metrics["minutes"]
        if metrics["mechanical_load"] is not None
        and metrics["minutes"]
        and metrics["minutes"] > 0
        else None
    )
    return metrics


def _baseline(connection, session, player_id, current):
    if session["classification"] != "practice":
        return {
            "sample_count": 0,
            "load_per_minute": None,
            "change_pct": None,
            "session_ids": [],
            "method": "Practice-only comparison.",
            "reason": "Recording is not classified as practice; a practice baseline is not applicable.",
        }
    basis = current.get("exposure_basis")
    samples = []
    if basis in ("on_playing_field", "session_duration"):
        candidates = connection.execute(
            """SELECT t.metrics_json,t.legacy,s.id,s.start_utc FROM stats t JOIN sessions s ON s.id=t.session_id WHERE t.player_id=%s AND s.start_utc<%s AND s.id<>%s AND s.classification='practice' AND s.reviewed AND NOT s.removed_upstream ORDER BY s.start_utc DESC,s.id DESC""",
            (player_id, session["start_utc"], session["id"]),
        ).fetchall()
        for row in candidates:
            metrics = _metrics(row)
            if (
                not row["legacy"]
                and metrics["exposure_basis"] == basis
                and metrics["load_per_minute"] is not None
            ):
                samples.append((row["id"], metrics))
            if len(samples) == 5:
                break
    enough = len(samples) >= 3
    rate = (
        sum(metrics["mechanical_load"] for _, metrics in samples)
        / sum(metrics["minutes"] for _, metrics in samples)
        if enough
        else None
    )
    change = (
        (current["load_per_minute"] / rate - 1) * 100
        if rate and current["load_per_minute"] is not None
        else None
    )
    return {
        "sample_count": len(samples),
        "load_per_minute": rate,
        "change_pct": change,
        "session_ids": [session_id for session_id, _ in samples],
        "method": "Exposure-weighted rate from up to five prior reviewed practices; minimum three comparable records.",
        "reason": None
        if enough
        else "At least three prior reviewed practices with the same known exposure denominator are required.",
    }


def _known_sum(players, key):
    # Sum only recorded values but expose contribution counts and incompleteness below.
    values = [
        player["metrics"][key]
        for player in players
        if player["metrics"][key] is not None
    ]
    return sum(values) if values else None


def get_report(session_id, job=None):
    from psycopg.errors import SerializationFailure, DeadlockDetected

    # Serialize same-session versions before opening the repeatable-read snapshot.
    with data.advisory_lock(f"report:{session_id}", wait=True):
        for attempt in range(5):
            try:
                return _build_report(session_id, job=job)
            except (SerializationFailure, DeadlockDetected):
                if attempt == 4:
                    raise


def _build_report(session_id, job=None):
    with data.database() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        session = connection.execute(
            "SELECT * FROM sessions WHERE id=%s FOR UPDATE", (session_id,)
        ).fetchone()
        if not session:
            raise KeyError("Session not found.")
        rows = connection.execute(
            'SELECT t.*,p.name,p.number FROM stats t JOIN players p ON p.id=t.player_id WHERE t.session_id=%s ORDER BY p.name COLLATE "C",p.id',
            (session_id,),
        ).fetchall()
        assignments = [
            row["player_id"]
            for row in connection.execute(
                "SELECT player_id FROM assignments WHERE session_id=%s ORDER BY player_id",
                (session_id,),
            )
        ]
        phases = connection.execute(
            "SELECT * FROM phases WHERE session_id=%s ORDER BY start_utc,id",
            (session_id,),
        ).fetchall()
        reviews = connection.execute(
            "SELECT classification,reason,created_at FROM reviews WHERE session_id=%s ORDER BY id",
            (session_id,),
        ).fetchall()
        players = []
        for row in rows:
            metrics = _metrics(row)
            players.append(
                {
                    "id": row["player_id"],
                    "name": row["name"],
                    "number": row["number"],
                    "metrics": metrics,
                    "baseline": _baseline(
                        connection, session, row["player_id"], metrics
                    ),
                    "missing_metrics": [
                        key for key in data.METRICS if metrics[key] is None
                    ],
                    "legacy": bool(row["legacy"]),
                }
            )
        recorded_ids = {player["id"] for player in players}
        missing_ids = sorted(set(assignments) - recorded_ids)
        legacy = (
            any(player["legacy"] for player in players)
            or data.json_value(session["source_json"]).get("import") == "legacy"
        )
        begin, end = (
            data.timestamp(session["start_utc"]),
            data.timestamp(session["end_utc"]),
        )
        valid_bounds = bool(
            begin
            and end
            and 0 < (end - begin).total_seconds() <= 21600
            and end <= datetime.now(timezone.utc)
        )
        inconsistent_exposure = [
            player
            for player in players
            if player["metrics"]["minutes"] == 0
            and any(
                (player["metrics"][key] or 0) > 0
                for key in ("distance_m", "mechanical_load")
            )
        ]
        complete = bool(
            valid_bounds
            and not inconsistent_exposure
            and not session["removed_upstream"]
            and session["assignment_complete"]
            and session["sync_complete"]
            and session["expected_players"] is not None
            and session["expected_players"] > 0
            and len(assignments) == session["expected_players"]
            and not missing_ids
            and not legacy
            and all(
                player["metrics"][key] is not None
                for player in players
                for key in ("minutes", "distance_m", "mechanical_load")
            )
        )
        warnings = []
        if not session["reviewed"]:
            warnings.append(
                "Activity type has not been reviewed. Source labels are suggestions, not proof of practice or competition."
            )
        if session["removed_upstream"]:
            warnings.append(
                "This recording was absent from the latest successful source calendar response and is retained for audit only."
            )
        if legacy:
            warnings.append(
                "Legacy cache: player coverage is unknown; some sessions were sampled. Old zero values may have meant missing data and are shown as unavailable. Exposure denominator is unknown; legacy rows are excluded from baselines."
            )
        if not complete:
            warnings.append(
                "Preliminary report: complete participant and core measurement coverage has not been established. Totals include available records only."
            )
        if inconsistent_exposure:
            warnings.append(
                f"{len(inconsistent_exposure)} player record(s) have positive distance or load but zero reported exposure. Their per-minute rates are unavailable; off-court activity or source timing may explain this and must be reviewed."
            )
        if (
            data.iso(session["local_date"]) in data.HOME_GAME_DATES
            and not session["reviewed"]
        ):
            warnings.append(
                "This date appears on the verified 2025–26 home-game schedule. Review activity type before treating this recording as practice."
            )
        if not valid_bounds:
            warnings.append(
                "Recording boundaries are missing, reversed, longer than six hours or not yet ended. Review timing before comparing workloads."
            )
        if not phases:
            warnings.append(
                "No drill/phase detail is cached for this recording. Whole-session data cannot establish five-minute or drill-specific workloads."
            )
        drills = []
        for phase in phases:
            phase_rows = connection.execute(
                'SELECT t.player_id,t.metrics_json,t.missing_json,p.name,p.number FROM phase_stats t JOIN players p ON p.id=t.player_id WHERE t.phase_id=%s ORDER BY p.name COLLATE "C",p.id',
                (phase["id"],),
            ).fetchall()
            from .sync import assignment_ids

            expected_ids = assignment_ids(
                data.json_value(phase["source_json"]).get("group_assignment")
            ) or set(assignments)
            missing_phase_ids = sorted(
                expected_ids - {row["player_id"] for row in phase_rows}
            )
            drills.append(
                {
                    "id": phase["id"],
                    "title": phase["title"],
                    "start": data.iso(phase["start_utc"]),
                    "end": data.iso(phase["end_utc"]),
                    "player_count": len(phase_rows),
                    "expected_players": len(expected_ids) if expected_ids else None,
                    "missing_player_ids": missing_phase_ids,
                    "valid": bool(phase["valid"]),
                    "players": [
                        {
                            "id": row["player_id"],
                            "name": row["name"],
                            "number": row["number"],
                            "metrics": _metrics(row),
                        }
                        for row in phase_rows
                    ],
                }
            )
        if any(not drill["valid"] for drill in drills):
            warnings.append(
                "Some phases have invalid or overlapping time boundaries. Their statistics are not summed into session totals."
            )
        if drills and any(
            not drill["player_count"] or drill["missing_player_ids"] for drill in drills
        ):
            warnings.append(
                "Phase metadata exists but some phase-player statistics are missing or unavailable."
            )
        observations = []
        valid_loads = [
            player
            for player in players
            if player["metrics"]["mechanical_load"] is not None
        ]
        if valid_loads:
            highest = max(
                valid_loads, key=lambda player: player["metrics"]["mechanical_load"]
            )
            observations.append(
                {
                    "title": "Highest recorded total load",
                    "body": f"{highest['name']} recorded {highest['metrics']['mechanical_load']:.1f} Kinexon mechanical-load units among the available records. Total load reflects exposure as well as intensity; it is not an effort ranking.",
                }
            )
        comparable = [
            player for player in players if player["baseline"]["change_pct"] is not None
        ]
        if comparable:
            changed = max(
                comparable, key=lambda player: abs(player["baseline"]["change_pct"])
            )
            observations.append(
                {
                    "title": "Individual comparison",
                    "body": f"{changed['name']}'s load per minute was {changed['baseline']['change_pct']:+.1f}% relative to {changed['baseline']['sample_count']} prior reviewed practices with comparable exposure measurement. This describes movement load, not fatigue or basketball performance.",
                }
            )
        else:
            if session["classification"] != "practice":
                observations.append(
                    {
                        "title": "Practice comparison not applicable",
                        "body": "This recording is not classified as practice, so it has not been compared with practice baselines. Game, unknown and practice activity are kept separate.",
                    }
                )
            else:
                observations.append(
                    {
                        "title": "Baseline not yet established",
                        "body": "No player has a usable three-practice reviewed baseline with a known, matching exposure denominator. No trend or fatigue inference has been generated.",
                    }
                )
        coverage = {
            "recorded_players": len(players),
            "expected_players": session["expected_players"],
            "complete": complete,
            "missing_player_ids": missing_ids,
            "notes": [
                "A classification review confirms activity type only; it does not certify data completeness.",
                "Player coverage is based on source assignments when successfully retrieved.",
            ],
        }
        summary = {
            "players": len(players),
            "minutes_total": _known_sum(players, "minutes"),
            "distance_m": _known_sum(players, "distance_m"),
            "mechanical_load": _known_sum(players, "mechanical_load"),
            "accel_load": _known_sum(players, "accel_load"),
            "contributing_players": {
                key: sum(player["metrics"][key] is not None for player in players)
                for key in ("minutes", "distance_m", "mechanical_load", "accel_load")
            },
        }
        snapshot = {
            "session": data.session_dict(session),
            "coverage": coverage,
            "summary": summary,
            "players": players,
            "drills": drills,
            "observations": observations,
            "warnings": warnings,
            "definitions": DEFINITIONS,
            "reviews": [data.public_value(dict(row)) for row in reviews],
            "rules_version": REPORT_RULES_VERSION,
        }
        # updated_at is transport metadata, not measurement content; repeated identical
        # imports must not create spurious report versions.
        hashable = {
            **snapshot,
            "session": {
                key: value
                for key, value in snapshot["session"].items()
                if key not in ("updated_at", "report_version")
            },
        }
        source_hash = data.digest(hashable)
        if job is not None:
            valid_job = connection.execute(
                """SELECT id FROM jobs WHERE id=%s
                AND worker_id=%s AND claim_token=%s AND status='running'
                AND lease_until>clock_timestamp() FOR UPDATE""",
                (job["id"], job["worker_id"], job["claim_token"]),
            ).fetchone()
            if valid_job is None:
                raise RuntimeError("Worker lease was lost before report publication.")
        previous = connection.execute(
            "SELECT source_hash,payload_json FROM reports WHERE session_id=%s ORDER BY version DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if previous and previous["source_hash"] == source_hash:
            return data.json_value(previous["payload_json"])
        version = connection.execute(
            "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM reports WHERE session_id=%s",
            (session_id,),
        ).fetchone()["next_version"]
        snapshot.update({"generated_at": data.utcnow(), "version": version})
        snapshot["session"]["report_version"] = version
        snapshot["session"]["status"] = "complete" if complete else "preliminary"
        connection.execute(
            "UPDATE sessions SET status=%s WHERE id=%s",
            (snapshot["session"]["status"], session_id),
        )
        # Keep status in the fingerprint aligned with the persisted status before save.
        hashable["session"]["status"] = snapshot["session"]["status"]
        source_hash = data.digest(hashable)
        connection.execute(
            "INSERT INTO reports(session_id,version,source_hash,payload_json,generated_at) VALUES (%s,%s,%s,%s,%s)",
            (
                session_id,
                version,
                source_hash,
                data.jsonb(snapshot),
                snapshot["generated_at"],
            ),
        )
        return snapshot


def regenerate_report(session_id):
    return get_report(session_id)
