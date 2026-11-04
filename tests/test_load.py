from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError

from db.models import ArticleTicker, Sentiment
from pipeline.extract import PriceBar
from pipeline.load import LoadStats, load, load_tickers
from pipeline.transform import SENTIMENT_MODEL, CleanArticle
from tests.conftest import count_rows

PUBLISHED = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def article(url: str, symbols: set[str], score: float = 0.5) -> CleanArticle:
    return CleanArticle(
        url=url,
        title=f"Headline {url}",
        description=None,
        source="Reuters",
        published_at=PUBLISHED,
        symbols=frozenset(symbols),
        sentiment_score=score,
        sentiment_label="positive",
    )


def bar(symbol: str, day: date, close: float = 100.0) -> PriceBar:
    return PriceBar(symbol, day, 99.0, 101.0, 98.0, close, 1_000_000)


ARTICLES = [article("https://a", {"NVDA"}), article("https://b", {"AAPL", "MSFT"})]
PRICES = [bar("NVDA", date(2026, 10, 30)), bar("NVDA", date(2026, 10, 31))]


def test_seeded_tickers_are_available(db: Engine) -> None:
    tickers = load_tickers(db)
    assert len(tickers) == 20
    assert tickers["NVDA"] == ["Nvidia", "NVDA"]


def test_load_inserts_all_tables(db: Engine) -> None:
    stats = load(db, ARTICLES, PRICES)

    assert stats == LoadStats(articles=2, article_tickers=3, sentiment=2, daily_prices=2)
    rows = count_rows(db)
    assert (rows["articles"], rows["article_tickers"], rows["sentiment"]) == (2, 3, 2)
    assert rows["daily_prices"] == 2


def test_rerun_does_not_duplicate_articles_or_prices(db: Engine) -> None:
    load(db, ARTICLES, PRICES)
    before = count_rows(db)

    stats = load(db, ARTICLES, PRICES)

    assert stats == LoadStats()
    assert count_rows(db) == before


def test_existing_rows_are_never_overwritten(db: Engine) -> None:
    load(db, ARTICLES, PRICES)

    load(db, [replace(ARTICLES[0], sentiment_score=-0.9)], [bar("NVDA", date(2026, 10, 30), 1.0)])

    with db.connect() as conn:
        assert conn.execute(select(Sentiment.score).limit(1)).scalar_one() == 0.5


def test_sentiment_written_once_per_article_per_model(db: Engine) -> None:
    load(db, ARTICLES, [])
    load(db, ARTICLES, [])

    with db.connect() as conn:
        rows = conn.execute(select(Sentiment.article_id, Sentiment.model)).all()
    assert len(rows) == 2
    assert {model for _, model in rows} == {SENTIMENT_MODEL}


def test_new_ticker_link_is_added_to_existing_article(db: Engine) -> None:
    load(db, [article("https://a", {"NVDA"})], [])

    stats = load(db, [article("https://a", {"NVDA", "AMD"})], [])

    assert (stats.articles, stats.article_tickers) == (0, 1)
    with db.connect() as conn:
        symbols = conn.execute(select(ArticleTicker.symbol)).scalars().all()
    assert sorted(symbols) == ["AMD", "NVDA"]


def test_empty_load_is_a_no_op(db: Engine) -> None:
    assert load(db, [], []) == LoadStats()


def test_run_is_one_transaction(db: Engine) -> None:
    unknown_symbol = bar("ZZZZ", date(2026, 10, 30))  # violates the tickers foreign key

    with pytest.raises(IntegrityError):
        load(db, ARTICLES, [unknown_symbol])

    rows = count_rows(db)
    assert rows["articles"] == rows["sentiment"] == rows["daily_prices"] == 0
