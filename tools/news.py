"""News tools: list_tickers, search_news, sentiment_trend."""

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from tools.common import (
    NEWS_DATE,
    SAMPLE_CAVEAT,
    SENTIMENT_MODEL,
    all_tickers,
    article_source,
    calendar_window,
    coverage_warnings,
    engine,
    news_coverage,
    news_date_range,
    news_window_sql,
    price_date_range,
    resolve_ticker,
)
from tools.registry import Tool, ToolError, ToolResult

MAX_ARTICLES = 25
DESCRIPTION_CHARS = 280
TREND_THRESHOLD = 0.05  # change in average VADER score that counts as a real shift

TICKER_FIELD = Field(
    min_length=1,
    max_length=50,
    description="Ticker symbol or company name, case-insensitive, e.g. 'NVDA' or 'Nvidia'.",
)


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def list_tickers(_: NoInput) -> ToolResult:
    with engine().connect() as conn:
        news_first, news_last = news_date_range(conn)
        price_first, price_last = price_date_range(conn)
        counts = dict(
            conn.execute(text("SELECT symbol, count(*) FROM article_tickers GROUP BY symbol")).all()
        )
        tickers = [
            {
                "symbol": t.symbol,
                "company_name": t.company_name,
                "aliases": t.aliases,
                "articles": counts.get(t.symbol, 0),
            }
            for t in all_tickers(conn)
        ]
    return ToolResult(
        data={
            "tickers": tickers,
            "news_dates": {"first": news_first, "last": news_last},
            "price_dates": {"first": price_first, "last": price_last},
        },
        as_of=max(news_last, price_last),
    )


class SearchNewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = TICKER_FIELD
    days: int | None = Field(
        default=None,
        ge=1,
        le=90,
        description="Calendar days ending at end_date (or the latest news date). Default 7. "
        "Do not combine with start_date.",
    )
    start_date: date | None = Field(
        default=None, description="First day to include, YYYY-MM-DD (US Eastern date)."
    )
    end_date: date | None = Field(
        default=None,
        description="Last day to include, YYYY-MM-DD. Defaults to the latest news date.",
    )
    keyword: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Only articles whose title or description contains this text "
        "(case-insensitive), e.g. 'earnings' or 'export'.",
    )
    limit: int = Field(default=10, ge=1, le=MAX_ARTICLES, description="Max articles to return.")

    @model_validator(mode="after")
    def days_or_start(self) -> "SearchNewsInput":
        if self.days is not None and self.start_date is not None:
            raise ValueError("give either days or start_date, not both")
        return self


def search_news(params: SearchNewsInput) -> ToolResult:
    with engine().connect() as conn:
        symbol = resolve_ticker(conn, params.ticker)
        first, latest = news_date_range(conn)
        end = params.end_date or latest
        if params.start_date:
            start = params.start_date
        else:
            start, end = calendar_window(end, params.days or 7)
        if start > end:
            raise ToolError(f"start_date {start} is after end_date {end}.")

        keyword_sql = ""
        args: dict[str, Any] = {"symbol": symbol, "model": SENTIMENT_MODEL}
        args |= {"start": start, "end": end, "limit": params.limit}
        if params.keyword:
            keyword_sql = "AND (a.title ILIKE :kw OR a.description ILIKE :kw)"
            args["kw"] = f"%{_escape_like(params.keyword)}%"
        rows = conn.execute(
            text(
                f"""
                SELECT a.id, a.title, a.description, a.source, a.url, a.published_at,
                       s.score, s.label, count(*) OVER () AS total,
                       ARRAY(SELECT x.symbol FROM article_tickers x
                             WHERE x.article_id = a.id ORDER BY x.symbol) AS tickers
                FROM articles a
                JOIN article_tickers t ON t.article_id = a.id AND t.symbol = :symbol
                LEFT JOIN sentiment s ON s.article_id = a.id AND s.model = :model
                WHERE {news_window_sql()} {keyword_sql}
                ORDER BY a.published_at DESC, a.id DESC
                LIMIT :limit
                """
            ),
            args,
        ).all()

    if not rows:
        about = f" mentioning '{params.keyword}'" if params.keyword else ""
        raise ToolError(
            f"No {symbol} articles{about} between {start} and {end}. "
            f"News data covers {first} to {latest}; try a wider window"
            f"{' or no keyword' if params.keyword else ''}."
        )
    total = rows[0].total
    return ToolResult(
        data={
            "ticker": symbol,
            "start_date": start,
            "end_date": end,
            "total_matching": total,
            "returned": len(rows),
            "truncated": total > len(rows),
            "articles": [
                {
                    "id": r.id,
                    "published_at": r.published_at,
                    "source": r.source,
                    "title": r.title,
                    "description": _shorten(r.description),
                    "sentiment": None if r.score is None else {"score": r.score, "label": r.label},
                    "tickers": list(r.tickers),
                }
                for r in rows
            ],
        },
        as_of=latest,
        sources=[article_source(r) for r in rows],
    )


class SentimentTrendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = TICKER_FIELD
    days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Calendar days ending at the latest news date. Default 7.",
    )


