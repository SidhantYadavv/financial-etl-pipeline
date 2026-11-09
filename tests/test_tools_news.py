from datetime import date

import pytest

from tests.tool_data import AAPL_BURST, AS_OF
from tools.registry import ToolRegistry

pytestmark = pytest.mark.usefixtures("tools_db")


def test_list_tickers(registry: ToolRegistry) -> None:
    result = registry.call("list_tickers", {})

    assert result["ok"], result
    data = result["data"]
    assert len(data["tickers"]) == 20
    nvda = next(t for t in data["tickers"] if t["symbol"] == "NVDA")
    assert nvda == {
        "symbol": "NVDA",
        "company_name": "NVIDIA Corporation",
        "aliases": ["Nvidia", "NVDA"],
        "articles": 32,
    }
    assert data["news_dates"] == {"first": "2026-06-01", "last": "2026-06-30"}
    assert data["price_dates"] == {"first": "2026-06-01", "last": "2026-06-30"}
    assert result["as_of"] == AS_OF.isoformat()


def test_list_tickers_rejects_arguments(registry: ToolRegistry) -> None:
    result = registry.call("list_tickers", {"ticker": "NVDA"})
    assert not result["ok"]
    assert "ticker" in result["error"]


def test_search_news_last_7_days_is_anchored_to_the_data(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "NVDA", "days": 7})

    assert date.today() > AS_OF  # the clock is far past the data
    assert result["ok"], result
    data = result["data"]
    assert (data["start_date"], data["end_date"]) == ("2026-06-24", "2026-06-30")
    assert result["as_of"] == "2026-06-30"
    assert data["total_matching"] == 9  # 7 daily + late-night + shared
    assert data["returned"] == 9
    assert data["truncated"] is False


def test_search_news_returns_newest_first_with_citations(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "NVDA", "days": 7})

    articles, sources = result["data"]["articles"], result["sources"]
    published = [a["published_at"] for a in articles]
    assert published == sorted(published, reverse=True)
    assert articles[0]["title"] == "Nvidia late-night filing"
    assert [s["id"] for s in sources] == [a["id"] for a in articles]
    assert set(sources[0]) == {"id", "title", "source", "published_at", "url"}
    assert sources[0]["url"] == "https://news.test/nvda/late"
    shared = next(a for a in articles if a["title"] == "Nvidia and Apple lead tech")
    assert shared["tickers"] == ["AAPL", "NVDA"]
    assert shared["sentiment"] == {"score": 0.8, "label": "positive"}


@pytest.mark.parametrize("ticker", ["NVDA", "nvda", "Nvidia", "NVIDIA", "nvidia corporation"])
def test_tickers_and_company_names_are_case_insensitive(
    registry: ToolRegistry, ticker: str
) -> None:
    result = registry.call("search_news", {"ticker": ticker, "days": 1})
    assert result["ok"], result
    assert result["data"]["ticker"] == "NVDA"


def test_unknown_ticker_suggests_a_match(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "NVDIA"})
    assert result == {"ok": False, "error": "Unknown ticker 'NVDIA'. Did you mean NVDA?"}


def test_unknown_ticker_without_close_match_lists_tickers(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "Zebra Holdings"})
    assert not result["ok"]
    assert "Tracked tickers: AAPL, AMD" in result["error"]
    assert "list_tickers" in result["error"]


def test_explicit_dates_use_eastern_time(registry: ToolRegistry) -> None:
    # The late-night article is July 1 in UTC but June 30 in New York.
    result = registry.call(
        "search_news", {"ticker": "NVDA", "start_date": "2026-06-30", "end_date": "2026-06-30"}
    )
    assert result["data"]["total_matching"] == 3
    titles = {a["title"] for a in result["data"]["articles"]}
    assert "Nvidia late-night filing" in titles


def test_keyword_filter(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "NVDA", "days": 30, "keyword": "BLACKWELL"})
    assert result["data"]["total_matching"] == 6  # June 5, 10, 15, 20, 25, 30


def test_keyword_wildcards_are_literal(registry: ToolRegistry) -> None:
    exact = registry.call("search_news", {"ticker": "AAPL", "days": 2, "keyword": "50% off_"})
    assert exact["data"]["total_matching"] == 1

    wildcard = registry.call("search_news", {"ticker": "AAPL", "days": 2, "keyword": "%%"})
    assert not wildcard["ok"]


