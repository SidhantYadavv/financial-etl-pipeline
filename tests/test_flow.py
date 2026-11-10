"""End-to-end flow runs with NewsAPI and yfinance mocked and a real Postgres database."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import pandas as pd
import pytest
import requests
from prefect.testing.utilities import prefect_test_harness
from sqlalchemy import Engine, text

from pipeline import extract
from pipeline.coverage import fetchable_days
from pipeline.extract import MARKET_TZ
from pipeline.flow import etl_flow
from tests.conftest import FakeResponse, count_rows, news_article


@pytest.fixture(scope="module", autouse=True)
def prefect_backend() -> Iterator[None]:
    with prefect_test_harness():
        yield


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, test_database_url: str, db: Engine) -> Engine:
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("NEWS_API_KEY", "test-key")
    monkeypatch.setenv("NEWS_API_REQUEST_BUDGET", "80")
    return db


class FakeNewsApi:
    """One article per (alias, day) request, plus one article shared by every request.

    Requests for `capped_symbol` report more results than one page holds.
    """

    def __init__(self, rate_limit_after: int | None = None) -> None:
        self.calls: list[tuple[str, date]] = []
        self.rate_limit_after = rate_limit_after
        self.capped_symbol = "Nvidia"
        self.extra: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], **kwargs: Any) -> FakeResponse:
        if self.rate_limit_after is not None and len(self.calls) >= self.rate_limit_after:
            return FakeResponse(429, {"status": "error", "code": "rateLimited"})
        first_alias = params["q"].split('"')[1]
        start = datetime.fromisoformat(params["from"]).replace(tzinfo=UTC)
        day = start.astimezone(MARKET_TZ).date()
        self.calls.append((first_alias, day))
        unique = news_article(
            f"{first_alias} shares rise on strong results",
            f"https://news.test/{quote(first_alias)}/{day}",
            published_at=f"{(start + timedelta(hours=15)).isoformat()}",
        )
        shared = news_article("Nvidia and Apple lead tech rally", "https://news.test/shared")
        total = 250 if first_alias == self.capped_symbol else 2
        return FakeResponse(
            200,
            {"status": "ok", "totalResults": total, "articles": [unique, shared, *self.extra]},
        )


class FakeTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, start: Any, end: Any, **kwargs: Any) -> pd.DataFrame:
        days = pd.bdate_range(start, end, inclusive="left", tz="America/New_York")
        n = len(days)
        return pd.DataFrame(
            {"Open": [1.0] * n, "High": [2.0] * n, "Low": [0.5] * n, "Close": [1.5] * n}
            | {"Volume": [100] * n},
            index=days,
        )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeNewsApi:
    fake = FakeNewsApi()
    monkeypatch.setattr(extract.requests, "get", fake.get)
    monkeypatch.setattr(extract.yf, "Ticker", FakeTicker)
    return fake


def fetch_log(engine: Engine) -> dict[tuple[str, date], str]:
    with engine.connect() as conn:
        return dict(
            ((symbol, day), status)
            for symbol, day, status in conn.execute(
                text("SELECT symbol, day, status FROM fetch_log")
            )
        )


def test_run_spends_the_budget_on_the_newest_missing_days(env: Engine, api: FakeNewsApi) -> None:
    summary = etl_flow(mode="daily")

    days = fetchable_days(datetime.now(UTC))
    assert summary["news_requests"] == 80  # the whole budget, no more
    assert len(api.calls) == 80
    # 20 tickers x the 4 newest days, newest day first.
    assert sorted({d for _, d in api.calls}, reverse=True) == days[:4]
    assert api.calls[0][1] == days[0]
    # The newest day's 20 windows are provisional and still to be refetched.
    assert summary["news_windows_remaining"] == 20 * len(days) - 80 + 20
    logged = fetch_log(env)
    assert len(logged) == 80
    assert summary["inserted"]["fetch_log"] == 80
    assert count_rows(env)["articles"] == 81  # 80 unique + 1 shared


def test_next_run_resumes_where_the_last_one_stopped(
    env: Engine, api: FakeNewsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWS_API_REQUEST_BUDGET", "30")
    first = etl_flow(mode="daily")
    first_calls = list(api.calls)
    after_first = count_rows(env)

    second = etl_flow(mode="daily")
    second_calls = api.calls[len(first_calls) :]

    assert len(first_calls) == len(second_calls) == 30
    assert not set(first_calls) & set(second_calls)  # nothing fetched twice
    days = fetchable_days(datetime.now(UTC))
    # Newest day for all 20 tickers, then the next day for the rest.
    assert sorted({d for _, d in first_calls + second_calls}, reverse=True) == days[:3]
    assert second["news_windows_remaining"] == first["news_windows_remaining"] - 30
    assert len(fetch_log(env)) == 60
    assert count_rows(env)["articles"] == after_first["articles"] + 30  # shared one not repeated


def test_rate_limited_run_logs_only_what_it_fetched(
    env: Engine, api: FakeNewsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.rate_limit_after = 12
    summary = etl_flow(mode="daily")

    assert summary["news_requests"] == 13  # the 13th request was refused
    assert len(summary["news_requests_skipped"]) == 80 - 12
    assert len(fetch_log(env)) == 12

    api.rate_limit_after = None
    monkeypatch.setenv("NEWS_API_REQUEST_BUDGET", "10")
    etl_flow(mode="daily")
    resumed = api.calls[12:]
    assert len(resumed) == 10
    assert not set(api.calls[:12]) & set(resumed)
    assert len(fetch_log(env)) == 22


def test_ceo_headlines_are_linked_by_the_flow(
    env: Engine, api: FakeNewsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWS_API_REQUEST_BUDGET", "1")
    api.extra = [news_article("Jensen Huang's keynote recap", "https://news.test/keynote")]

    etl_flow(mode="daily")

    with env.connect() as conn:
        symbols = (
            conn.execute(
                text(
                    "SELECT t.symbol FROM article_tickers t JOIN articles a ON a.id = t.article_id "
                    "WHERE a.url = 'https://news.test/keynote'"
                )
            )
            .scalars()
            .all()
        )
    assert symbols == ["NVDA"]


def test_capped_and_provisional_days_are_recorded(env: Engine, api: FakeNewsApi) -> None:
    etl_flow(mode="daily")

    logged = fetch_log(env)
    newest = fetchable_days(datetime.now(UTC))[0]
    # Yesterday is still inside NewsAPI's 24h delay, so it will be fetched again.
    assert logged[("AAPL", newest)] == "provisional"
    assert {s for (sym, d), s in logged.items() if sym == "NVDA" and d < newest} == {"capped"}
    assert {s for (sym, d), s in logged.items() if sym == "AAPL" and d < newest} == {"complete"}


def test_failed_news_api_does_not_crash_the_run(
    env: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_get(*args: Any, **kwargs: Any) -> None:
        raise requests.ConnectionError("NewsAPI unreachable")

    monkeypatch.setattr(extract.requests, "get", broken_get)
    monkeypatch.setattr(extract.yf, "Ticker", FakeTicker)

    summary = etl_flow(mode="daily")

    assert len(summary["news_requests_failed"]) == 80
    assert summary["inserted"]["articles"] == 0
    assert summary["inserted"]["fetch_log"] == 0  # failed windows stay missing
    assert summary["inserted"]["daily_prices"] > 0  # prices still load
