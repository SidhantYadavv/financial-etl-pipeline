"""Gap planning, the fetch log, the coverage report and re-matching stored articles."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, text

from pipeline.coverage import (
    CAPPED,
    COMPLETE,
    PROVISIONAL,
    coverage_report,
    fetchable_days,
    format_report,
    is_settled,
    plan_gap_fill,
    read_statuses,
    record_fetches,
)
from pipeline.extract import NEWS_API_MAX_DAYS, FetchedWindow, NewsRequest
from pipeline.load import load, load_title_aliases, rematch_articles
from pipeline.transform import CleanArticle
from tests.conftest import count_rows

# 06:00 New York on Monday 9 November 2026: the daily run's time.
NOW = datetime(2026, 11, 9, 11, tzinfo=UTC)
YESTERDAY = date(2026, 11, 8)


def test_fetchable_days_are_whole_past_days_newest_first() -> None:
    days = fetchable_days(NOW)

    assert days[0] == YESTERDAY  # today is still running
    assert days == sorted(days, reverse=True)
    assert days[-1] == date(2026, 10, 12)  # NOW - 29 days is mid-Oct 11, so the 12th
    assert len(days) == NEWS_API_MAX_DAYS - 1
    assert all(b - a == timedelta(days=1) for a, b in zip(days[1:], days, strict=False))


def test_a_day_settles_24_hours_after_it_ends() -> None:
    day_end = datetime(2026, 11, 9, 5, tzinfo=UTC)  # midnight New York after Nov 8
    assert not is_settled(YESTERDAY, day_end + timedelta(hours=23, minutes=59))
    assert is_settled(YESTERDAY, day_end + timedelta(hours=24))
    assert not is_settled(YESTERDAY, NOW)


def test_plan_fetches_missing_days_newest_first_then_provisional() -> None:
    days = fetchable_days(NOW)
    statuses = {
        ("NVDA", days[0]): PROVISIONAL,
        ("NVDA", days[1]): COMPLETE,
        ("AAPL", days[1]): CAPPED,
        ("AAPL", days[2]): PROVISIONAL,
    }

    plan = plan_gap_fill(["NVDA", "AAPL"], statuses, NOW)

    assert plan[:4] == [
        NewsRequest("AAPL", days[0]),
        NewsRequest("NVDA", days[2]),
        NewsRequest("NVDA", days[3]),
        NewsRequest("AAPL", days[3]),
    ]
    assert plan[-2:] == [NewsRequest("NVDA", days[0]), NewsRequest("AAPL", days[2])]
    assert len(plan) == 2 * len(days) - 2  # complete and capped days are never refetched
    assert len(set(plan)) == len(plan)


def test_nothing_to_plan_once_every_day_is_settled() -> None:
    days = fetchable_days(NOW)
    statuses = {(s, d): COMPLETE for s in ("NVDA", "AAPL") for d in days}
    assert plan_gap_fill(["NVDA", "AAPL"], statuses, NOW) == []


def _fetched(symbol: str, day: date, returned: int = 5, total: int = 5) -> FetchedWindow:
    return FetchedWindow(NewsRequest(symbol, day), returned, total)


def test_refetching_a_provisional_day_replaces_it(db: Engine) -> None:
    with db.begin() as conn:
        record_fetches(conn, [_fetched("NVDA", YESTERDAY, 40, 40)], NOW)
    with db.connect() as conn:
        assert read_statuses(conn) == {("NVDA", YESTERDAY): PROVISIONAL}

    with db.begin() as conn:
        record_fetches(conn, [_fetched("NVDA", YESTERDAY, 100, 180)], NOW + timedelta(days=1))
    with db.connect() as conn:
        assert read_statuses(conn) == {("NVDA", YESTERDAY): CAPPED}
        row = conn.execute(text("SELECT articles_returned, total_results FROM fetch_log")).one()
    assert tuple(row) == (100, 180)


def test_fetch_log_is_written_with_the_articles(db: Engine) -> None:
    article = CleanArticle(
        url="https://a",
        title="Nvidia rises",
        description=None,
        source="Reuters",
        published_at=datetime(2026, 11, 7, 15, tzinfo=UTC),
        symbols=frozenset({"NVDA"}),
        sentiment_score=0.5,
        sentiment_label="positive",
    )

    stats = load(db, [article], [], [_fetched("NVDA", date(2026, 11, 7))], NOW)

    assert (stats.articles, stats.fetch_log) == (1, 1)
    with db.connect() as conn:
        assert read_statuses(conn) == {("NVDA", date(2026, 11, 7)): COMPLETE}


def _seed_articles(engine: Engine, rows: list[tuple[str, str | None, str, str, list[str]]]) -> None:
    """(title, description, source, published_at, symbols), linked exactly as given."""
    with engine.begin() as conn:
        for i, (title, description, source, published_at, symbols) in enumerate(rows):
            article_id = conn.execute(
                text(
                    "INSERT INTO articles (url, title, description, source, published_at) "
                    "VALUES (:url, :title, :description, :source, :published_at) RETURNING id"
                ),
                {
                    "url": f"https://{source.lower()}/{i}",
                    "title": title,
                    "description": description,
                    "source": source,
                    "published_at": published_at,
                },
            ).scalar_one()
            for symbol in symbols:
                conn.execute(
                    text("INSERT INTO article_tickers VALUES (:id, :symbol)"),
                    {"id": article_id, "symbol": symbol},
                )


def test_coverage_report_counts_each_status(db: Engine) -> None:
    days = fetchable_days(NOW)
    with db.begin() as conn:
        record_fetches(conn, [_fetched("NVDA", d) for d in days[1:4]], NOW)  # complete
        record_fetches(conn, [_fetched("NVDA", days[4], 100, 300)], NOW)  # capped
        record_fetches(conn, [_fetched("NVDA", days[0])], NOW)  # provisional
        record_fetches(conn, [_fetched("AAPL", days[1], 0, 0)], NOW)  # complete, no news
    _seed_articles(
        db,
        [
            ("Nvidia one", None, "Reuters", "2026-11-07T15:00:00Z", ["NVDA"]),
            ("Nvidia two", None, "Reuters", "2026-11-07T16:00:00Z", ["NVDA"]),
            ("Nvidia three", None, "Reuters", "2026-11-06T15:00:00Z", ["NVDA"]),
            # 01:00 UTC on Nov 7 is still Nov 6 in New York: the same day as above.
            ("Nvidia late", None, "Reuters", "2026-11-07T01:00:00Z", ["NVDA"]),
            ("Nvidia old", None, "Reuters", "2026-09-01T15:00:00Z", ["NVDA"]),  # out of range
        ],
    )

    report_days, report = coverage_report(db, NOW)

    rows = {r.symbol: r for r in report}
    assert report_days == days
    assert len(report) == 20
    nvda = rows["NVDA"]
    assert (nvda.days, nvda.with_articles) == (len(days), 2)
    assert (nvda.complete, nvda.capped, nvda.provisional) == (3, 1, 1)
    assert nvda.not_fetched == len(days) - 5
    assert (rows["AAPL"].complete, rows["AAPL"].with_articles) == (1, 0)
    assert rows["TSLA"].not_fetched == len(days)

    text_report = format_report(report_days, report)
    assert f"News coverage {days[-1]} to {days[0]}" in text_report
    assert f"NVDA       {len(days)}          2         3       1            1" in text_report
    missing = 20 * len(days) - 6
    assert f"Requests still needed: {missing + 1} ({missing} missing, 1 provisional)" in (
        text_report
    )


SURFACE = (
    "Surface Laptop Ultra doubles Apple MacBook Pro's repairability score in iFixit teardown",
    "With 20 ARM processor cores, a fast Nvidia RTX graphics chip and a Mini-LED display, "
    "the Microsoft Surface Laptop Ultra is an exciting alternative to the Apple MacBook Pro.",
    "Notebookcheck.net",
    "2026-11-02T15:00:00Z",
    ["AAPL", "MSFT", "NVDA"],
)
SLICKDEALS = (
    "AMD Ryzen 5 9600X 6-Core, 12-Thread Unlocked Desktop Processor $178",
    "DealsPro1 via Amazon has AMD Ryzen 5 9600X for $178",
    "Slickdeals.net",
    "2026-11-02T15:00:00Z",
    ["AMD", "AMZN"],
)
GOOD = (
    "Nvidia beats estimates",
    "Data center revenue doubled; Microsoft and Meta keep buying.",
    "Reuters",
    "2026-11-02T15:00:00Z",
    ["NVDA", "MSFT"],
)


def test_rematch_removes_links_the_new_rules_reject(db: Engine) -> None:
    _seed_articles(db, [SURFACE, SLICKDEALS, GOOD])
    before = count_rows(db)

    stats = rematch_articles(db)

    assert (stats.articles, stats.links_before, stats.links_removed) == (3, 7, 5)
    assert stats.links_added == 0
    with db.connect() as conn:
        links = conn.execute(
            text(
                "SELECT a.source, array_agg(t.symbol ORDER BY t.symbol) FROM article_tickers t "
                "JOIN articles a ON a.id = t.article_id GROUP BY a.source ORDER BY a.source"
            )
        ).all()
    assert [tuple(r) for r in links] == [("Notebookcheck.net", ["AAPL"]), ("Reuters", ["NVDA"])]
    after = count_rows(db)
    assert after["articles"] == before["articles"]  # articles and sentiment are kept
    again = rematch_articles(db)
    assert (again.links_removed, again.links_added) == (0, 0)  # idempotent


DIMON = (
    "Jamie Dimon Ignores the Noise, While Hermès Seeks Suburban Wealth",
    "Criticism in the press doesn't change what's right, JPMorgan Chase CEO says.",
    "Biztoc.com",
    "2026-11-02T15:00:00Z",
    [],
)
MUSK = (
    "Elon Musk Pours $276 Million Into Political Donations For Republicans In Midterms",
    "The Tesla and SpaceX CEO is a huge donor in the midterm elections.",
    "Biztoc.com",
    "2026-11-02T15:00:00Z",
    [],
)


def test_rematch_adds_links_for_ceo_headlines(db: Engine) -> None:
    _seed_articles(db, [DIMON, MUSK])

    stats = rematch_articles(db)

    assert (stats.links_added, stats.links_removed) == (1, 0)
    with db.connect() as conn:
        links = conn.execute(
            text(
                "SELECT a.title, t.symbol FROM article_tickers t "
                "JOIN articles a ON a.id = t.article_id"
            )
        ).all()
    assert [(title[:11], symbol) for title, symbol in links] == [("Jamie Dimon", "JPM")]
    assert rematch_articles(db).links_added == 0


def test_seeded_title_aliases_skip_ambiguous_names(db: Engine) -> None:
    title_aliases = load_title_aliases(db)

    assert title_aliases["NVDA"] == ["Jensen Huang"]
    assert title_aliases["JPM"] == ["Jamie Dimon"]
    assert "John Ternus" in title_aliases["AAPL"]
    assert {"TSLA", "GS", "CVX"}.isdisjoint(title_aliases)  # Elon Musk etc. are ambiguous
    assert "Greg Peters" not in title_aliases["NFLX"]
    assert len(title_aliases) == 17
