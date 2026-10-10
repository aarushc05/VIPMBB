"""Printable reports preserve numerical trends, source links and safe HTML."""

from html.parser import HTMLParser

import pytest


class PrintMarkup(HTMLParser):
    """Inspect actual HTML structure without adding a browser/parser dependency."""

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.tables, self.links, self.svgs, self.tags = [], [], [], []
        self.table = self.section = self.row = self.cell = self.link = None
        self.invalid_table_rows = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "table":
            self.table = {"thead": [], "tbody": []}
            self.tables.append(self.table)
        elif tag in {"thead", "tbody"}:
            self.section = tag
        elif tag == "tr":
            self.row = []
            if self.table is None or self.section is None:
                self.invalid_table_rows.append(tag)
            else:
                self.table[self.section].append(self.row)
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "a":
            self.link = {"href": attrs.get("href"), "text": []}
            self.links.append(self.link)
        elif tag == "svg":
            self.svgs.append(attrs)

    def handle_data(self, value):
        if self.cell is not None:
            self.cell.append(value)
        if self.link is not None:
            self.link["text"].append(value)

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            if self.row is not None and self.cell is not None:
                self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr":
            self.row = None
        elif tag in {"thead", "tbody"}:
            self.section = None
        elif tag == "table":
            self.table = None
        elif tag == "a":
            self.link = None

    def trend_table(self):
        return next(
            table
            for table in self.tables
            if any("Prior average AU/min" in row for row in table["thead"])
        )


@pytest.fixture
def printable_practice(seed):
    seed.player(11, "Synthetic Trend Player")
    for sid, day, minutes, load in [
        (1, "2026-10-01", 30, 300),
        (2, "2026-10-02", 60, 1200),
        (3, "2026-10-03", 60, 1800),
        (4, "2026-10-06", 60, 2400),
    ]:
        seed.session(sid, day, reviewed=False, expected_players=1)
        seed.stats(sid, 11, minutes=minutes, mechanical_load=load)
    return 4


def fetch_print(client, session_id):
    response = client.get(f"/api/reports/{session_id}/print")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return response.text, PrintMarkup(response.text)


def test_printed_trend_has_weighted_values_chronological_dates_and_source_links(
    client, printable_practice
):
    text, markup = fetch_print(client, printable_practice)
    trend = markup.trend_table()
    assert trend["thead"] == [
        [
            "Player",
            "Recorded practice trend",
            "Current AU/min",
            "Prior average AU/min",
            "Change",
            "Prior practices",
            "Prior date: AU/min",
        ]
    ]
    row = trend["tbody"][0]
    assert row[0] == "Synthetic Trend Player"
    assert row[2:6] == ["40.00", "22.00", "+81.8%", "3"]
    assert "2026-10-01: 10.00" in row[6]
    assert "2026-10-02: 20.00" in row[6]
    assert "2026-10-03: 30.00" in row[6]
    assert (
        row[6].index("2026-10-01")
        < row[6].index("2026-10-02")
        < row[6].index("2026-10-03")
    )
    prior_links = [
        link
        for link in markup.links
        if link["href"] in {"/reports/1", "/reports/2", "/reports/3"}
    ]
    assert [(link["href"], "".join(link["text"])) for link in prior_links] == [
        ("/reports/1", "2026-10-01"),
        ("/reports/2", "2026-10-02"),
        ("/reports/3", "2026-10-03"),
    ]
    assert len(markup.svgs) == 1
    assert markup.svgs[0]["role"] == "img"
    assert (
        markup.svgs[0]["aria-label"]
        == "2026-10-01: 10.00; 2026-10-02: 20.00; 2026-10-03: 30.00; 2026-10-06: 40.00"
    )
    assert "manual review is not required" in text.lower()
    assert "reviewed practices" not in text.lower()
    assert "reviewed baseline" not in text.lower()


def test_print_tables_have_semantic_headers_and_bodies(client, printable_practice):
    _, markup = fetch_print(client, printable_practice)
    assert len(markup.tables) == 2
    assert markup.invalid_table_rows == []
    for table in markup.tables:
        assert len(table["thead"]) == 1
        assert table["tbody"]
        assert all(len(row) == len(table["thead"][0]) for row in table["tbody"])


