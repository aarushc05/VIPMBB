"""Loopback-only practice intelligence API and production frontend."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import html
import re
from pathlib import Path
import secrets
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import chat, data, knowledge, models, sync, worker


class BoundedBody:
    """Bound streamed writes too; Content-Length is not a trusted size limit."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > 20000:
                return await JSONResponse({"detail": "Request is too large."}, status_code=413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        sent = False
        async def bounded_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()
        return await self.app(scope, bounded_receive, send)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Review(StrictBody):
    classification: str
    reason: str = Field(min_length=1, max_length=2000)


class SyncRange(StrictBody):
    start: str | None = None
    end: str | None = None


class Note(StrictBody):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=12000)


class Question(StrictBody):
    message: str = Field(min_length=1, max_length=2000)
    session_id: int | None = Field(default=None, gt=0)
    conversation_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class Conversation(StrictBody):
    conversation_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


def _local_origin(value, ports=(8000, 8001)):
    try:
        parsed = urlparse(value)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port in ports and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
    except ValueError:
        return False


def _print_report(report):
    escape = lambda value: html.escape(str(value), quote=True)
    session = report["session"]
    def number(value):
        return "Unavailable" if value is None else f"{value:,.2f}"
    columns = [(key, label, unit) for key, (label, unit) in chat.SPECS.items()]
    def table(players):
        heads = "".join(f"<th>{escape(label)}<small>{escape(unit)}</small></th>" for _, label, unit in columns)
        rows = "".join("<tr><th>"+escape(p.get("name", p.get("id", "Player")))+"</th>"+"".join(f"<td>{number(p.get('metrics', {}).get(key))}</td>" for key, _, _ in columns)+"</tr>" for p in players)
        return f"<div class='wide'><table><thead><tr><th>Player</th>{heads}</tr></thead><tbody>{rows}</tbody></table></div>"
    warnings = "".join(f"<li>{escape(w)}</li>" for w in report["warnings"])
    observations = "".join(f"<p><strong>{escape(o['title'])}</strong> — {escape(o['body'])}</p>" for o in report["observations"])
    definitions = "".join(f"<p><strong>{escape(d['title'])} ({escape(d['unit'])})</strong> {escape(d['body'])}</p>" for d in report["definitions"])
    drills = "".join(f"<h3>{escape(d['title'])}</h3><p>{escape(d['start'])} — {escape(d['end'])} · {d['player_count']} recorded players · {'valid bounds' if d.get('valid') else 'bounds need review'}</p>"+table(d.get("players", [])) for d in report["drills"])
    baselines = "".join(f"<tr><th>{escape(p['name'])}</th><td>{p['baseline']['sample_count']}</td><td>{number(p['baseline']['load_per_minute'])}</td><td>{number(p['baseline']['change_pct'])}</td><td>{escape(p['baseline'].get('reason') or p['baseline']['method'])}</td></tr>" for p in report["players"])
    review_log = "".join(f"<li>{escape(r['created_at'])}: {escape(r['classification'])} — {escape(r['reason'])}</li>" for r in report["reviews"])
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Practice report {session['id']}</title>
<style>body{{font:14px system-ui;color:#142b3c;margin:32px}}h1{{font-size:28px}}h2{{margin-top:32px}}small{{display:block;font-weight:normal}}table{{border-collapse:collapse;font-size:11px;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:right}}th:first-child{{text-align:left}}.wide{{overflow:auto}}.warning{{background:#fff7df;padding:16px}}a{{color:#003057}}@media print{{body{{margin:10mm;font-size:11px}}.screen{{display:none}}table{{font-size:8px}}td,th{{padding:4px}}@page{{size:landscape}}}}</style></head><body>
<p class='screen'>Use your browser’s Print command to save this complete report as a PDF. <a href='/reports/{session['id']}'>Back to report</a></p>
<p>GEORGIA TECH · LOCAL PRACTICE INTELLIGENCE</p><h1>{escape(session['title'])}</h1><p>{escape(session['date'])} · {escape(session['classification'])} · {'reviewed' if session['reviewed'] else 'unreviewed'} · {escape(session['status'])} · report v{report['version']}</p>
<p>Generated {escape(report['generated_at'])}. {report['coverage']['recorded_players']} recorded players / {escape(report['coverage']['expected_players'] if report['coverage']['expected_players'] is not None else 'unknown')} assigned. Times below are UTC; session date is America/New_York.</p>
<div class='warning'><strong>Coverage and interpretation</strong><ul>{warnings or '<li>No current report warnings.</li>'}</ul></div>
<h2>Coaching observations</h2>{observations}<h2>All recorded players</h2>{table(report['players'])}
<h2>Individual baselines</h2><table><tr><th>Player</th><th>Prior practices</th><th>Load/min baseline</th><th>Change %</th><th>Method / limitation</th></tr>{baselines}</table>
<h2>Phases</h2>{drills or '<p>No phase statistics available.</p>'}<h2>Definitions</h2>{definitions}<h2>Classification audit</h2><ul>{review_log or '<li>Not yet reviewed.</li>'}</ul></body></html>"""


def create_app(local_ports=(8000, 8001)):
    token = secrets.token_urlsafe(32)
    @asynccontextmanager
    async def lifespan(_app):
        data.initialize()
        knowledge.initialize()
        yield

    app = FastAPI(title="VIP-MBB Local", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(BoundedBody)

    @app.middleware("http")
    async def protect_local_data(request: Request, call_next):
        if not _local_origin("http://"+request.headers.get("host", ""), local_ports):
            return JSONResponse({"detail": "This application only accepts local loopback requests."}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and not _local_origin(origin, local_ports):
            return JSONResponse({"detail": "Cross-site access to private local data is not allowed."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site access is not allowed."}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(request.headers.get("X-VIPMBB-CSRF", ""), token):
                return JSONResponse({"detail": "Security token missing or expired. Reload the page and try again."}, status_code=403)
            size = request.headers.get("content-length", "0")
            if not size.isdigit() or int(size) > 20000:
                return JSONResponse({"detail": "Request is too large."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid_input(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing_record(_request, _error):
        return JSONResponse({"detail": "The requested record was not found."}, status_code=404)

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _error):
        return JSONResponse({"detail": "The local operation failed. Existing records were retained. Check System status and retry."}, status_code=500)

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"csrf_token": token}

    @app.get("/api/status")
    def status():
        stored = data.status_data()
        warnings = []
        if stored.get("legacy_records", 0):
            warnings.append("Historical cache imported. Refresh dates from Kinexon to establish source measurements and assigned-player coverage.")
        if stored["latest"] and stored["latest"] < (chat.today()-timedelta(days=30)).isoformat():
            warnings.append("The newest stored session is more than 30 days old. Recent empty dates do not imply zero activity.")
        return {"today": chat.today().isoformat(), "timezone": "America/New_York", "data": stored,
                "credentials_configured": sync.credentials_configured(), "worker": worker.worker_status(),
                "model": models.status(), "jobs": data.list_jobs()["jobs"], "warnings": warnings}

    @app.get("/api/sessions")
    def sessions(start: str | None = None, end: str | None = None, classification: str | None = None,
                 q: str | None = None, limit: int = 100, offset: int = 0):
        return data.list_sessions(start or None, end or None, classification, q, limit, offset)

    @app.get("/api/players")
    def players():
        return data.list_players()

    @app.get("/api/reports/{session_id}")
    def report(session_id: int):
        return data.get_report(session_id)

    @app.post("/api/reports/{session_id}/regenerate")
    def regenerate(session_id: int):
        return data.regenerate_report(session_id)

    @app.get("/api/reports/{session_id}/print", response_class=HTMLResponse)
    def printable(session_id: int):
        return _print_report(data.get_report(session_id))

    @app.post("/api/sessions/{session_id}/review")
    def review(session_id: int, body: Review):
        return data.review_session(session_id, body.classification, body.reason)

    @app.post("/api/sync")
    def enqueue_sync(body: SyncRange):
        data.validate_range(body.start, body.end)
        if not sync.credentials_configured():
            raise HTTPException(409, "Add the Kinexon credentials to the local .env file before syncing.")
        effective_end = body.end or chat.today().isoformat()
        if body.start and (datetime.fromisoformat(effective_end)-datetime.fromisoformat(body.start)).days > 366:
            raise ValueError("Sync at most one year at a time.")
        return {"job": data.enqueue_job("sync", body.model_dump(exclude_none=True))}

    @app.get("/api/jobs")
    def jobs():
        return data.list_jobs()

    @app.post("/api/backup")
    def backup():
        return data.backup()

    @app.get("/api/knowledge")
    def documents():
        return knowledge.list_documents()

    @app.post("/api/knowledge")
    def note(body: Note):
        result = knowledge.add_note(body.title, body.body)
        if models.EMBED_MODEL in models.status()["installed_models"]:
            data.enqueue_job("index", {})
        return result

    @app.post("/api/model/index")
    def index():
        return {"job": data.enqueue_job("index", {})}

    @app.post("/api/chat")
    def ask(body: Question):
        return chat.answer(body.message, body.session_id, body.conversation_id)

    @app.get("/api/chat/history")
    def history(conversation_id: str = ""):
        return chat.get_history(conversation_id)

    @app.post("/api/chat/clear")
    def clear(body: Conversation):
        return chat.clear_history(body.conversation_id)

    @app.get("/{path:path}")
    def frontend(path: str):
        build = data.ROOT / "web/dist"
        # Only built app files and explicit client routes, never repo/data directories.
        if path.startswith("assets/") and not any(part.startswith(".") for part in Path(path).parts):
            target = (build/path).resolve()
            if target.is_relative_to(build.resolve()) and target.is_file():
                return FileResponse(target)
        if path == "" or path in {"practices", "assistant", "knowledge", "system"} or re.fullmatch(r"reports/\d+", path):
            if (build/"index.html").is_file():
                return FileResponse(build/"index.html")
            return HTMLResponse("<h1>VIP-MBB backend is ready</h1><p>Open the development interface at <a href='http://127.0.0.1:8001'>127.0.0.1:8001</a>, or run npm run build.</p>")
        raise HTTPException(404, "Not found.")

    return app


app = create_app()
