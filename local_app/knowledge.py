"""Local, provenance-preserving retrieval. Coach notes are data, never instructions."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from . import data, models

SEEDS = [
    ("Exposure minutes", "definition", "Exposure minutes come from Kinexon time_on_playing_field when present, otherwise recording duration. These are tracked exposure, NOT official basketball playing minutes. Compare rates only when the denominator basis is the same. Missing exposure is unavailable, not zero. Legacy imports have an unverified denominator."),
    ("Mechanical load and intensity", "definition", "Mechanical load is a Kinexon movement-workload measure in vendor load units. It is not a score of effort, basketball execution, or fatigue. Load per minute is mechanical load divided by positive tracked exposure minutes. An increase in total load can reflect longer exposure rather than greater intensity."),
    ("Metabolic work and power", "definition", "Metabolic work is a tracking-derived estimate. Units and the vendor calculation remain unverified; this app shows source units, not calories or an efficiency score. Power is already a rate and must not be divided by minutes again. Lower work is not automatically better efficiency."),
    ("Practice classification", "method", "Source labels such as Training or Match do not establish verified activity type. Scheduled home-game dates and implausible boundaries require review. Reviewing a session confirms its activity type only, not completeness, sensor quality or competitive interval boundaries. Unreviewed practices must be identified as provisional."),
    ("Personal baselines", "method", "The report compares load per minute with a player's previous reviewed practices where exposure denominators match and measurements are available. It excludes the current and future sessions. A small sample is descriptive, not a diagnosis. Similar drill mix and coaching context still matter."),
    ("Missing data and coverage", "method", "Missing values are unavailable and never silently replaced with zero. Legacy cache zeros may reflect unavailable source fields and are treated conservatively. All assigned players must be retrieved, including historical players absent from the active roster. A missing record is not evidence that a player was absent or inactive."),
    ("Drills and phase totals", "method", "Phases are segments of a recording. Phase statistics can describe a drill only after its identity, participation and boundaries are checked. Overlapping phases cannot be summed without de-duplication. Whole-session totals cannot be prorated to invent five-minute or drill statistics."),
    ("What the assistant can establish", "safety", "This assistant describes recorded physical workload and exposure. It cannot diagnose injury, fatigue, effort, readiness, or prescribe substitutions from movement data alone. Basketball execution and why something changed need additional evidence and coach context. Notes are staff observations, not verified causal findings."),
    ("Date ranges and freshness", "method", "Dates use America/New_York local calendar days. Last week and last month mean real recent dates, not the most recent dates containing data. Old records remain historical. Incomplete synchronization and missing away-game coverage must not be interpreted as zero workload."),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize():
    with closing(data.connect()) as conn, conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
          kind TEXT NOT NULL, updated_at TEXT NOT NULL, content_hash TEXT NOT NULL,
          embedding_json TEXT, embedding_model TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(title,body,content='documents',content_rowid='id');
        CREATE TABLE IF NOT EXISTS chat_messages (
          id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
          content TEXT NOT NULL, response_json TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS chat_conversation ON chat_messages(conversation_id,id);
        """)
        for title, kind, body in SEEDS:
            row = conn.execute("SELECT id,content_hash FROM documents WHERE title=? AND kind=?", (title, kind)).fetchone()
            digest = hashlib.sha256(body.encode()).hexdigest()
            if not row:
                conn.execute("INSERT INTO documents(title,body,kind,updated_at,content_hash) VALUES(?,?,?,?,?)", (title,body,kind,now(),digest))
            elif row["content_hash"] != digest:
                conn.execute("UPDATE documents SET body=?,updated_at=?,content_hash=?,embedding_json=NULL,embedding_model=NULL WHERE id=?",(body,now(),digest,row["id"]))
        conn.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")


def list_documents():
    with closing(data.connect()) as conn:
        return {"documents":[dict(r) for r in conn.execute("SELECT id,title,body,kind,updated_at FROM documents ORDER BY kind,title")]}


def add_note(title, body):
    title, body = title.strip(), body.strip()
    if not title or not body or len(title)>160 or len(body)>12000:
        raise ValueError("A note needs a title (up to 160 characters) and body (up to 12,000 characters).")
    with closing(data.connect()) as conn, conn:
        cursor=conn.execute("INSERT INTO documents(title,body,kind,updated_at,content_hash) VALUES(?,?,?,?,?)",
                            (title,body,"coach_note",now(),hashlib.sha256(body.encode()).hexdigest()))
        identifier=cursor.lastrowid
        conn.execute("INSERT INTO documents_fts(rowid,title,body) VALUES(?,?,?)", (identifier,title,body))
        return dict(conn.execute("SELECT id,title,body,kind,updated_at FROM documents WHERE id=?",(identifier,)).fetchone())


def index_documents():
    with closing(data.connect()) as conn:
        rows=[dict(r) for r in conn.execute("SELECT * FROM documents WHERE embedding_json IS NULL OR embedding_model!=?",(models.EMBED_MODEL,))]
    count=0
    for start in range(0,len(rows),8):
        batch=rows[start:start+8]
        vectors=models.embed([r["title"]+"\n"+r["body"] for r in batch])
        with closing(data.connect()) as conn, conn:
            for row,vector in zip(batch,vectors):
                if not vector or not all(isinstance(n,(float,int)) and math.isfinite(n) for n in vector):
                    raise ValueError("Invalid embedding result")
                conn.execute("UPDATE documents SET embedding_json=?,embedding_model=? WHERE id=? AND content_hash=?",
                             (json.dumps(vector),models.EMBED_MODEL,row["id"],row["content_hash"]))
                count+=1
    return {"indexed":count,"model":models.EMBED_MODEL}


def retrieve(query, limit=4, kind=None):
    tokens=[t for t in re.findall(r"[a-zA-Z0-9]+",query.lower()) if len(t)>2]
    lexical={}
    with closing(data.connect()) as conn:
        if tokens:
            match=" OR ".join('"'+t+'"' for t in tokens[:30])
            for rank,row in enumerate(conn.execute("SELECT d.id AS rowid FROM documents_fts JOIN documents d ON d.id=documents_fts.rowid WHERE documents_fts MATCH ? AND (? IS NULL OR d.kind=?) ORDER BY bm25(documents_fts) LIMIT 12",(match,kind,kind))):
                lexical[row["rowid"]]=1/(rank+1)
        rows=[dict(r) for r in conn.execute("SELECT * FROM documents WHERE (? IS NULL OR kind=?)", (kind, kind))]
    vector=None
    if any(r["embedding_json"] and r["embedding_model"]==models.EMBED_MODEL for r in rows) and not models.disabled():
        try:
            vector=models.embed([query])[0]
        except Exception:
            pass  # Lexical fallback is complete and explicitly surfaced by model status.
    scored=[]
    for row in rows:
        score=lexical.get(row["id"],0)
        if vector is not None and row["embedding_json"] and row["embedding_model"]==models.EMBED_MODEL:
            stored=json.loads(row["embedding_json"])
            if len(stored)==len(vector):
                similarity=sum(a*b for a,b in zip(vector,stored))
                if similarity>0.25:
                    score+=similarity
        if score>0:
            scored.append((score,row))
    scored.sort(key=lambda x:(-x[0],x[1]["id"]))
    return [{k:r[k] for k in ("id","title","body","kind","updated_at")} for _,r in scored[:limit]]
