"""Idempotent loading into Postgres: every insert is ON CONFLICT DO NOTHING."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Engine, select
from sqlalchemy.dialects.postgresql import insert

from db.models import Article, ArticleTicker, Base, DailyPrice, Sentiment, Ticker
from pipeline.extract import PriceBar
from pipeline.transform import SENTIMENT_MODEL, CleanArticle

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000  # keeps each statement well under Postgres' bind-parameter limit


@dataclass
class LoadStats:
    articles: int = 0
    article_tickers: int = 0
    sentiment: int = 0
    daily_prices: int = 0


def load_tickers(engine: Engine) -> dict[str, list[str]]:
    with engine.connect() as conn:
        rows = conn.execute(select(Ticker.symbol, Ticker.aliases).order_by(Ticker.symbol))
        return {symbol: list(aliases) for symbol, aliases in rows}


def load(engine: Engine, articles: Sequence[CleanArticle], prices: Sequence[PriceBar]) -> LoadStats:
    """Write one run's data in a single transaction. Re-running never duplicates rows."""
    stats = LoadStats()
    with engine.begin() as conn:
        stats.articles = _insert_ignore(
            conn,
            Article,
            [
                {
                    "url": a.url,
                    "title": a.title,
                    "description": a.description,
                    "source": a.source,
                    "published_at": a.published_at,
                }
                for a in articles
            ],
        )

        # Look ids up by url so articles that already existed still get links and sentiment.
        ids = _article_ids(conn, [a.url for a in articles])
        stats.article_tickers = _insert_ignore(
            conn,
            ArticleTicker,
            [
                {"article_id": ids[a.url], "symbol": symbol}
                for a in articles
                for symbol in sorted(a.symbols)
            ],
        )
        stats.sentiment = _insert_ignore(
            conn,
            Sentiment,
            [
                {
                    "article_id": ids[a.url],
                    "model": SENTIMENT_MODEL,
                    "score": a.sentiment_score,
                    "label": a.sentiment_label,
                }
                for a in articles
            ],
        )
        stats.daily_prices = _insert_ignore(
            conn,
            DailyPrice,
            [
                {
                    "symbol": p.symbol,
                    "date": p.date,
                    "open": p.open,
                    "high": p.high,
                    "low": p.low,
                    "close": p.close,
                    "volume": p.volume,
                }
                for p in prices
            ],
        )
    logger.info("Load: inserted %s", stats)
    return stats


def _insert_ignore(conn: Connection, model: type[Base], rows: list[dict[str, Any]]) -> int:
    inserted = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start : start + CHUNK_SIZE]
        statement = insert(model).values(chunk).on_conflict_do_nothing()
        # Count RETURNING rows: rowcount is not reliable across drivers for this statement.
        result = conn.execute(statement.returning(*model.__table__.primary_key.columns))
        inserted += len(result.all())
    return inserted


def _article_ids(conn: Connection, urls: list[str]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for start in range(0, len(urls), CHUNK_SIZE):
        chunk = urls[start : start + CHUNK_SIZE]
        rows = conn.execute(select(Article.url, Article.id).where(Article.url.in_(chunk)))
        ids.update({url: article_id for url, article_id in rows})
    return ids
