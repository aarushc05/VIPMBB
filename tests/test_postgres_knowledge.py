"""PostgreSQL FTS/vector retrieval with offline models and owner boundaries."""

import pytest


def test_generated_full_text_search_updates_with_note_body(db):
    from server.app import knowledge

    note = knowledge.add_note(
        "Synthetic note", "Transition spacing improved in the fictional drill."
    )
    assert [r["id"] for r in knowledge.retrieve("transition", kind="coach_note")] == [
        note["id"]
    ]
    with db.database() as connection:
        connection.execute(
            "UPDATE documents SET body='Synthetic rebounding observation.' WHERE id=%s",
            (note["id"],),
        )
    assert knowledge.retrieve("transition", kind="coach_note") == []
    assert [r["id"] for r in knowledge.retrieve("rebounding", kind="coach_note")] == [
        note["id"]
    ]


def test_retrieval_and_list_never_cross_note_owner_boundary(db):
    from server.app import knowledge

    first = knowledge.add_note(
        "Transition A", "Synthetic transition spacing.", owner_id="coach-a"
    )
    knowledge.add_note(
        "Transition B", "Synthetic transition spacing.", owner_id="coach-b"
    )
    knowledge.initialize()
    results = knowledge.retrieve("transition", kind="coach_note", owner_id="coach-a")
    assert [r["id"] for r in results] == [first["id"]]
    visible = knowledge.list_documents(owner_id="coach-a")["documents"]
    assert all(r["title"] != "Transition B" for r in visible)
    assert any(r["kind"] == "definition" for r in visible)


def test_pgvector_cosine_retrieval_and_dimension_filter(db, monkeypatch):
    from server.app import knowledge, models

    first = knowledge.add_note("Synthetic one", "First observation.")
    second = knowledge.add_note("Synthetic two", "Second observation.")
    monkeypatch.setattr(models, "embed", lambda texts: [[1.0, 0.0], [0.0, 1.0]])
    assert knowledge.index_documents()["indexed"] == 2
    monkeypatch.setenv("VIPMBB_DISABLE_MODEL", "0")
    monkeypatch.setattr(models, "embed", lambda texts: [[1.0, 0.0]])
    assert [r["id"] for r in knowledge.retrieve("unrelated", kind="coach_note")] == [
        first["id"]
    ]
    monkeypatch.setattr(models, "embed", lambda texts: [[1.0, 0.0, 0.0]])
    assert knowledge.retrieve("unrelated", kind="coach_note") == []
    # Dimension mismatch must still preserve complete lexical retrieval.
    assert [r["id"] for r in knowledge.retrieve("second", kind="coach_note")] == [
        second["id"]
    ]


@pytest.mark.parametrize(
    "vectors",
    [[[0.0, 0.0]], [[float("nan"), 1.0]], [[True, 1.0]], [[1.0, 0.0], [0.0, 1.0]]],
)
def test_invalid_embedding_batch_is_never_written(db, monkeypatch, vectors):
    from server.app import knowledge, models

    knowledge.add_note("Synthetic note", "Synthetic observation.")
    monkeypatch.setattr(models, "embed", lambda texts: vectors)
    with pytest.raises(ValueError):
        knowledge.index_documents()
    with db.database() as connection:
        row = connection.execute(
            "SELECT embedding,embedding_json FROM documents"
        ).fetchone()
    assert row["embedding"] is None and row["embedding_json"] is None


def test_model_failure_retains_lexical_retrieval(db, monkeypatch):
    import httpx
    from server.app import knowledge, models

    note = knowledge.add_note("Synthetic spacing", "Synthetic spacing observation.")
    monkeypatch.setattr(models, "embed", lambda texts: [[1.0, 0.0]])
    knowledge.index_documents()
    monkeypatch.setenv("VIPMBB_DISABLE_MODEL", "0")

    def offline(*args):
        raise httpx.ConnectError("Synthetic local model offline")

    monkeypatch.setattr(models, "embed", offline)
    assert [r["id"] for r in knowledge.retrieve("spacing")] == [note["id"]]


def test_fts_query_punctuation_cannot_change_sql(db):
    from server.app import knowledge

    knowledge.add_note("Synthetic note", "Synthetic observation.")
    assert knowledge.retrieve("'; DROP TABLE documents; --") == []
    assert len(knowledge.list_documents()["documents"]) == 1
