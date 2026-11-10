from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
import requests

from pipeline import extract
from pipeline.config import BLOCKED_SOURCES
from pipeline.extract import (
    FetchedWindow,
    NewsRequest,
    build_query,
    fetch_news,
    fetch_prices,
)
from tests.conftest import FakeResponse, news_article

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
ALIASES = {"NVDA": ["Nvidia", "NVDA"], "AAPL": ["Apple", "AAPL"]}


def test_build_query_quotes_each_alias() -> None:
    assert build_query(["Nvidia", "NVDA"]) == '"Nvidia" OR "NVDA"'


def test_window_is_one_new_york_day() -> None:
    request = NewsRequest("NVDA", date(2026, 10, 27))

    assert request.start == datetime(2026, 10, 27, 4, tzinfo=UTC)  # midnight EDT
    assert request.end == datetime(2026, 10, 28, 4, tzinfo=UTC)
    assert str(request) == "NVDA 2026-10-27"


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25), (date(2026, 11, 2), 24)],
)
def test_windows_follow_daylight_saving_changes(day: date, hours: int) -> None:
    request = NewsRequest("NVDA", day)
    assert request.end - request.start == timedelta(hours=hours)
    assert NewsRequest("NVDA", day + timedelta(days=1)).start == request.end  # contiguous


@pytest.mark.parametrize(
    ("returned", "total", "capped"),
    [
        (100, 100, True),
        (100, 412, True),
        (99, 120, True),
        (37, 37, False),
        (0, 0, False),
        (72, 73, False),  # NewsAPI's total is a little high, but the page was not full
    ],
)
def test_capped_windows(returned: int, total: int, capped: bool) -> None:
    window = FetchedWindow(NewsRequest("NVDA", date(2026, 10, 27)), returned, total)
    assert window.capped is capped


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
    """n days for NVDA and AAPL, newest first."""
    days = [date(2026, 11, 2) - timedelta(days=i) for i in range(n)]
    return [NewsRequest(s, d) for d in days for s in ("NVDA", "AAPL")]


def test_fetch_news_collects_articles_and_sends_key_in_header(api: FakeNewsApi) -> None:
    ok = {"status": "ok", "articles": [news_article("Nvidia up", "https://a")]}
    api.responses.extend([FakeResponse(200, ok), FakeResponse(200, ok)])

    result = fetch_news(_plan(1), ALIASES, "secret", budget=10)

    assert len(result.articles) == 2
    assert result.requests_made == 2
    assert api.calls[0]["headers"] == {"X-Api-Key": "secret"}
    assert "secret" not in str(api.calls[0]["params"])
    assert api.calls[0]["params"]["q"] == '"Nvidia" OR "NVDA"'
    # 2026-11-02 in New York, in UTC, with an inclusive `to`.
    assert api.calls[0]["params"]["from"] == "2026-11-02T05:00:00"
    assert api.calls[0]["params"]["to"] == "2026-11-03T04:59:59"


def test_blocked_domains_are_excluded_at_the_source(api: FakeNewsApi) -> None:
    api.responses.append(FakeResponse(200, {"status": "ok", "articles": []}))

    fetch_news(_plan(1)[:1], ALIASES, "key", budget=1)

    excluded = api.calls[0]["params"]["excludeDomains"].split(",")
    assert set(excluded) == BLOCKED_SOURCES
    assert {"slickdeals.net", "dealnews.com", "pypi.org"} <= set(excluded)


def test_fetch_news_records_what_each_window_returned(api: FakeNewsApi) -> None:
    full = [news_article(f"Nvidia {i}", f"https://n/{i}") for i in range(100)]
    api.responses.extend(
        [
            FakeResponse(200, {"status": "ok", "totalResults": 431, "articles": full}),
            FakeResponse(200, {"status": "ok", "totalResults": 2, "articles": full[:2]}),
        ]
    )

    result = fetch_news(_plan(1), ALIASES, "key", budget=10)

    nvda, aapl = result.fetched
    assert (nvda.request.symbol, nvda.articles_returned, nvda.total_results) == ("NVDA", 100, 431)
    assert nvda.capped and not aapl.capped


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
    # Only answered requests count as fetched; failed windows stay unfetched.
    assert [f.articles_returned for f in result.fetched] == [0, 1]


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
