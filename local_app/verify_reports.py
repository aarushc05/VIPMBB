"""Compare recomputed PostgreSQL reports against the readonly SQLite snapshots.

Only aggregates and session IDs are emitted, never names or measurements. Report
generation is the normal deterministic publication path, not a review or edit.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from . import data


def semantic(report):
    result={key:value for key,value in report.items() if key not in {'generated_at','version'}}
    result['session']={key:value for key,value in result['session'].items() if key not in {'updated_at','report_version'}}
    return result


def verify(path):
    path=Path(path).expanduser().resolve(strict=True)
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Source has a nonempty WAL; use a safely checkpointed snapshot.')
    source=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        rows=source.execute('''SELECT r.session_id,r.payload_json FROM reports r
            JOIN (SELECT session_id,MAX(version) AS version FROM reports GROUP BY session_id) latest
            ON latest.session_id=r.session_id AND latest.version=r.version ORDER BY r.session_id''').fetchall()
        count=source.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        mismatches=[]
        changed_versions=[]
        for identifier,payload in rows:
            expected=json.loads(payload)
            actual=data.get_report(identifier)
            if data.canonical(semantic(expected))!=data.canonical(semantic(actual)):
                differing=[key for key in semantic(expected).keys()|semantic(actual).keys()
                           if data.canonical(semantic(expected).get(key))!=data.canonical(semantic(actual).get(key))]
                mismatches.append({'session_id':identifier,'fields':sorted(differing)})
            if actual['version']!=expected['version']:
                changed_versions.append(identifier)
        return {'sessions':count,'reports_checked':len(rows),'sessions_without_source_report':count-len(rows),
                'semantic_matches':len(rows)-len(mismatches),'mismatches':mismatches,
                'changed_version_session_ids':changed_versions,'passed':not mismatches and count==len(rows)}
    finally:
        source.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    args=parser.parse_args()
    try:
        data.initialize()
        result=verify(args.source)
    except Exception:
        parser.exit(1,'Report parity check could not complete. No private values were logged.\n')
    print(json.dumps(result,sort_keys=True))
    if not result['passed']:
        raise SystemExit(1)

if __name__=='__main__':
    main()