def test_individual_legacy_exclusion_keeps_actual_current_rate_in_trend_table(
    client, seed, printable_practice
):
    seed.stats(4, 11, minutes=60, mechanical_load=2400, legacy=True)
    _, markup = fetch_print(client, printable_practice)
    row = markup.trend_table()["tbody"][0]
    assert "Legacy measurement" in row[0]
    assert row[2] == "40.00"
    assert row[3:6] == ["—", "—", "0"]
    assert markup.svgs == []


@pytest.mark.parametrize("exclusion", ["invalid_bounds", "schedule_conflict"])
def test_whole_session_exclusion_has_one_explanation_and_only_raw_player_table(
    client, db, printable_practice, exclusion
):
    with db.database() as connection:
        if exclusion == "invalid_bounds":
            connection.execute(
                "UPDATE sessions SET end_utc='2026-10-07T01:00:00Z' WHERE id=4"
            )
        else:
            identity = db.digest(
                {
                    "base_url": "https://georgia-tech-mccamish.access.kinexon.com",
                    "team_id": 3,
                }
            )
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('source_identity',%s)", (identity,)
            )
            connection.execute("""UPDATE sessions SET local_date='2026-03-04',
                start_utc='2026-03-04T22:28:00Z',end_utc='2026-03-05T02:10:00Z' WHERE id=4""")
    report = db.get_report(printable_practice)
    assert report["session"]["classification"] == "practice"
    assert report["session"]["comparison_exclusion"] == exclusion
    text, markup = fetch_print(client, printable_practice)
    explanation = "Practice trends are unavailable for this recording; see the coverage and interpretation notes above."
    assert text.count(explanation) == 1
    assert "Recent practice trends" not in text
    assert "Prior average AU/min" not in text
    assert len(markup.tables) == 1
    assert "40.00" in markup.tables[0]["tbody"][0]
    assert markup.svgs == []
    assert markup.invalid_table_rows == []


@pytest.mark.parametrize("classification", ["game", "unknown"])
def test_nonpractice_print_has_raw_metrics_but_no_practice_trend_table(
    client, db, printable_practice, classification
):
    db.review_session(
        printable_practice, classification, "Synthetic staff classification"
    )
    text, markup = fetch_print(client, printable_practice)
    assert "Recent practice trends" not in text
    assert "Practice trends are not calculated" in text
    assert len(markup.tables) == 1
    assert "40.00" in markup.tables[0]["tbody"][0]
    assert markup.svgs == []


def test_print_escapes_names_titles_and_review_content_inside_trends(
    client, db, seed, printable_practice
):
    player_name = '<script>alert("synthetic-player")</script>'
    title = '<img src=x onerror="synthetic-title">'
    review = '<svg onload="synthetic-review">'
    seed.player(11, player_name)
    with db.database() as connection:
        connection.execute("UPDATE sessions SET title=%s WHERE id=4", (title,))
    db.review_session(4, "practice", review)
    text, markup = fetch_print(client, printable_practice)
    assert player_name not in text and title not in text and review not in text
    assert "&lt;script&gt;" in text and "&lt;img" in text and "&lt;svg" in text
    assert not any(tag in {"script", "img"} for tag, _ in markup.tags)
    assert not any(
        name.lower().startswith("on") for _, attrs in markup.tags for name in attrs
    )
    assert markup.trend_table()["tbody"][0][0] == player_name


def test_print_zero_baseline_shows_zero_average_without_percentage(
    client, seed, printable_practice
):
    for sid in (1, 2, 3):
        seed.stats(sid, 11, minutes=60, mechanical_load=0)
    _, markup = fetch_print(client, printable_practice)
    row = markup.trend_table()["tbody"][0]
    assert "Prior average is zero" in row[0]
    assert row[2:6] == ["40.00", "0.00", "—", "3"]


def test_print_insufficient_history_exposes_observations_not_fabricated_percentage(
    client, db, printable_practice
):
    with db.database() as connection:
        connection.execute("DELETE FROM stats WHERE session_id=3")
    _, markup = fetch_print(client, printable_practice)
    row = markup.trend_table()["tbody"][0]
    assert "2 of 3 practices available" in row[0]
    assert row[2:6] == ["40.00", "—", "—", "2"]
    assert "2026-10-01" in row[6] and "2026-10-02" in row[6]
    assert "2026-10-03" not in row[6]
