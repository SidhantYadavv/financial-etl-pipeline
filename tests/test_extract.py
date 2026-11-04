from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
import requests

from pipeline import extract
from pipeline.extract import (
    NewsRequest,
    build_query,
    fetch_news,
    fetch_prices,
    plan_news_requests,
)
from tests.conftest import FakeResponse, news_article

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
ALIASES = {"NVDA": ["Nvidia", "NVDA"], "AAPL": ["Apple", "AAPL"]}


def test_build_query_quotes_each_alias() -> None:
    assert build_query(["Nvidia", "NVDA"]) == '"Nvidia" OR "NVDA"'


def test_plan_orders_newest_window_first_and_caps_history() -> None:
    plan = plan_news_requests(["NVDA", "AAPL"], end=NOW, days=60, windows=3)

    assert len(plan) == 6
    assert [r.symbol for r in plan[:2]] == ["NVDA", "AAPL"]
    assert plan[0].end == NOW
    assert plan[-1].start == NOW - timedelta(days=extract.NEWS_API_MAX_DAYS)
    assert plan[0].start == plan[2].end  # windows are contiguous


class FakeNewsApi:
    """Stands in for requests.get: returns queued responses and records each call."""

    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeNewsApi:
    fake = FakeNewsApi()
    monkeypatch.setattr(extract.requests, "get", fake.get)
    return fake


def _plan(n: int) -> list[NewsRequest]:
    return plan_news_requests(["NVDA", "AAPL"], end=NOW, days=10, windows=n)


def test_fetch_news_collects_articles_and_sends_key_in_header(api: FakeNewsApi) -> None:
    ok = {"status": "ok", "articles": [news_article("Nvidia up", "https://a")]}
    api.responses.extend([FakeResponse(200, ok), FakeResponse(200, ok)])

    result = fetch_news(_plan(1), ALIASES, "secret", budget=10)

    assert len(result.articles) == 2
    assert result.requests_made == 2
    assert api.calls[0]["headers"] == {"X-Api-Key": "secret"}
    assert "secret" not in str(api.calls[0]["params"])
    assert api.calls[0]["params"]["q"] == '"Nvidia" OR "NVDA"'


def test_fetch_news_stops_at_budget_and_reports_skipped(api: FakeNewsApi) -> None:
    api.responses.extend([FakeResponse(200, {"status": "ok", "articles": []})] * 3)

    result = fetch_news(_plan(3), ALIASES, "key", budget=3)

    assert result.requests_made == 3
    assert len(result.skipped) == 3
    assert len(api.calls) == 3


def test_fetch_news_stops_on_rate_limit(api: FakeNewsApi) -> None:
    api.responses.extend(
        [
            FakeResponse(200, {"status": "ok", "articles": []}),
            FakeResponse(429, {"status": "error", "code": "rateLimited"}),
        ]
    )

    result = fetch_news(_plan(2), ALIASES, "key", budget=100)

    assert len(api.calls) == 2
    assert len(result.skipped) == 3
    assert result.articles == []


def test_fetch_news_survives_failed_and_empty_responses(api: FakeNewsApi) -> None:
    api.responses.extend(
        [
            requests.ConnectionError("network down"),
            FakeResponse(500, ValueError("not json")),
            FakeResponse(200, {"status": "error", "code": "apiKeyInvalid"}),
            FakeResponse(200, {"status": "ok", "articles": None}),
            FakeResponse(200, {"status": "ok", "articles": [news_article("Apple", "https://b")]}),
            FakeResponse(200, ["unexpected"]),
        ]
    )

    result = fetch_news(_plan(3), ALIASES, "key", budget=100)

    assert len(result.failed) == 4
    assert [a["url"] for a in result.articles] == ["https://b"]
    assert result.skipped == []


def _frame(days: list[str], tz: str = "America/New_York") -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.to_datetime(days)).tz_localize(tz)
    n = len(days)
    return pd.DataFrame(
        {
            "Open": [10.0] * n,
            "High": [11.0] * n,
            "Low": [9.0] * n,
            "Close": [10.5] * n,
            "Adj Close": [10.4] * n,
            "Volume": [1000] * n,
        },
        index=index,
    )


class FakeTicker:
    frames: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, **kwargs: Any) -> pd.DataFrame:
        FakeTicker.calls.append({"symbol": self.symbol, **kwargs})
        frame = FakeTicker.frames[self.symbol]
        if isinstance(frame, Exception):
            raise frame
        return frame


@pytest.fixture
def fake_yf(monkeypatch: pytest.MonkeyPatch) -> type[FakeTicker]:
    FakeTicker.frames = {}
    FakeTicker.calls = []
    monkeypatch.setattr(extract.yf, "Ticker", FakeTicker)
    return FakeTicker


def test_fetch_prices_converts_frames_and_drops_incomplete_rows(fake_yf: Any) -> None:
    frame = _frame(["2026-10-29", "2026-10-30", "2026-10-31"])
    frame.loc[frame.index[1], "Close"] = float("nan")
    fake_yf.frames = {"NVDA": frame}

    bars = fetch_prices(["NVDA"], date(2026, 10, 29), date(2026, 11, 1))

    assert [b.date for b in bars] == [date(2026, 10, 29), date(2026, 10, 31)]
    assert bars[0].close == 10.5
    assert bars[0].volume == 1000
    assert fake_yf.calls[0]["auto_adjust"] is False


def test_fetch_prices_never_requests_todays_partial_bar(fake_yf: Any) -> None:
    fake_yf.frames = {"NVDA": _frame([])}
    today = datetime.now(extract.MARKET_TZ).date()

    fetch_prices(["NVDA"], today - timedelta(days=5), today + timedelta(days=1))

    assert fake_yf.calls[0]["end"] == today  # exclusive end, so today is not fetched


def test_fetch_prices_survives_errors_and_empty_frames(fake_yf: Any) -> None:
    fake_yf.frames = {
        "NVDA": RuntimeError("Yahoo is down"),
        "AAPL": _frame([]),
        "MSFT": None,
        "AMD": _frame(["2026-10-30"]),
    }

    bars = fetch_prices(["NVDA", "AAPL", "MSFT", "AMD"], date(2026, 10, 1), date(2026, 11, 1))

    assert [b.symbol for b in bars] == ["AMD"]
