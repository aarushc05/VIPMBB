"""Local is not equivalent to safe: reject hostile browsers and accidental exposure."""

import pytest


def test_status_is_real_and_does_not_expose_secrets(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    result = response.json()
    assert result["timezone"] == "America/New_York"
    assert result["data"]["sessions"] == 0
    assert result["model"]["available"] is False
    assert "password" not in result and "api_key" not in result


def test_host_header_blocks_dns_rebinding(client):
    response = client.get("/api/status", headers={"Host": "attacker.example"})
    assert response.status_code in (400, 403)


@pytest.mark.parametrize(
    "origin",
    ["https://attacker.example", "http://127.0.0.1.attacker.example:8000", "null"],
)
def test_hostile_origin_cannot_read_player_data(client, origin):
    assert client.get("/api/players", headers={"Origin": origin}).status_code == 403


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/chat", {"message": "Show practices"}),
        ("/api/knowledge", {"title": "A note", "body": "Plain text"}),
        ("/api/sync", {}),
        ("/api/backup", {}),
        ("/api/model/index", {}),
        ("/api/chat/clear", {"conversation_id": "test"}),
    ],
)
def test_all_writes_require_csrf_even_without_origin(client, path, body):
    assert client.post(path, json=body).status_code == 403


def test_bad_token_is_rejected(client, csrf):
    csrf["X-VIPMBB-CSRF"] = "wrong-token"
    assert client.post("/api/backup", json={}, headers=csrf).status_code == 403


def test_valid_token_does_not_override_hostile_origin(client, csrf):
    csrf["Origin"] = "https://attacker.example"
    assert client.post("/api/backup", json={}, headers=csrf).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/.local/practice.sqlite3",
        "/.env",
        "/api/../.env",
        "/data/kinexon.local.sqlite3",
    ],
)
def test_private_paths_are_never_served(client, path):
    response = client.get(path)
    assert response.status_code in (400, 403, 404)
    assert not response.content.startswith(b"SQLite format 3")


def test_valid_empty_report_request_returns_not_found(client):
    assert client.get("/api/reports/99999").status_code == 404


@pytest.mark.parametrize(
    "query",
    ["start=not-a-date", "start=2026-10-07&end=2026-10-01", "limit=-1", "offset=-1"],
)
def test_bad_date_and_pagination_inputs_are_rejected(client, query):
    assert client.get("/api/sessions?" + query).status_code in (400, 422)


def test_note_is_plain_text_not_executable_html(client, csrf):
    text = '<script>fetch("https://attacker.example")</script>'
    response = client.post(
        "/api/knowledge", headers=csrf, json={"title": "Untrusted note", "body": text}
    )
    assert response.status_code in (200, 201)
    assert response.headers["content-type"].startswith("application/json")
    documents = client.get("/api/knowledge").json()["documents"]
    assert any(doc["title"] == "Untrusted note" for doc in documents)


def test_print_report_escapes_untrusted_player_name(client, csrf, seed):
    seed.player(11, '<script>alert("xss")</script>')
    seed.session(101, expected_players=1)
    seed.stats(101, 11)
    response = client.get("/api/reports/101/print")
    assert response.status_code == 200
    assert '<script>alert("xss")</script>' not in response.text
    assert "&lt;script&gt;" in response.text


def test_report_api_contains_all_records(client, practice):
    response = client.get(f"/api/reports/{practice}")
    assert response.status_code == 200
    assert len(response.json()["players"]) == 2


def test_review_route_rejects_arbitrary_classification(client, csrf, practice):
    response = client.post(
        f"/api/sessions/{practice}/review",
        headers=csrf,
        json={"classification": "verified_healthy", "reason": "x"},
    )
    assert response.status_code in (400, 422)


def test_api_missing_worker_credentials_rejects_sync(client, csrf):
    assert client.post("/api/sync", headers=csrf, json={}).status_code == 409


def test_api_queues_sync_using_worker_configuration_flag(client, csrf, db):
    with db.database() as connection:
        connection.execute(
            "INSERT INTO meta(key,value) VALUES('credentials_configured','true')"
        )
    response = client.post(
        "/api/sync", headers=csrf, json={"start": "2026-10-01", "end": "2026-10-06"}
    )
    assert response.status_code in (200, 202)
    assert db.list_jobs()["jobs"][0]["kind"] == "sync"
