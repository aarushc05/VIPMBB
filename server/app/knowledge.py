"""Local, provenance-preserving retrieval. Coach notes are data, never instructions."""

from __future__ import annotations

import hashlib
import math
import re
import httpx

from . import data, models

SEEDS = [
    (
        "Exposure minutes",
        "definition",
        "Exposure minutes come from Kinexon time_on_playing_field when present, otherwise recording duration. These are tracked exposure, NOT official basketball playing minutes. Compare rates only when the denominator basis is the same. Missing exposure is unavailable, not zero. Legacy imports have an unverified denominator.",
    ),
    (
        "Mechanical load and intensity",
        "definition",
        "Mechanical load is a Kinexon movement-workload measure in vendor load units. It is not a score of effort, basketball execution, or fatigue. Load per minute is mechanical load divided by positive tracked exposure minutes. An increase in total load can reflect longer exposure rather than greater intensity.",
    ),
    (
        "Metabolic work and power",
        "definition",
        "Metabolic work is a tracking-derived estimate. Units and the vendor calculation remain unverified; this app shows source units, not calories or an efficiency score. Power is already a rate and must not be divided by minutes again. Lower work is not automatically better efficiency.",
    ),
    (
        "Practice classification",
        "method",
        "Source labels such as Training or Match do not establish verified activity type. Scheduled home-game dates and implausible boundaries require review. Reviewing a session confirms its activity type only, not completeness, sensor quality or competitive interval boundaries. Unreviewed practices must be identified as provisional.",
    ),
    (
        "Personal baselines",
        "method",
        "The report compares load per minute with a player's previous reviewed practices where exposure denominators match and measurements are available. It excludes the current and future sessions. A small sample is descriptive, not a diagnosis. Similar drill mix and coaching context still matter.",
    ),
    (
        "Missing data and coverage",
        "method",
        "Missing values are unavailable and never silently replaced with zero. Legacy cache zeros may reflect unavailable source fields and are treated conservatively. All assigned players must be retrieved, including historical players absent from the active roster. A missing record is not evidence that a player was absent or inactive.",
    ),
    (
        "Drills and phase totals",
        "method",
        "Phases are segments of a recording. Phase statistics can describe a drill only after its identity, participation and boundaries are checked. Overlapping phases cannot be summed without de-duplication. Whole-session totals cannot be prorated to invent five-minute or drill statistics.",
    ),
    (
        "What the assistant can establish",
        "safety",
        "This assistant describes recorded physical workload and exposure. It cannot diagnose injury, fatigue, effort, readiness, or prescribe substitutions from movement data alone. Basketball execution and why something changed need additional evidence and coach context. Notes are staff observations, not verified causal findings.",
    ),
    (
        "Date ranges and freshness",
        "method",
        "Dates use America/New_York local calendar days. Last week and last month mean real recent dates, not the most recent dates containing data. Old records remain historical. Incomplete synchronization and missing away-game coverage must not be interpreted as zero workload.",
    ),
]


def initialize():
    """Seed definitions after migrations; no startup DDL or global FTS rebuild."""
    with data.database() as conn:
        for title, kind, body in SEEDS:
            digest = hashlib.sha256(body.encode()).hexdigest()
            conn.execute(
                """INSERT INTO documents(title,body,kind,content_hash,owner_id)
                VALUES(%s,%s,%s,%s,NULL)
                ON CONFLICT(title,kind) WHERE kind<>'coach_note' DO UPDATE
                SET body=EXCLUDED.body,updated_at=now(),content_hash=EXCLUDED.content_hash,
                    embedding_json=NULL,embedding_model=NULL,embedding=NULL,embedding_dimensions=NULL
                WHERE documents.content_hash<>EXCLUDED.content_hash""",
                (title, body, kind, digest),
            )


def list_documents(owner_id="local"):
    with data.database() as conn:
        rows = conn.execute(
            """SELECT id,title,body,kind,updated_at FROM documents
            WHERE owner_id IS NULL OR owner_id=%s ORDER BY kind,title COLLATE "C" """,
            (owner_id,),
        ).fetchall()
        return {"documents": data.public_value(rows)}


def add_note(title, body, owner_id="local"):
    title, body = title.strip(), body.strip()
    if not title or not body or len(title) > 160 or len(body) > 12000:
        raise ValueError(
            "A note needs a title (up to 160 characters) and body (up to 12,000 characters)."
        )
    with data.database() as conn:
        row = conn.execute(
            """INSERT INTO documents(title,body,kind,content_hash,owner_id)
            VALUES(%s,%s,'coach_note',%s,%s) RETURNING id,title,body,kind,updated_at""",
            (title, body, hashlib.sha256(body.encode()).hexdigest(), owner_id),
        ).fetchone()
        return data.public_value(row)


