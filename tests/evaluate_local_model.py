"""Opt-in real Ollama evaluation, isolated from every production database.

Run from the project root: PYTHONPATH=. .venv/bin/python tests/evaluate_local_model.py
Requires the local qwen3:4b model. Does not contact Kinexon or read credentials.
This is deliberately not collected by the offline pytest suite.
"""
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import time


def main():
    with tempfile.TemporaryDirectory(prefix="vipmbb-model-evaluation-") as folder:
        os.environ["VIPMBB_DATA_DIR"] = folder
        os.environ["VIPMBB_IMPORT_LEGACY"] = "0"
        os.environ.pop("VIPMBB_DISABLE_MODEL", None)
        from local_app import chat, data, knowledge, models
        chat.today = lambda: date(2026, 10, 7)
        data.initialize()
        knowledge.initialize()
        runtime = models.status(refresh=True)
        if not runtime["available"]:
            raise SystemExit("The configured local model is unavailable; no evaluation was performed.")
        raw_plans = []
        original_chat_json = models.chat_json
        def capture_plan(*args, **kwargs):
            raw = original_chat_json(*args, **kwargs)
            raw_plans.append(raw)
            return raw
        models.chat_json = capture_plan
        with data.database() as conn:
            for pid, name in [(11, "Alex Rivera"), (22, "Blake Chen")]:
                conn.execute("INSERT INTO players(id,name,active) VALUES(?,?,1)", (pid, name))
            conn.execute("INSERT INTO players(id,name,active) VALUES(33,'QA Avery Example',1)")
            for sid, day in [(1, "2026-09-20"), (2, "2026-09-24"), (3, "2026-09-28"),
                             (4, "2026-10-01"), (5, "2026-10-06"), (6, "2026-11-01")]:
                conn.execute("INSERT INTO sessions(id,title,start_utc,end_utc,local_date,classification,reviewed,expected_players) VALUES(?,?,?,?,?,'practice',1,2)",
                             (sid, f"Synthetic practice {sid}", day + "T18:00:00Z", day + "T20:00:00Z", day))
                for pid, minutes, load, distance, speed in [(11, 60, 1200, 3000, 7.2), (22, 30, 300, 1800, 8.1)]:
                    metrics = {"minutes": minutes, "mechanical_load": load, "distance_m": distance,
                               "speed_max": speed, "jump_count": 0 if pid == 11 else None,
                               "exposure_basis": "on_playing_field"}
                    conn.execute("INSERT INTO stats(session_id,player_id,metrics_json) VALUES(?,?,?)", (sid, pid, json.dumps(metrics)))
        def values(result):
            return {row["player_id"]: row["value"] for row in result.get("table", {}).get("rows", [])}
        cases = [
            ("Who had the highest load per minute in this practice?", 5,
             lambda r: values(r) == {11: 20, 22: 10} and "Alex Rivera" in r["answer"]),
            ("Compare Alex Rivera and Blake Chen's distance in this practice", 5,
             lambda r: values(r) == {11: 3000, 22: 1800}),
            ("Show Alex Rivera's distance over the last five practices", None,
             lambda r: r.get("query", {}).get("intent") == "trend" and len(r.get("table", {}).get("rows", [])) == 5
             and all(row["player_id"] == 11 and row["value"] == 3000 for row in r["table"]["rows"])),
            ("What does mechanical load mean?", None,
             lambda r: bool(r["sources"]) and all(s["type"] == "document" for s in r["sources"])),
            ("Compare Jordan Poole's workload in this practice", 5,
             lambda r: not r.get("table") and any(word in r["answer"].lower() for word in ("player", "match", "name"))),
            ("Show distance last week", None,
             lambda r: r.get("query", {}).get("start") == "2026-10-01" and r["query"]["end"] == "2026-10-07"
             and values(r) == {11: 6000, 22: 3600}),
            ("Who had the lowest jump count in this practice?", 5,
             lambda r: values(r) == {11: 0, 22: None} and "Alex Rivera" in r["answer"]),
            ("Who was fastest across practices from 2026-10-01 to 2026-10-06?", None,
             lambda r: values(r) == {11: 7.2, 22: 8.1} and "Blake Chen" in r["answer"]),
            ("What data is missing from this session?", 5,
             lambda r: r.get("query", {}).get("intent") == "coverage"
             and len(r.get("table", {}).get("rows", [])) == 2),
            ("Show QA Avery Example's distance in this practice", 5,
             lambda r: r.get("query", {}).get("player_ids") == [33]
             and "unavailable" in r["answer"].lower()),
        ]
        results = []
        for question, session_id, correct in cases:
            began = time.monotonic()
            raw_plans.clear()
            result = chat.answer(question, session_id)
            try:
                passed = correct(result)
            except (KeyError, TypeError):
                passed = False
            row = {"question": question, "passed": passed, "seconds": round(time.monotonic() - began, 2),
                   "mode": result["mode"], "query": result.get("query"), "answer": result["answer"],
                   "rows": result.get("table", {}).get("rows", []), "sources": result["sources"], "raw_plans": list(raw_plans)}
            results.append(row)
            print(json.dumps(row), flush=True)
        summary = {"passed": sum(row["passed"] for row in results), "total": len(results),
                   "model_answers": sum(row["mode"] == "local-model" for row in results),
                   "total_seconds": round(sum(row["seconds"] for row in results), 2)}
        print(json.dumps({"summary": summary}), flush=True)
        return 0 if summary["passed"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
