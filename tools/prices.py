"""Price tools: price_history, compare_tickers."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from tools.common import (
    SENTIMENT_MODEL,
    article_source,
    calendar_window,
    engine,
    news_window_sql,
    pct_change,
    price_date_range,
    resolve_ticker,
)
from tools.news import TICKER_FIELD
from tools.registry import Tool, ToolError, ToolResult

MAX_TRADING_DAYS = 250


class PriceHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = TICKER_FIELD
    days: int = Field(
        default=10,
        ge=1,
        le=MAX_TRADING_DAYS,
        description="Number of TRADING days (market sessions, not calendar days) ending at "
        "the latest completed session. Default 10.",
    )


def price_history(params: PriceHistoryInput) -> ToolResult:
    with engine().connect() as conn:
        symbol = resolve_ticker(conn, params.ticker)
        first, latest = price_date_range(conn)
        # One extra bar so the first day's change and the total change start from a close.
        rows = conn.execute(
            text(
                "SELECT date, open, high, low, close, volume FROM daily_prices "
                "WHERE symbol = :symbol ORDER BY date DESC LIMIT :n"
            ),
            {"symbol": symbol, "n": params.days + 1},
        ).all()[::-1]

    if not rows:
        raise ToolError(f"No price data for {symbol}. Prices cover {first} to {latest}.")
    notes = []
    if len(rows) > params.days:
        previous_close: float | None = rows[0].close
        bars = rows[1:]
        base, basis = rows[0].close, f"close on {rows[0].date}"
    else:
        previous_close = None
        bars = rows
        base, basis = rows[0].open, f"open on {rows[0].date}"
        notes.append(f"Only {len(bars)} trading days of {symbol} prices are available.")

    daily = []
    for bar in bars:
        change = None if previous_close is None else pct_change(previous_close, bar.close)
        daily.append(
            {
                "date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "change_pct": change,
            }
        )
        previous_close = bar.close
    ranked = sorted(
        (d for d in daily if d["change_pct"] is not None), key=lambda d: d["change_pct"]
    )
    return ToolResult(
        data={
            "ticker": symbol,
            "trading_days": len(daily),
            "start_date": daily[0]["date"],
            "end_date": daily[-1]["date"],
            "total_change_pct": pct_change(base, daily[-1]["close"]),
            "change_measured_from": basis,
            "period_high": max(d["high"] for d in daily),
            "period_low": min(d["low"] for d in daily),
            "best_day": _day(ranked[-1]) if ranked else None,
            "worst_day": _day(ranked[0]) if ranked else None,
            "notes": notes,
            "daily": daily,
        },
        as_of=latest,
    )


class CompareTickersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tickers: list[str] = Field(
        min_length=2,
        max_length=5,
        description="2 to 5 ticker symbols or company names, e.g. ['NVDA', 'AMD'].",
    )
    days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="CALENDAR days ending at the latest price date, used for both the price "
        "change and the news statistics. Default 7.",
    )


def compare_tickers(params: CompareTickersInput) -> ToolResult:
    with engine().connect() as conn:
        symbols = [resolve_ticker(conn, t) for t in params.tickers]
        if len(set(symbols)) != len(symbols):
            raise ToolError(f"Tickers must be different; they resolved to {', '.join(symbols)}.")
        _, latest = price_date_range(conn)
        start, end = calendar_window(latest, params.days)
        rows, sources = [], []
        for symbol in symbols:
            row, notable = _compare_one(conn, symbol, start, end)
            rows.append(row)
            sources.extend(notable)

    if all(r["price_change_pct"] is None and r["articles"] == 0 for r in rows):
        raise ToolError(f"No prices or news for {', '.join(symbols)} between {start} and {end}.")
    return ToolResult(
        data={
            "start_date": start,
            "end_date": end,
            "window": f"{params.days} calendar days ending at the latest price date",
            "tickers": rows,
            "best_price_change": _leader(rows, "price_change_pct"),
            "most_positive_sentiment": _leader(rows, "avg_sentiment"),
            "most_articles": _leader(rows, "articles"),
        },
        as_of=latest,
        sources=sources,
    )


def _compare_one(
    conn: Connection, symbol: str, start: Any, end: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    window = {"symbol": symbol, "start": start, "end": end, "model": SENTIMENT_MODEL}
    prices = conn.execute(
        text(
            """
            SELECT
              (SELECT close FROM daily_prices WHERE symbol = :symbol AND date < :start
               ORDER BY date DESC LIMIT 1) AS base_close,
              (SELECT open FROM daily_prices WHERE symbol = :symbol AND date >= :start
               ORDER BY date LIMIT 1) AS first_open,
              (SELECT close FROM daily_prices WHERE symbol = :symbol AND date <= :end
               ORDER BY date DESC LIMIT 1) AS last_close,
              (SELECT count(*) FROM daily_prices
               WHERE symbol = :symbol AND date BETWEEN :start AND :end) AS trading_days
            """
        ),
        window,
    ).one()
    news_filter = f"""
        FROM articles a
        JOIN article_tickers t ON t.article_id = a.id AND t.symbol = :symbol
        JOIN sentiment s ON s.article_id = a.id AND s.model = :model
        WHERE {news_window_sql()}
    """
    news = conn.execute(
        text(f"SELECT count(*) AS n, avg(s.score) AS avg_score {news_filter}"), window
    ).one()
    notable = conn.execute(
        text(
            f"""
            (SELECT a.id, a.title, a.source, a.published_at, a.url, s.score {news_filter}
             ORDER BY s.score DESC, a.id LIMIT 1)
            UNION
            (SELECT a.id, a.title, a.source, a.published_at, a.url, s.score {news_filter}
             ORDER BY s.score ASC, a.id LIMIT 1)
            """
        ),
        window,
    ).all()

    base = prices.base_close if prices.base_close is not None else prices.first_open
    change = None
    if base is not None and prices.last_close is not None and prices.trading_days:
        change = pct_change(base, prices.last_close)
    row = {
        "ticker": symbol,
        "price_change_pct": change,
        "trading_days": prices.trading_days,
        "articles": news.n,
        "avg_sentiment": news.avg_score,
    }
    sources = [{**article_source(r), "ticker": symbol, "sentiment": r.score} for r in notable]
    return row, sources


def _leader(rows: list[dict[str, Any]], key: str) -> str | None:
    candidates = [r for r in rows if r[key] is not None]
    return max(candidates, key=lambda r: r[key])["ticker"] if candidates else None


def _day(entry: dict[str, Any]) -> dict[str, Any]:
    return {"date": entry["date"], "change_pct": entry["change_pct"], "close": entry["close"]}


PRICE_HISTORY = Tool(
    name="price_history",
    description=(
        "Daily open/high/low/close/volume for one ticker over the last `days` TRADING days "
        "(market sessions, not calendar days), ending at the latest completed session in the "
        "database (today's unfinished session is never included). Returns each day's % change "
        "from the previous close, the total % change over the period, the period high/low, "
        "and the best and worst day. Use it to see how and when a stock moved."
    ),
    input_model=PriceHistoryInput,
    fn=price_history,
)

COMPARE_TICKERS = Tool(
    name="compare_tickers",
    description=(
        "Compare 2-5 tickers side by side over the same window: % price change, average news "
        "sentiment and number of articles. The window is `days` CALENDAR days ending at the "
        "latest price date, applied to both prices and news so they line up; news published "
        "after the last completed session is not counted. Price change runs from the close "
        "before the window to the last close in it. `sources` holds each ticker's most "
        "positive and most negative article in the window."
    ),
    input_model=CompareTickersInput,
    fn=compare_tickers,
)
