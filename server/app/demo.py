"""Explicit, isolated synthetic examples. Never imports or resets private data."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import os

from . import analytics, data, knowledge

DEMO_DATABASE = "vipmbb_demo"
SEED_VERSION = 1
MANIFEST_KEY = "synthetic_demo"
PLAYERS = (
    "Demo Jordan Vale",
    "Demo Avery Brooks",
    "Demo Cameron Reed",
    "Demo Morgan Ellis",
    "Demo Taylor Hayes",
    "Demo Riley Quinn",
    "Demo Casey Lane",
    "Demo Drew Parker",
)
SESSIONS = (
    (16, "practice", "Foundations"),
    (12, "practice", "Transition development"),
    (9, "practice", "Half-court execution"),
    (6, "game", "Fictional exhibition A"),
    (4, "practice", "Defensive rotations"),
    (3, "unknown", "Unclassified recording"),
    (2, "game", "Fictional exhibition B"),
    (1, "practice", "Game preparation"),
)


def enabled():
    return os.environ.get("VIPMBB_DEMO") == "1"


def status():
    with data.database() as connection:
        row = connection.execute(
            "SELECT value FROM meta WHERE key=%s", (MANIFEST_KEY,)
        ).fetchone()
    manifest = json.loads(row["value"]) if row else {}
    return {
        "enabled": enabled() or bool(manifest),
        "synthetic": bool(manifest),
        "reference_date": manifest.get("reference_date"),
        "seeded_at": manifest.get("seeded_at"),
    }


def _guard(connection):
    if not enabled() or connection.info.dbname != DEMO_DATABASE:
        raise RuntimeError(
            "Demo seeding requires VIPMBB_DEMO=1 and the dedicated vipmbb_demo database. "
            "Use npm run demo; the private database must not be used."
        )


def _metrics(player_index, session_index, *, fraction=1):
    minutes = (68 + player_index * 2 + session_index % 3) * fraction
    load = (940 + player_index * 85 + session_index * 29) * fraction
    return {
        "minutes": round(minutes, 2),
        "distance_m": round(
            (3400 + player_index * 180 + session_index * 75) * fraction, 2
        ),
        "mechanical_load": round(load, 2),
        "load_per_minute": load / minutes,
        "accel_load": round(load * 0.24, 2),
        "metabolic_work": None,  # Demonstrates an unavailable metric, not a zero.
        "speed_max": round(5.8 + player_index * 0.17, 2),
        "acceleration_count": round((26 + player_index * 3) * fraction),
        "deceleration_count": round((23 + player_index * 2) * fraction),
        "change_of_direction_count": round((40 + player_index * 4) * fraction),
        "jump_count": None if player_index == 1 else round(player_index * 9 * fraction),
        "exposure_basis": "on_playing_field",
    }


def _insert_stats(connection, table, parent_id, player_index, metrics, now):
    # Table and key names are internal constants, never request input.
    key = "session_id" if table == "stats" else "phase_id"
    connection.execute(
        f"INSERT INTO {table}({key},player_id,metrics_json,missing_json,raw_json,updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (
            parent_id,
            910001 + player_index,
            data.jsonb(metrics),
            data.jsonb([key for key in data.METRICS if metrics[key] is None]),
            data.jsonb({"synthetic": True}),
            now,
        ),
    )


def seed(reference_date=None):
    """Seed once; retries preserve dates, user edits and the original synthetic data."""
    anchor = reference_date or datetime.now(data.ATLANTA).date()
    if not isinstance(anchor, date) or isinstance(anchor, datetime):
        raise ValueError("The demo reference date must be a date.")
    data.initialize()
    with data.advisory_lock("synthetic-demo-seed", wait=True):
        with data.database() as connection:
            _guard(connection)
            # Exclude concurrent insertions while verifying an empty target. This
            # command is deliberately incapable of replacing an existing dataset.
            connection.execute(
                "LOCK TABLE meta,players,sessions,stats,assignments,phases,phase_stats,"
                "reviews,reports,jobs,sync_runs,documents,chat_messages,import_manifests "
                "IN EXCLUSIVE MODE"
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key=%s", (MANIFEST_KEY,)
            ).fetchone()
            if row:
                manifest = json.loads(row["value"])
                if manifest.get("version") != SEED_VERSION:
                    raise RuntimeError(
                        "The existing demo uses another seed version; it was left unchanged."
                    )
                result = "already-seeded"
            else:
                for table in (
                    "meta",
                    "players",
                    "sessions",
                    "stats",
                    "assignments",
                    "phases",
                    "phase_stats",
                    "reviews",
                    "reports",
                    "jobs",
                    "sync_runs",
                    "documents",
                    "chat_messages",
                    "import_manifests",
                ):
                    if connection.execute(
                        f"SELECT EXISTS(SELECT 1 FROM {table}) AS populated"
                    ).fetchone()["populated"]:
                        raise RuntimeError(
                            "The target contains unmarked data. Demo seeding was refused; nothing was changed."
                        )
                now = datetime.now(timezone.utc)
                manifest = {
                    "version": SEED_VERSION,
                    "reference_date": anchor.isoformat(),
                    "seeded_at": now.isoformat(),
                }
                for index, name in enumerate(PLAYERS):
                    connection.execute(
                        "INSERT INTO players(id,name,number,active,updated_at) VALUES(%s,%s,%s,TRUE,%s)",
                        (910001 + index, name, str(index + 1), now),
                    )
                for index, (days_ago, activity, title) in enumerate(SESSIONS):
                    session_id = 900001 + index
                    local_date = anchor - timedelta(days=days_ago)
                    start = datetime.combine(
                        local_date, time(14), data.ATLANTA
                    ).astimezone(timezone.utc)
                    end = start + timedelta(minutes=90)
                    reviewed = activity != "unknown"
                    connection.execute(
                        """INSERT INTO sessions(id,title,start_utc,end_utc,local_date,source_labels,
                        classification,reviewed,status,expected_players,source_hash,updated_at,
                        source_json,assignment_complete,sync_complete)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'complete',8,%s,%s,%s,TRUE,TRUE)""",
                        (
                            session_id,
                            "Demo · " + title,
                            start,
                            end,
                            local_date,
                            data.jsonb([activity.title()] if reviewed else []),
                            activity,
                            reviewed,
                            data.digest({"synthetic": session_id}),
                            now,
                            data.jsonb({"synthetic": True}),
                        ),
                    )
                    if reviewed:
                        connection.execute(
                            "INSERT INTO reviews(session_id,classification,reason,created_at,actor_id,provenance) VALUES(%s,%s,%s,%s,'demo-seeder','synthetic-demo')",
                            (
                                session_id,
                                activity,
                                "Fictional example only; explicitly assigned by the demo generator, not verified GT activity.",
                                now,
                            ),
                        )
                    for player_index in range(len(PLAYERS)):
                        connection.execute(
                            "INSERT INTO assignments(session_id,player_id,source_json) VALUES(%s,%s,%s)",
                            (
                                session_id,
                                910001 + player_index,
                                data.jsonb({"synthetic": True}),
                            ),
                        )
                        if index == 7 and player_index == 7:
                            continue  # An assigned player with no measurement record.
                        _insert_stats(
                            connection,
                            "stats",
                            session_id,
                            player_index,
                            _metrics(player_index, index),
                            now,
                        )
                    if activity == "practice":
                        elapsed = 0
                        for phase_index, (phase_title, duration) in enumerate(
                            (
                                ("Demo · Movement preparation", 15),
                                ("Demo · Advantage drills", 20),
                                ("Demo · Half-court execution", 25),
                            )
                        ):
                            phase_id = 920001 + index * 10 + phase_index
                            phase_start = start + timedelta(minutes=elapsed)
                            elapsed += duration
                            connection.execute(
                                "INSERT INTO phases(id,session_id,title,start_utc,end_utc,source_json,valid) VALUES(%s,%s,%s,%s,%s,%s,TRUE)",
                                (
                                    phase_id,
                                    session_id,
                                    phase_title,
                                    phase_start,
                                    start + timedelta(minutes=elapsed),
                                    data.jsonb({"synthetic": True}),
                                ),
                            )
                            for player_index in range(len(PLAYERS)):
                                if index == 7 and player_index == 7:
                                    continue
                                _insert_stats(
                                    connection,
                                    "phase_stats",
                                    phase_id,
                                    player_index,
                                    _metrics(
                                        player_index, index, fraction=duration / 90
                                    ),
                                    now,
                                )
                for key, value in (
                    (MANIFEST_KEY, data.canonical(manifest)),
                    ("source_identity", data.digest({"synthetic_demo": SEED_VERSION})),
                ):
                    connection.execute(
                        "INSERT INTO meta(key,value) VALUES(%s,%s)", (key, value)
                    )
                connection.execute(
                    "INSERT INTO documents(title,body,kind,content_hash,owner_id) VALUES(%s,%s,'coach_note',%s,'local')",
                    (
                        "Synthetic demo context",
                        "All names, activities and measurements in this demo are invented. The final practice intentionally has one missing player record; a zero jump count is not the same as an unavailable jump count. Do not use these examples for coaching decisions.",
                        data.digest({"demo_note": SEED_VERSION}),
                    ),
                )
                result = "seeded"
        # Reports are derived only after the seed commits. A retry after interruption
        # completes them without deleting or replacing any source data or edits.
        knowledge.initialize()
        for index in range(len(SESSIONS)):
            analytics.get_report(900001 + index)
    return {
        "status": result,
        "synthetic": True,
        **manifest,
        "players": len(PLAYERS),
        "sessions": len(SESSIONS),
    }


def main():
    try:
        result = seed()
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
