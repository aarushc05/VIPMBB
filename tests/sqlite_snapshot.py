"""Only a synthetic legacy-file fixture; SQLite is not a runtime test backend."""

import json
import sqlite3


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE players(id INTEGER PRIMARY KEY,name TEXT NOT NULL,number TEXT,active INTEGER,updated_at TEXT);
CREATE TABLE sessions(id INTEGER PRIMARY KEY,title TEXT,start_utc TEXT,end_utc TEXT,local_date TEXT,source_labels TEXT,
 classification TEXT,reviewed INTEGER,status TEXT,expected_players INTEGER,source_hash TEXT,updated_at TEXT,
 source_json TEXT,assignment_complete INTEGER,sync_complete INTEGER,removed_upstream INTEGER);
CREATE TABLE stats(session_id INTEGER,player_id INTEGER,metrics_json TEXT,missing_json TEXT,legacy INTEGER,updated_at TEXT,raw_json TEXT,PRIMARY KEY(session_id,player_id));
CREATE TABLE assignments(session_id INTEGER,player_id INTEGER,source_json TEXT,PRIMARY KEY(session_id,player_id));
CREATE TABLE phases(id INTEGER PRIMARY KEY,session_id INTEGER,title TEXT,start_utc TEXT,end_utc TEXT,source_labels TEXT,source_json TEXT,valid INTEGER);
CREATE TABLE phase_stats(phase_id INTEGER,player_id INTEGER,metrics_json TEXT,missing_json TEXT,raw_json TEXT,updated_at TEXT,PRIMARY KEY(phase_id,player_id));
CREATE TABLE reviews(id INTEGER PRIMARY KEY,session_id INTEGER,classification TEXT,reason TEXT,created_at TEXT);
CREATE TABLE reports(id INTEGER PRIMARY KEY,session_id INTEGER,version INTEGER,source_hash TEXT,payload_json TEXT,generated_at TEXT);
CREATE TABLE jobs(id INTEGER PRIMARY KEY,kind TEXT,payload_json TEXT,status TEXT,message TEXT,created_at TEXT,updated_at TEXT,attempts INTEGER,result_json TEXT);
CREATE TABLE documents(id INTEGER PRIMARY KEY,title TEXT,body TEXT,kind TEXT,updated_at TEXT,content_hash TEXT,embedding_json TEXT,embedding_model TEXT);
CREATE TABLE chat_messages(id INTEGER PRIMARY KEY,conversation_id TEXT,role TEXT,content TEXT,response_json TEXT,created_at TEXT);
"""


def create_snapshot(path):
    stamp = "2026-10-06T20:00:00+00:00"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO meta VALUES('schema_version','1')")
        conn.execute("INSERT INTO meta VALUES('source','synthetic-migration-fixture')")
        conn.execute(
            "INSERT INTO players VALUES(11,'Synthetic Avery Example','11',1,?)",
            (stamp,),
        )
        conn.execute(
            "INSERT INTO players VALUES(22,'Synthetic Blake Example','22',0,?)",
            (stamp,),
        )
        conn.execute(
            "INSERT INTO sessions VALUES(101,'SYNTHETIC migration practice','2026-10-07T00:30:00+00:00','2026-10-07T01:30:00+00:00','2026-10-06',?,'practice',1,'complete',2,'source-fixture',?, '{}',1,1,0)",
            (json.dumps(["Training"]), stamp),
        )
        for player, legacy, jump in [(11, 0, 0), (22, 1, None)]:
            metrics = {
                "minutes": 60,
                "mechanical_load": 1200,
                "distance_m": 3000,
                "load_per_minute": 20,
                "jump_count": jump,
                "accel_load": None,
                "exposure_basis": "on_playing_field"
                if not legacy
                else "legacy_unknown",
            }
            conn.execute(
                "INSERT INTO stats VALUES(101,?,?,?,?,?,?)",
                (
                    player,
                    json.dumps(metrics),
                    json.dumps(
                        ["accel_load"] + (["jump_count"] if jump is None else [])
                    ),
                    legacy,
                    stamp,
                    json.dumps({"session_id": 101}),
                ),
            )
            conn.execute("INSERT INTO assignments VALUES(101,?,'{}')", (player,))
        conn.execute(
            "INSERT INTO phases VALUES(201,101,'SYNTHETIC drill','2026-10-07T00:30:00+00:00','2026-10-07T01:00:00+00:00','[\"Drill\"]','{}',1)"
        )
        conn.execute(
            "INSERT INTO phase_stats VALUES(201,11,?,'[]','{}',?)",
            (
                json.dumps(
                    {
                        "minutes": 20,
                        "mechanical_load": 250,
                        "exposure_basis": "on_playing_field",
                    }
                ),
                stamp,
            ),
        )
        conn.execute(
            "INSERT INTO reviews VALUES(1,101,'practice','Synthetic staff review',?)",
            (stamp,),
        )
        conn.execute(
            "INSERT INTO reports VALUES(1,101,1,'synthetic-report',?,?)",
            (
                json.dumps({"session": {"id": 101}, "version": 1, "synthetic": True}),
                stamp,
            ),
        )
        conn.execute(
            "INSERT INTO jobs VALUES(1,'report','{\"session_id\":101}','completed','Synthetic historical job',?,?,1,'{\"version\":1}')",
            (stamp, stamp),
        )
        conn.execute(
            "INSERT INTO documents VALUES(1,'SYNTHETIC coaching note','Fictional note for migration testing.','coach_note',?,'synthetic-note',NULL,NULL)",
            (stamp,),
        )
        conn.execute(
            "INSERT INTO chat_messages VALUES(1,'synthetic-conversation','user','Show synthetic distance',NULL,?)",
            (stamp,),
        )
    return path