def sentiment_trend(params: SentimentTrendInput) -> ToolResult:
    with engine().connect() as conn:
        symbol = resolve_ticker(conn, params.ticker)
        first, latest = news_date_range(conn)
        start, end = calendar_window(latest, params.days)
        window = {"symbol": symbol, "model": SENTIMENT_MODEL, "start": start, "end": end}
        base = f"""
            FROM articles a
            JOIN article_tickers t ON t.article_id = a.id AND t.symbol = :symbol
            JOIN sentiment s ON s.article_id = a.id AND s.model = :model
            WHERE {news_window_sql()}
        """
        daily = conn.execute(
            text(
                f"""
                SELECT {NEWS_DATE} AS day, avg(s.score) AS avg_score, count(*) AS articles,
                       count(*) FILTER (WHERE s.label = 'positive') AS positive,
                       count(*) FILTER (WHERE s.label = 'negative') AS negative
                {base} GROUP BY 1 ORDER BY 1
                """
            ),
            window,
        ).all()
        notable = conn.execute(
            text(
                f"""
                (SELECT a.id, a.title, a.source, a.published_at, a.url, s.score
                 {base} ORDER BY s.score ASC, a.id LIMIT 5)
                UNION
                (SELECT a.id, a.title, a.source, a.published_at, a.url, s.score
                 {base} ORDER BY s.score DESC, a.id LIMIT 5)
                ORDER BY published_at DESC
                """
            ),
            window,
        ).all()
        fetch_status, coverage = news_coverage(conn, symbol, start, end)

    if not daily:
        raise ToolError(
            f"No {symbol} articles between {start} and {end}. "
            f"News data covers {first} to {latest}; try more days."
        )
    by_day = {r.day: r for r in daily}
    days = [start + timedelta(days=i) for i in range(params.days)]
    series = [
        {
            "date": d,
            "avg_sentiment": by_day[d].avg_score if d in by_day else None,
            "articles": by_day[d].articles if d in by_day else 0,
            "positive": by_day[d].positive if d in by_day else 0,
            "negative": by_day[d].negative if d in by_day else 0,
            "fetch": fetch_status[d],
        }
        for d in days
    ]
    half = len(days) // 2
    first_half = _weighted_avg(series[:half])
    second_half = _weighted_avg(series[half:])
    change = None if first_half is None or second_half is None else second_half - first_half
    return ToolResult(
        data={
            "ticker": symbol,
            "start_date": start,
            "end_date": end,
            "model": SENTIMENT_MODEL,
            "articles": sum(r.articles for r in daily),
            "avg_sentiment": _weighted_avg(series),
            "first_half_avg": first_half,
            "second_half_avg": second_half,
            "change": change,
            "direction": _direction(change),
            "coverage": coverage,
            "warnings": coverage_warnings(symbol, coverage),
            "daily": series,
        },
        as_of=latest,
        sources=[{**article_source(r), "sentiment": r.score} for r in notable],
    )


def _weighted_avg(series: list[dict[str, Any]]) -> float | None:
    total = sum(d["articles"] for d in series)
    if not total:
        return None
    return sum(d["avg_sentiment"] * d["articles"] for d in series if d["articles"]) / total


def _direction(change: float | None) -> str:
    if change is None:
        return "insufficient data"
    if change >= TREND_THRESHOLD:
        return "improving"
    if change <= -TREND_THRESHOLD:
        return "worsening"
    return "flat"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _shorten(value: str | None) -> str | None:
    if value is None or len(value) <= DESCRIPTION_CHARS:
        return value
    return value[: DESCRIPTION_CHARS - 1].rstrip() + "…"


LIST_TICKERS = Tool(
    name="list_tickers",
    description=(
        "List every tracked ticker with its company name, the aliases used to match news, "
        "and how many articles mention it, plus the date ranges covered by news and prices. "
        "Call this first if you are unsure which tickers exist or how fresh the data is. "
        "Takes no arguments."
    ),
    input_model=NoInput,
    fn=list_tickers,
)

SEARCH_NEWS = Tool(
    name="search_news",
    description=(
        "Find news articles that mention one ticker, newest first, with each article's "
        "VADER sentiment (-1 very negative to +1 very positive). Use it to explain why a "
        "stock moved or to quote specific stories. The window is either `days` calendar days "
        "ending at end_date (default: the latest news date in the database, not today) or "
        "start_date..end_date. Optional `keyword` filters title/description. Returns at most "
        f"{MAX_ARTICLES} articles and says how many matched in total; `sources` holds "
        "citable id, title, source, date and URL for each returned article."
    ),
    input_model=SearchNewsInput,
    fn=search_news,
)

SENTIMENT_TREND = Tool(
    name="sentiment_trend",
    description=(
        "Daily average news sentiment and article counts for one ticker over the last `days` "
        "calendar days, ending at the latest news date in the database (not today). Also "
        "compares the average of the first half of the period with the second half: "
        "`change` > 0 means sentiment is improving, and `direction` labels it "
        f"improving/worsening/flat (threshold {TREND_THRESHOLD}). Days without articles have "
        "avg_sentiment null. Each day's `fetch` is complete, capped (hit the 100-article "
        "limit), provisional (fetched before all articles were available) or not_fetched. "
        "`sources` lists the most positive and most negative articles in the period so you "
        "can cite what drove the trend. " + SAMPLE_CAVEAT
    ),
    input_model=SentimentTrendInput,
    fn=sentiment_trend,
)
