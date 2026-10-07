"""Create a deliberately synthetic UI QA store, never the default private store.

VIPMBB_DATA_DIR=/explicit/private/qa VIPMBB_IMPORT_LEGACY=0 PYTHONPATH=. \
  .venv/bin/python tests/seed_ui_fixture.py
Refuses a nonempty database. Names and measurements are fictional.
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path


def main():
    if not os.environ.get("VIPMBB_DATA_DIR") or os.environ.get("VIPMBB_IMPORT_LEGACY") != "0":
        raise SystemExit("Set an explicit VIPMBB_DATA_DIR and VIPMBB_IMPORT_LEGACY=0 for isolated QA.")
    from local_app import data, knowledge
    if Path(os.environ["VIPMBB_DATA_DIR"]).resolve() == (data.ROOT / ".local").resolve():
        raise SystemExit("Refusing the application's normal data directory.")
    data.initialize()
    if data.status_data()["sessions"]:
        raise SystemExit("Refusing to overwrite a nonempty QA database.")
    knowledge.initialize()
    today = datetime.now(data.ATLANTA).date()
    with data.database() as conn:
        for pid, name, number in [(9001, "QA Avery Example", "91"), (9002, "QA Morgan Example", "92")]:
            conn.execute("INSERT INTO players(id,name,number,active) VALUES(?,?,?,1)", (pid, name, number))
        for index, age in enumerate((10, 7, 4, 1), start=1):
            sid, day = 9000 + index, (today - timedelta(days=age)).isoformat()
            conn.execute("""INSERT INTO sessions(id,title,start_utc,end_utc,local_date,source_labels,classification,reviewed,
                expected_players,assignment_complete,sync_complete,source_json)
                VALUES(?,?,?,?,?,?,'practice',?,2,1,1,?)""",
                         (sid, f"SYNTHETIC QA Practice {index}", day + "T18:00:00Z", day + "T20:00:00Z", day,
                          json.dumps(["Training"]), int(index < 4), json.dumps({"synthetic": True})))
            for pid, load, minutes in [(9001, 900 + index * 150, 60), (9002, 600 + index * 100, 45)]:
                metrics = {"minutes": minutes, "distance_m": 2600 + index * 100, "mechanical_load": load,
                           "load_per_minute": load / minutes, "exposure_basis": "on_playing_field",
                           "accel_load": load / 3, "speed_max": 7.1 if pid == 9001 else 7.7,
                           "metabolic_work": None, "acceleration_count": 15 + index,
                           "deceleration_count": 10 + index, "change_of_direction_count": 8,
                           "jump_count": 0 if pid == 9001 else None}
                conn.execute("INSERT INTO assignments(session_id,player_id) VALUES(?,?)", (sid, pid))
                conn.execute("INSERT INTO stats(session_id,player_id,metrics_json,missing_json) VALUES(?,?,?,?)",
                             (sid, pid, json.dumps(metrics), json.dumps([k for k,v in metrics.items() if v is None])))
            if index == 4:
                for phase, title, start, end, valid in [(9101, "SYNTHETIC QA Transition drill", "18:00:00", "18:30:00", 0),
                                                       (9102, "SYNTHETIC QA Overlapping drill", "18:20:00", "18:45:00", 0)]:
                    conn.execute("INSERT INTO phases(id,session_id,title,start_utc,end_utc,valid) VALUES(?,?,?,?,?,?)",
                                 (phase, sid, title, f"{day}T{start}Z", f"{day}T{end}Z", valid))
                    conn.execute("INSERT INTO phase_stats(phase_id,player_id,metrics_json) VALUES(?,?,?)",
                                 (phase, 9001, json.dumps({"minutes": 20, "mechanical_load": 250, "distance_m": 900,
                                                        "exposure_basis": "on_playing_field"})))
        conn.execute("INSERT OR REPLACE INTO meta VALUES('source','synthetic-qa')")
    for sid in range(9001, 9005):
        data.get_report(sid)
    knowledge.add_note("SYNTHETIC QA Transition context", "Fictional example: the transition drill had shorter repetitions. This note is for UI testing, not a real coaching observation.")
    print(json.dumps({"source": "synthetic-qa", "sessions": [9001, 9002, 9003, 9004],
                      "target_unreviewed": 9004, "date": (today - timedelta(days=1)).isoformat()}))


if __name__ == "__main__":
    main()
