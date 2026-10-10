"""Seed a migrated synthetic PostgreSQL QA schema, never an application DB.

Requires VIPMBB_TEST_DATABASE_URL for vipmbb_test, VIPMBB_DB_SCHEMA set to an
existing qa_<32 hex> or test_<32 hex> migrated schema, explicit VIPMBB_DATA_DIR,
and VIPMBB_IMPORT_LEGACY=0. Refuses nonempty data. All records are fictional.
"""

from datetime import datetime, timedelta
import json
import os
import re
from pathlib import Path
from pg_helpers import test_database_url


def main():
    if (
        not os.environ.get("VIPMBB_DATA_DIR")
        or os.environ.get("VIPMBB_IMPORT_LEGACY") != "0"
    ):
        raise SystemExit(
            "Set an explicit VIPMBB_DATA_DIR and VIPMBB_IMPORT_LEGACY=0 for isolated QA."
        )
    if not re.fullmatch(
        r"(?:qa|test)_[a-f0-9]{32}", os.environ.get("VIPMBB_DB_SCHEMA", "")
    ):
        raise SystemExit(
            "Set a unique migrated QA schema; public and application schemas are refused."
        )
    os.environ["DATABASE_URL"] = test_database_url()
    from server.app import data, knowledge

    if (
        Path(os.environ["VIPMBB_DATA_DIR"]).resolve()
        == (data.ROOT / ".local").resolve()
    ):
        raise SystemExit("Refusing the application's normal data directory.")
    data.initialize()
    with data.database() as conn:
        if conn.execute(
            "SELECT EXISTS(SELECT 1 FROM players) OR EXISTS(SELECT 1 FROM sessions) AS occupied"
        ).fetchone()["occupied"]:
            raise SystemExit("Refusing to overwrite a nonempty QA database.")
    knowledge.initialize()
    today = datetime.now(data.ATLANTA).date()
    with data.database() as conn:
        for pid, name, number in [
            (9001, "QA Avery Example", "91"),
            (9002, "QA Morgan Example", "92"),
        ]:
            conn.execute(
                "INSERT INTO players(id,name,number,active) VALUES(%s,%s,%s,TRUE)",
                (pid, name, number),
            )
        for index, age in enumerate((10, 7, 4, 1), start=1):
            sid, day = 9000 + index, (today - timedelta(days=age)).isoformat()
            conn.execute(
                """INSERT INTO sessions(id,title,start_utc,end_utc,local_date,source_labels,classification,reviewed,
                expected_players,assignment_complete,sync_complete,source_json)
                VALUES(%s,%s,%s,%s,%s,%s,'practice',%s,2,TRUE,TRUE,%s)""",
                (
                    sid,
                    f"SYNTHETIC QA Practice {index}",
                    day + "T18:00:00Z",
                    day + "T20:00:00Z",
                    day,
                    data.jsonb(["Training"]),
                    index < 4,
                    data.jsonb({"synthetic": True}),
                ),
            )
            for pid, load, minutes in [
                (9001, 900 + index * 150, 60),
                (9002, 600 + index * 100, 45),
            ]:
                metrics = {
                    "minutes": minutes,
                    "distance_m": 2600 + index * 100,
                    "mechanical_load": load,
                    "load_per_minute": load / minutes,
                    "exposure_basis": "on_playing_field",
                    "accel_load": load / 3,
                    "speed_max": 7.1 if pid == 9001 else 7.7,
                    "metabolic_work": None,
                    "acceleration_count": 15 + index,
                    "deceleration_count": 10 + index,
                    "change_of_direction_count": 8,
                    "jump_count": 0 if pid == 9001 else None,
                }
                conn.execute(
                    "INSERT INTO assignments(session_id,player_id) VALUES(%s,%s)",
                    (sid, pid),
                )
                conn.execute(
                    "INSERT INTO stats(session_id,player_id,metrics_json,missing_json) VALUES(%s,%s,%s,%s)",
                    (
                        sid,
                        pid,
                        data.jsonb(metrics),
                        data.jsonb([k for k, v in metrics.items() if v is None]),
                    ),
                )
            if index == 4:
                for phase, title, start, end, valid in [
                    (9101, "SYNTHETIC QA Transition drill", "18:00:00", "18:30:00", 0),
                    (9102, "SYNTHETIC QA Overlapping drill", "18:20:00", "18:45:00", 0),
                ]:
                    conn.execute(
                        "INSERT INTO phases(id,session_id,title,start_utc,end_utc,valid) VALUES(%s,%s,%s,%s,%s,%s)",
                        (
                            phase,
                            sid,
                            title,
                            f"{day}T{start}Z",
                            f"{day}T{end}Z",
                            bool(valid),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO phase_stats(phase_id,player_id,metrics_json) VALUES(%s,%s,%s)",
                        (
                            phase,
                            9001,
                            data.jsonb(
                                {
                                    "minutes": 20,
                                    "mechanical_load": 250,
                                    "distance_m": 900,
                                    "exposure_basis": "on_playing_field",
                                }
                            ),
                        ),
                    )
        conn.execute(
            "INSERT INTO meta VALUES('source','synthetic-qa') ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value"
        )
    for sid in range(9001, 9005):
        data.get_report(sid)
    knowledge.add_note(
        "SYNTHETIC QA Transition context",
        "Fictional example: the transition drill had shorter repetitions. This note is for UI testing, not a real coaching observation.",
    )
    print(
        json.dumps(
            {
                "source": "synthetic-qa",
                "sessions": [9001, 9002, 9003, 9004],
                "target_unreviewed": 9004,
                "date": (today - timedelta(days=1)).isoformat(),
            }
        )
    )


if __name__ == "__main__":
    main()
