"""Extract news per ticker from NewsAPI and daily prices from yfinance."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

NEWS_API_URL = "https://newsapi.org/v2/everything"
NEWS_API_PAGE_SIZE = 100  # free tier returns at most 100 results per query
NEWS_API_MAX_DAYS = 29  # free tier only serves the last month; stay clear of the edge
MARKET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class NewsRequest:
    """One ticker, one New York calendar day. Small windows keep each day's articles
    from being crowded out by busier days in the same request."""

    symbol: str
    day: date

    @property
    def start(self) -> datetime:
        return datetime.combine(self.day, time.min, tzinfo=MARKET_TZ).astimezone(UTC)

    @property
    def end(self) -> datetime:
        """Start of the next day (exclusive), in UTC so 23- and 25-hour DST days are exact."""
        next_day = self.day + timedelta(days=1)
        return datetime.combine(next_day, time.min, tzinfo=MARKET_TZ).astimezone(UTC)

    def __str__(self) -> str:
        return f"{self.symbol} {self.day}"


@dataclass(frozen=True)
class FetchedWindow:
    """A request that NewsAPI answered, with enough detail to tell a full day from a sample."""

    request: NewsRequest
    articles_returned: int
    total_results: int

    @property
    def capped(self) -> bool:
        return self.articles_returned >= NEWS_API_PAGE_SIZE or (
            self.total_results > self.articles_returned
        )


@dataclass
class NewsResult:
    articles: list[dict[str, Any]] = field(default_factory=list)
    requests_made: int = 0
    fetched: list[FetchedWindow] = field(default_factory=list)
    failed: list[NewsRequest] = field(default_factory=list)
    skipped: list[NewsRequest] = field(default_factory=list)


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


def build_query(aliases: list[str]) -> str:
    return " OR ".join(f'"{alias}"' for alias in aliases)


def fetch_news(
    plan: list[NewsRequest],
    aliases_by_symbol: dict[str, list[str]],
    api_key: str,
    budget: int,
) -> NewsResult:
    """Run the planned requests, stopping at the budget or on a rate-limit response."""
    result = NewsResult()
    for index, req in enumerate(plan):
        if result.requests_made >= budget:
            result.skipped = plan[index:]
            break
        result.requests_made += 1
        try:
            response = requests.get(
                NEWS_API_URL,
                params={
                    "q": build_query(aliases_by_symbol[req.symbol]),
                    "searchIn": "title,description",
                    "from": _isoformat(req.start),
                    # `to` is inclusive; stop one second before the next day starts.
                    "to": _isoformat(req.end - timedelta(seconds=1)),
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": NEWS_API_PAGE_SIZE,
                },
                # Header rather than query param so the key never appears in logged URLs.
                headers={"X-Api-Key": api_key},
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("NewsAPI request failed for %s: %s", req, exc)
            result.failed.append(req)
            continue

        if response.status_code == 429:
            logger.warning("NewsAPI rate limit reached at %s", req)
            result.skipped = plan[index:]
            break

        body = _json_or_empty(response)
        if response.status_code != 200 or body.get("status") != "ok":
            logger.warning(
                "NewsAPI error for %s: HTTP %s %s %s",
                req,
                response.status_code,
                body.get("code"),
                body.get("message"),
            )
            result.failed.append(req)
            continue

        articles = body.get("articles") or []
        result.articles.extend(articles)
        total = body.get("totalResults")
        result.fetched.append(
            FetchedWindow(req, len(articles), total if isinstance(total, int) else len(articles))
        )

    if result.skipped:
        logger.warning(
            "Skipped %d NewsAPI requests (budget %d): %s",
            len(result.skipped),
            budget,
            ", ".join(str(r) for r in result.skipped),
        )
    logger.info(
        "NewsAPI: %d requests, %d articles, %d failed, %d skipped",
        result.requests_made,
        len(result.articles),
        len(result.failed),
        len(result.skipped),
    )
    return result


def fetch_prices(symbols: list[str], start: date, end: date) -> list[PriceBar]:
    """Fetch completed daily bars in [start, end).

    Today's bar is excluded because it may still change while the market is open,
    and the load step never overwrites existing rows.
    """
    end = min(end, datetime.now(MARKET_TZ).date())
    bars: list[PriceBar] = []
    for symbol in symbols:
        try:
            frame = yf.Ticker(symbol).history(
                start=start, end=end, interval="1d", auto_adjust=False
            )
        except Exception as exc:  # yfinance raises a wide range of error types
            logger.warning("Price fetch failed for %s: %s", symbol, exc)
            continue
        if frame is None or frame.empty:
            logger.warning("No price data for %s between %s and %s", symbol, start, end)
            continue
        bars.extend(_frame_to_bars(symbol, frame))
    logger.info("yfinance: %d bars for %d symbols", len(bars), len(symbols))
    return bars


def _frame_to_bars(symbol: str, frame: pd.DataFrame) -> list[PriceBar]:
    columns = ["Open", "High", "Low", "Close", "Volume"]
    frame = frame.dropna(subset=columns)
    return [
        PriceBar(
            symbol=symbol,
            date=timestamp.date(),
            open=float(row.Open),
            high=float(row.High),
            low=float(row.Low),
            close=float(row.Close),
            volume=int(row.Volume),
        )
        for timestamp, row in zip(frame.index, frame[columns].itertuples(), strict=True)
    ]


def _isoformat(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _json_or_empty(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