def vector_literal(vector):
    if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 16000:
        raise ValueError("Invalid embedding dimensions.")
    if not all(
        isinstance(n, (float, int)) and not isinstance(n, bool) and math.isfinite(n)
        for n in vector
    ):
        raise ValueError("Invalid embedding values.")
    if not any(n != 0 for n in vector):
        raise ValueError("Zero-norm embeddings cannot be used for cosine retrieval.")
    return "[" + ",".join(str(float(n)) for n in vector) + "]"


def index_documents():
    with data.database() as conn:
        rows = conn.execute(
            """SELECT * FROM documents
            WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s ORDER BY id""",
            (models.EMBED_MODEL,),
        ).fetchall()
        known = conn.execute(
            """SELECT DISTINCT embedding_dimensions AS dimensions FROM documents
            WHERE embedding IS NOT NULL AND embedding_model=%s""",
            (models.EMBED_MODEL,),
        ).fetchall()
    dimensions = {r["dimensions"] for r in known}
    if len(dimensions) > 1:
        raise ValueError("Stored embedding dimensions disagree; reindex this model.")
    expected = next(iter(dimensions), None)
    count = 0
    for start in range(0, len(rows), 8):
        batch = rows[start : start + 8]
        vectors = models.embed([r["title"] + "\n" + r["body"] for r in batch])
        if len(vectors) != len(batch):
            raise ValueError("Embedding response did not cover every document.")
        literals = [vector_literal(vector) for vector in vectors]
        sizes = {len(vector) for vector in vectors}
        if len(sizes) != 1 or (expected is not None and sizes != {expected}):
            raise ValueError("Embedding dimensions do not match the selected model.")
        expected = next(iter(sizes))
        with data.database() as conn:
            for row, vector, literal in zip(batch, vectors, literals):
                changed = conn.execute(
                    """UPDATE documents SET embedding_json=%s,embedding_model=%s,
                    embedding=%s::public.vector,embedding_dimensions=%s WHERE id=%s AND content_hash=%s""",
                    (
                        data.jsonb(vector),
                        models.EMBED_MODEL,
                        literal,
                        len(vector),
                        row["id"],
                        row["content_hash"],
                    ),
                ).rowcount
                count += changed
    return {"indexed": count, "model": models.EMBED_MODEL, "dimensions": expected}


def retrieve(query, limit=4, kind=None, owner_id="local"):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Retrieval limit must be between 1 and 100.")
    tokens = [t for t in re.findall(r"[a-zA-Z0-9]+", query.lower()) if len(t) > 2][:30]
    scores = {}
    with data.database() as conn:
        if tokens:
            rows = conn.execute(
                """SELECT id,ts_rank_cd(search_vector,to_tsquery('english',%s)) AS rank
                FROM documents WHERE search_vector @@ to_tsquery('english',%s)
                AND (%s::text IS NULL OR kind=%s) AND (owner_id IS NULL OR owner_id=%s)
                ORDER BY rank DESC,id LIMIT 12""",
                (" | ".join(tokens), " | ".join(tokens), kind, kind, owner_id),
            ).fetchall()
            scores = {row["id"]: 1 / (rank + 1) for rank, row in enumerate(rows)}
        indexed = conn.execute(
            """SELECT id FROM documents WHERE embedding IS NOT NULL
            AND embedding_model=%s AND (%s::text IS NULL OR kind=%s)
            AND (owner_id IS NULL OR owner_id=%s) LIMIT 1""",
            (models.EMBED_MODEL, kind, kind, owner_id),
        ).fetchone()
    if indexed and not models.disabled():
        try:
            vector = models.embed([query])[0]
            literal = vector_literal(vector)
            with data.database() as conn:
                rows = conn.execute(
                    """SELECT id,1-(embedding <=> %s::public.vector) AS similarity
                    FROM documents WHERE embedding IS NOT NULL AND embedding_model=%s
                    AND embedding_dimensions=%s AND (%s::text IS NULL OR kind=%s)
                    AND (owner_id IS NULL OR owner_id=%s)
                    ORDER BY embedding <=> %s::public.vector,id LIMIT 12""",
                    (
                        literal,
                        models.EMBED_MODEL,
                        len(vector),
                        kind,
                        kind,
                        owner_id,
                        literal,
                    ),
                ).fetchall()
                for row in rows:
                    if row["similarity"] > 0.25:
                        scores[row["id"]] = scores.get(row["id"], 0) + row["similarity"]
        except (ValueError, RuntimeError, IndexError, KeyError, httpx.HTTPError):
            pass  # Invalid/offline model keeps a complete lexical fallback.
    if not scores:
        return []
    with data.database() as conn:
        rows = conn.execute(
            """SELECT id,title,body,kind,updated_at FROM documents
            WHERE id=ANY(%s) AND (owner_id IS NULL OR owner_id=%s)""",
            (list(scores), owner_id),
        ).fetchall()
    rows.sort(key=lambda row: (-scores[row["id"]], row["id"]))
    return data.public_value(rows[:limit])
