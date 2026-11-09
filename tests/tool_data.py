"""Deterministic fixture data for tool tests.

Everything is dated June 2026, months before "today", so any tool that anchors its
windows on the clock instead of the data returns nothing and fails loudly.
"""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import Engine, text

AS_OF = date(2026, 6, 30)  # latest news date (US Eastern) and latest price date
NEWS_START = date(2026, 6, 1)
TRADING_DAYS = [d.date() for d in pd.bdate_range("2026-06-01", "2026-06-30")]
AAPL_BURST = 30  # more AAPL articles on one day than any tool returns


def _articles() -> list[dict]:
    rows = []
    for i in range(30):
        day = NEWS_START + timedelta(days=i)
        blackwell = day.day % 5 == 0
        rows.append(
            {
                "url": f"https://news.test/nvda/{day}",
                "title": f"Nvidia update {day}",
                "description": "Blackwell chip demand" if blackwell else "Market notes",
                "published_at": datetime.combine(day, datetime.min.time(), UTC).replace(hour=14),
                "symbols": ["NVDA"],
                # Negative first half of June, positive second half.
                "score": -0.4 if day.day <= 15 else 0.6,
            }
        )
    # 22:00 New York on June 30 is already July 1 in UTC; it must count as June 30.
    rows.append(
        {
            "url": "https://news.test/nvda/late",
            "title": "Nvidia late-night filing",
            "description": None,
            "published_at": datetime(2026, 7, 1, 2, 0, tzinfo=UTC),
            "symbols": ["NVDA"],
            "score": 0.0,
        }
    )
    rows.append(
        {
            "url": "https://news.test/shared",
            "title": "Nvidia and Apple lead tech",
            "description": "Both rallied",
            "published_at": datetime(2026, 6, 30, 15, 0, tzinfo=UTC),
            "symbols": ["NVDA", "AAPL"],
            "score": 0.8,
        }
    )
    for n in range(AAPL_BURST):
        rows.append(
            {
                "url": f"https://news.test/aapl/{n}",
                "title": f"Apple story {n}",
                "description": "iPhone 50% off_sale" if n == 0 else "Retail",
                "published_at": datetime(2026, 6, 29, 13, 0, n, tzinfo=UTC),
                "symbols": ["AAPL"],
                "score": 0.1,
            }
        )
    for i in range(5):  # AMD news stops early in the month
        rows.append(
            {
                "url": f"https://news.test/amd/{i}",
                "title": f"AMD note {i}",
                "description": None,
                "published_at": datetime(2026, 6, 1 + i, 15, 0, tzinfo=UTC),
                "symbols": ["AMD"],
                "score": 0.2,
            }
        )
    return rows


def _label(score: float) -> str:
    return "positive" if score >= 0.05 else "negative" if score <= -0.05 else "neutral"


def _fetch_log() -> list[dict]:
    """NVDA: complete except June 10 (never fetched), June 29 (capped) and June 30
    (provisional). AMD: every day complete, including its quiet days. AAPL: never fetched."""
    rows = []
    for i in range(30):
        day = NEWS_START + timedelta(days=i)
        if day.day != 10:
            capped, settled = day.day == 29, day.day != 30
            rows.append({"symbol": "NVDA", "day": day, "capped": capped, "settled": settled})
        rows.append({"symbol": "AMD", "day": day, "capped": False, "settled": True})
    return rows


def nvda_close(index: int) -> float:
    return 100.0 + index


def seed_tool_data(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE articles, article_tickers, sentiment, daily_prices, fetch_log "
                "RESTART IDENTITY"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fetch_log (symbol, day, fetched_at, articles_returned, "
                "total_results, capped, settled) "
                "VALUES (:symbol, :day, '2026-07-02', 1, 1, :capped, :settled)"
            ),
            _fetch_log(),
        )
        for row in _articles():
            article_id = conn.execute(
                text(
                    "INSERT INTO articles (url, title, description, source, published_at) "
                    "VALUES (:url, :title, :description, 'Test Wire', :published_at) RETURNING id"
                ),
                row,
            ).scalar_one()
            for symbol in row["symbols"]:
                conn.execute(
                    text("INSERT INTO article_tickers VALUES (:id, :symbol)"),
                    {"id": article_id, "symbol": symbol},
                )
            conn.execute(
                text("INSERT INTO sentiment VALUES (:id, 'vader', :score, :label)"),
                {"id": article_id, "score": row["score"], "label": _label(row["score"])},
            )
        prices = []
        for i, day in enumerate(TRADING_DAYS):
            close = nvda_close(i)
            prices.append(("NVDA", day, close - 0.5, close + 1, close - 1, close, 1000 + i))
            prices.append(("AAPL", day, 50.0, 51.0, 49.0, 50.0, 500))
            prices.append(("AMD", day, 20.0, 20.5, 19.5, 20.0 - i * 0.1, 800))
        conn.execute(
            text("INSERT INTO daily_prices VALUES (:s, :d, :o, :h, :l, :c, :v)"),
            [dict(zip("sdohlcv", p, strict=True)) for p in prices],
        )
