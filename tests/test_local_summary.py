from local_app import chat


def test_broad_summary_has_multiple_complementary_metrics(practice):
    result = chat.answer("Summarize this session.", practice)
    columns = {c["key"] for c in result["table"]["columns"]}
    assert {"minutes", "distance_m", "mechanical_load", "load_per_minute", "speed_max"} <= columns
    rows = {r["player_id"]: r for r in result["table"]["rows"]}
    assert rows[11]["minutes"] == 60
    assert rows[11]["mechanical_load"] == 1200
    assert rows[11]["load_per_minute"] == 20
    assert rows[22]["load_per_minute"] == 10
    assert rows[11]["speed_max"] is None
    assert result["query"]["metric"] is None
    assert result["sources"][0]["id"] == practice


def test_explicit_metric_summary_remains_the_requested_metric(practice):
    result = chat.answer("Summarize distance in this session", practice)
    assert result["query"]["metric"] == "distance_m"
    assert {r["player_id"]: r["value"] for r in result["table"]["rows"]} == {11: 3000, 22: 1800}