def test_limit_and_truncation(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "AAPL", "days": 2, "limit": 25})

    data = result["data"]
    assert data["total_matching"] == AAPL_BURST + 1
    assert data["returned"] == 25
    assert data["truncated"] is True
    assert len(result["sources"]) == 25


def test_limit_above_cap_is_rejected(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "AAPL", "limit": 26})
    assert not result["ok"]
    assert "limit" in result["error"]


def test_empty_window_explains_coverage(registry: ToolRegistry) -> None:
    result = registry.call("search_news", {"ticker": "AMD", "days": 7})
    assert result == {
        "ok": False,
        "error": "No AMD articles between 2026-06-24 and 2026-06-30. News data covers "
        "2026-06-01 to 2026-06-30; try a wider window.",
    }


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"ticker": "NVDA", "start_date": "2026-06-10", "end_date": "2026-06-01"}, "after"),
        ({"ticker": "NVDA", "days": 3, "start_date": "2026-06-01"}, "not both"),
        ({"ticker": "NVDA", "days": 0}, "days"),
        ({"ticker": "NVDA", "start_date": "June 1st"}, "start_date"),
        ({"ticker": "NVDA", "foo": 1}, "foo"),
        ({}, "ticker"),
    ],
)
def test_search_news_bad_arguments(registry: ToolRegistry, args: dict, message: str) -> None:
    result = registry.call("search_news", args)
    assert not result["ok"]
    assert message in result["error"]


def test_sentiment_trend_over_the_month(registry: ToolRegistry) -> None:
    result = registry.call("sentiment_trend", {"ticker": "nvidia", "days": 30})

    assert result["ok"], result
    data = result["data"]
    assert (data["start_date"], data["end_date"]) == ("2026-06-01", "2026-06-30")
    assert data["articles"] == 32
    assert len(data["daily"]) == 30
    assert data["first_half_avg"] == pytest.approx(-0.4)
    assert data["second_half_avg"] == pytest.approx((15 * 0.6 + 0.0 + 0.8) / 17, abs=1e-4)
    assert data["change"] > 0.9
    assert data["direction"] == "improving"
    assert result["as_of"] == "2026-06-30"


def test_sentiment_trend_last_7_days_is_anchored_to_the_data(registry: ToolRegistry) -> None:
    data = registry.call("sentiment_trend", {"ticker": "NVDA", "days": 7})["data"]
    assert [d["date"] for d in data["daily"]][0] == "2026-06-24"
    assert data["daily"][-1] == {
        "date": "2026-06-30",
        "avg_sentiment": pytest.approx((0.6 + 0.0 + 0.8) / 3, abs=1e-4),
        "articles": 3,
        "positive": 2,
        "negative": 0,
    }


def test_sentiment_trend_cites_extreme_articles(registry: ToolRegistry) -> None:
    sources = registry.call("sentiment_trend", {"ticker": "NVDA", "days": 30})["sources"]
    assert 2 <= len(sources) <= 10
    assert min(s["sentiment"] for s in sources) == -0.4
    assert max(s["sentiment"] for s in sources) == 0.8
    assert all(s["url"].startswith("https://news.test/") for s in sources)


def test_sentiment_trend_fills_quiet_days(registry: ToolRegistry) -> None:
    data = registry.call("sentiment_trend", {"ticker": "AMD", "days": 30})["data"]
    quiet = [d for d in data["daily"] if d["articles"] == 0]
    assert len(quiet) == 25
    assert all(d["avg_sentiment"] is None for d in quiet)
    assert data["direction"] == "insufficient data"  # no articles in the second half


def test_sentiment_trend_single_day_has_no_change(registry: ToolRegistry) -> None:
    data = registry.call("sentiment_trend", {"ticker": "NVDA", "days": 1})["data"]
    assert data["change"] is None
    assert data["direction"] == "insufficient data"


def test_sentiment_trend_empty_and_unknown(registry: ToolRegistry) -> None:
    empty = registry.call("sentiment_trend", {"ticker": "AMD", "days": 7})
    assert not empty["ok"] and "No AMD articles" in empty["error"]

    unknown = registry.call("sentiment_trend", {"ticker": "Nvidiaa"})
    assert unknown == {"ok": False, "error": "Unknown ticker 'Nvidiaa'. Did you mean NVDA?"}

    too_long = registry.call("sentiment_trend", {"ticker": "NVDA", "days": 91})
    assert not too_long["ok"] and "days" in too_long["error"]
