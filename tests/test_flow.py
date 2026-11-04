"""End-to-end flow runs with NewsAPI and yfinance mocked and a real Postgres database."""

from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pandas as pd
import pytest
import requests
from prefect.testing.utilities import prefect_test_harness
from sqlalchemy import Engine

from pipeline import extract
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


def fake_news_get(url: str, params: dict[str, Any], **kwargs: Any) -> FakeResponse:
    """Two articles per request; one of them is shared by every request."""
    first_alias = params["q"].split('"')[1]
    day = params["from"][:10]  # stable across two runs made seconds apart
    unique = news_article(
        f"{first_alias} shares rise on strong results",
        f"https://news.test/{quote(first_alias)}/{day}",
        published_at=f"{day}T12:00:00Z",
    )
    shared = news_article("Nvidia and Apple lead tech rally", "https://news.test/shared")
    return FakeResponse(200, {"status": "ok", "totalResults": 2, "articles": [unique, shared]})


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
def mocked_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract.requests, "get", fake_news_get)
    monkeypatch.setattr(extract.yf, "Ticker", FakeTicker)


@pytest.mark.usefixtures("mocked_sources")
def test_backfill_twice_adds_no_duplicates(env: Engine) -> None:
    first = etl_flow(mode="backfill")
    after_first = count_rows(env)

    second = etl_flow(mode="backfill")

    assert first["news_requests"] == 80  # 20 tickers x 4 windows fits the budget
    assert first["news_requests_skipped"] == []
    assert after_first["articles"] == 81  # 80 unique + 1 shared
    assert after_first["sentiment"] == 81
    assert after_first["daily_prices"] > 0
    assert set(second["inserted"].values()) == {0}
    assert count_rows(env) == after_first


@pytest.mark.usefixtures("mocked_sources")
def test_small_budget_skips_oldest_windows(env: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_API_REQUEST_BUDGET", "30")

    summary = etl_flow(mode="backfill")

    # 30 // 20 tickers = 1 window each; 20 requests made, nothing skipped.
    assert summary["news_requests"] == 20
    assert summary["news_requests_skipped"] == []


def test_failed_news_api_does_not_crash_the_run(
    env: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_get(*args: Any, **kwargs: Any) -> None:
        raise requests.ConnectionError("NewsAPI unreachable")

    monkeypatch.setattr(extract.requests, "get", broken_get)
    monkeypatch.setattr(extract.yf, "Ticker", FakeTicker)

    summary = etl_flow(mode="daily")

    assert len(summary["news_requests_failed"]) == 20
    assert summary["inserted"]["articles"] == 0
    assert summary["inserted"]["daily_prices"] > 0  # prices still load
