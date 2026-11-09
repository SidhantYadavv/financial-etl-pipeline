"""Prefect flow: extract -> transform -> load, for daily runs and the 30-day backfill.

News is fetched the same way in both modes: one request per ticker and New York day,
spending the request budget on the newest unfetched days first (see pipeline.coverage).
The modes differ only in how many days of prices they fetch.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

from db.session import get_engine
from pipeline.config import Settings
from pipeline.coverage import is_settled, plan_gap_fill, read_statuses
from pipeline.extract import NewsRequest, NewsResult, PriceBar, fetch_news, fetch_prices
from pipeline.load import LoadStats, load, load_tickers
from pipeline.transform import CleanArticle, transform_articles

Mode = Literal["daily", "backfill"]


# Days of price bars per mode. Daily runs overlap so late bars are not missed.
PRICE_DAYS: dict[str, int] = {"daily": 7, "backfill": 30}


@dataclass(frozen=True)
class NewsPlan:
    requests: list[NewsRequest]
    remaining: int  # windows still unfetched after this run's budget


@task(name="Load tickers", cache_policy=NO_CACHE)
def load_tickers_task(database_url: str) -> dict[str, list[str]]:
    return load_tickers(get_engine(database_url))


@task(name="Plan news requests", cache_policy=NO_CACHE)
def plan_news_task(database_url: str, symbols: list[str], budget: int, now: datetime) -> NewsPlan:
    with get_engine(database_url).connect() as conn:
        plan = plan_gap_fill(symbols, read_statuses(conn), now)
    return NewsPlan(requests=plan[:budget], remaining=max(0, len(plan) - budget))


@task(name="Extract news", cache_policy=NO_CACHE)
def extract_news_task(
    aliases_by_symbol: dict[str, list[str]], plan: NewsPlan, settings: Settings
) -> NewsResult:
    return fetch_news(
        plan.requests, aliases_by_symbol, settings.news_api_key, settings.request_budget
    )


@task(name="Extract prices", cache_policy=NO_CACHE)
def extract_prices_task(symbols: list[str], price_days: int, now: datetime) -> list[PriceBar]:
    end = now.date() + timedelta(days=1)  # yfinance's end date is exclusive
    return fetch_prices(symbols, start=now.date() - timedelta(days=price_days), end=end)


@task(name="Transform", cache_policy=NO_CACHE)
def transform_task(news: NewsResult, aliases_by_symbol: dict[str, list[str]]) -> list[CleanArticle]:
    return transform_articles(news.articles, aliases_by_symbol)


@task(name="Load", cache_policy=NO_CACHE)
def load_task(
    database_url: str,
    articles: list[CleanArticle],
    prices: list[PriceBar],
    news: NewsResult,
    now: datetime,
) -> LoadStats:
    return load(get_engine(database_url), articles, prices, news.fetched, now)


@flow(name="financial-news-etl")
def etl_flow(mode: Mode = "daily") -> dict[str, Any]:
    logger = get_run_logger()
    settings = Settings.from_env()
    now = datetime.now(UTC)

    aliases_by_symbol = load_tickers_task(settings.database_url)
    if not aliases_by_symbol:
        raise RuntimeError("No tickers found. Run `alembic upgrade head` to seed them.")

    symbols = list(aliases_by_symbol)
    plan = plan_news_task(settings.database_url, symbols, settings.request_budget, now)
    news = extract_news_task(aliases_by_symbol, plan, settings)
    prices = extract_prices_task(symbols, PRICE_DAYS[mode], now)
    articles = transform_task(news, aliases_by_symbol)
    stats = load_task(settings.database_url, articles, prices, news, now)

    # Still to fetch: beyond this run's budget, refused or failed, or fetched too early.
    unfetched = len(news.failed) + len(news.skipped)
    provisional = sum(not is_settled(f.request.day, now) for f in news.fetched)
    summary = {
        "mode": mode,
        "news_requests": news.requests_made,
        "news_windows_capped": sum(f.capped for f in news.fetched),
        "news_windows_remaining": plan.remaining + unfetched + provisional,
        "news_requests_failed": [str(r) for r in news.failed],
        "news_requests_skipped": [str(r) for r in news.skipped],
        "inserted": asdict(stats),
    }
    logger.info("Run summary: %s", summary)
    return summary
