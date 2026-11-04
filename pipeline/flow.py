"""Prefect flow: extract -> transform -> load, for daily runs and the 30-day backfill."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

from db.session import get_engine
from pipeline.config import Settings
from pipeline.extract import NewsResult, PriceBar, fetch_news, fetch_prices, plan_news_requests
from pipeline.load import LoadStats, load, load_tickers
from pipeline.transform import CleanArticle, transform_articles

Mode = Literal["daily", "backfill"]


@dataclass(frozen=True)
class RunWindow:
    news_days: int
    max_news_windows: int
    price_days: int


# Daily runs overlap the previous day so late-indexed articles and bars are not missed.
WINDOWS: dict[str, RunWindow] = {
    "daily": RunWindow(news_days=3, max_news_windows=1, price_days=7),
    "backfill": RunWindow(news_days=30, max_news_windows=4, price_days=30),
}


@task(name="Load tickers", cache_policy=NO_CACHE)
def load_tickers_task(database_url: str) -> dict[str, list[str]]:
    return load_tickers(get_engine(database_url))


@task(name="Extract news", cache_policy=NO_CACHE)
def extract_news_task(
    aliases_by_symbol: dict[str, list[str]], window: RunWindow, settings: Settings, now: datetime
) -> NewsResult:
    symbols = list(aliases_by_symbol)
    # Use as many windows per ticker as the request budget allows.
    windows = max(1, min(window.max_news_windows, settings.request_budget // len(symbols)))
    plan = plan_news_requests(symbols, end=now, days=window.news_days, windows=windows)
    return fetch_news(plan, aliases_by_symbol, settings.news_api_key, settings.request_budget)


@task(name="Extract prices", cache_policy=NO_CACHE)
def extract_prices_task(symbols: list[str], window: RunWindow, now: datetime) -> list[PriceBar]:
    end = now.date() + timedelta(days=1)  # yfinance's end date is exclusive
    return fetch_prices(symbols, start=now.date() - timedelta(days=window.price_days), end=end)


@task(name="Transform", cache_policy=NO_CACHE)
def transform_task(news: NewsResult, aliases_by_symbol: dict[str, list[str]]) -> list[CleanArticle]:
    return transform_articles(news.articles, aliases_by_symbol)


@task(name="Load", cache_policy=NO_CACHE)
def load_task(database_url: str, articles: list[CleanArticle], prices: list[PriceBar]) -> LoadStats:
    return load(get_engine(database_url), articles, prices)


@flow(name="financial-news-etl")
def etl_flow(mode: Mode = "daily") -> dict[str, Any]:
    logger = get_run_logger()
    settings = Settings.from_env()
    window = WINDOWS[mode]
    now = datetime.now(UTC)

    aliases_by_symbol = load_tickers_task(settings.database_url)
    if not aliases_by_symbol:
        raise RuntimeError("No tickers found. Run `alembic upgrade head` to seed them.")

    news = extract_news_task(aliases_by_symbol, window, settings, now)
    prices = extract_prices_task(list(aliases_by_symbol), window, now)
    articles = transform_task(news, aliases_by_symbol)
    stats = load_task(settings.database_url, articles, prices)

    summary = {
        "mode": mode,
        "news_requests": news.requests_made,
        "news_requests_failed": [str(r) for r in news.failed],
        "news_requests_skipped": [str(r) for r in news.skipped],
        "inserted": asdict(stats),
    }
    logger.info("Run summary: %s", summary)
    return summary
