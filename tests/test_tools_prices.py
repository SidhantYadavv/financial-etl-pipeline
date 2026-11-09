import pytest

from tests.tool_data import AS_OF, TRADING_DAYS, nvda_close
from tools.registry import ToolRegistry

pytestmark = pytest.mark.usefixtures("tools_db")


def test_price_history_counts_trading_days(registry: ToolRegistry) -> None:
    result = registry.call("price_history", {"ticker": "nvda", "days": 5})

    assert result["ok"], result
    data = result["data"]
    window = TRADING_DAYS[-5:]
    assert [d["date"] for d in data["daily"]] == [d.isoformat() for d in window]
    assert data["trading_days"] == 5
    assert data["change_measured_from"] == f"close on {TRADING_DAYS[-6]}"
    base, last = nvda_close(len(TRADING_DAYS) - 6), nvda_close(len(TRADING_DAYS) - 1)
    assert data["total_change_pct"] == pytest.approx((last - base) / base * 100, abs=1e-3)
    assert data["best_day"]["date"] == window[0].isoformat()  # +1 on the smallest base
    assert data["worst_day"]["date"] == window[-1].isoformat()
    assert data["period_high"] == last + 1
    assert data["period_low"] == nvda_close(len(TRADING_DAYS) - 5) - 1
    assert data["notes"] == []
    assert result["as_of"] == AS_OF.isoformat()
    assert result["sources"] == []


def test_price_history_with_more_days_than_data(registry: ToolRegistry) -> None:
    data = registry.call("price_history", {"ticker": "NVDA", "days": 100})["data"]

    assert data["trading_days"] == len(TRADING_DAYS)
    assert data["notes"] == [f"Only {len(TRADING_DAYS)} trading days of NVDA prices are available."]
    assert data["change_measured_from"] == f"open on {TRADING_DAYS[0]}"
    assert data["daily"][0]["change_pct"] is None


def test_price_history_flat_stock(registry: ToolRegistry) -> None:
    data = registry.call("price_history", {"ticker": "Apple", "days": 3})["data"]
    assert data["total_change_pct"] == 0


def test_price_history_errors(registry: ToolRegistry) -> None:
    no_prices = registry.call("price_history", {"ticker": "TSLA"})
    assert no_prices == {
        "ok": False,
        "error": "No price data for TSLA. Prices cover 2026-06-01 to 2026-06-30.",
    }
    unknown = registry.call("price_history", {"ticker": "NVDIA"})
    assert unknown["error"] == "Unknown ticker 'NVDIA'. Did you mean NVDA?"
    for days in (0, 251):
        assert not registry.call("price_history", {"ticker": "NVDA", "days": days})["ok"]


def test_compare_tickers_side_by_side(registry: ToolRegistry) -> None:
    result = registry.call("compare_tickers", {"tickers": ["NVDA", "apple", "AMD"], "days": 7})

    assert result["ok"], result
    data = result["data"]
    assert (data["start_date"], data["end_date"]) == ("2026-06-24", "2026-06-30")
    rows = {r["ticker"]: r for r in data["tickers"]}
    # The window starts June 24, so the change runs from the June 23 close.
    base, last = nvda_close(len(TRADING_DAYS) - 6), nvda_close(len(TRADING_DAYS) - 1)
    assert rows["NVDA"]["price_change_pct"] == pytest.approx((last - base) / base * 100, abs=1e-3)
    assert rows["NVDA"]["trading_days"] == 5
    assert rows["NVDA"]["articles"] == 9
    assert rows["AAPL"]["price_change_pct"] == 0
    assert rows["AAPL"]["articles"] == 31
    assert rows["AMD"]["price_change_pct"] < 0
    assert rows["AMD"]["articles"] == 0
    assert rows["AMD"]["avg_sentiment"] is None
    assert data["best_price_change"] == "NVDA"
    assert data["most_articles"] == "AAPL"
    assert data["most_positive_sentiment"] == "NVDA"
    assert result["as_of"] == "2026-06-30"


def test_compare_tickers_cites_articles_per_ticker(registry: ToolRegistry) -> None:
    sources = registry.call("compare_tickers", {"tickers": ["NVDA", "AMD"], "days": 7})["sources"]
    assert {s["ticker"] for s in sources} == {"NVDA"}  # AMD has no news in the window
    assert all({"id", "title", "url", "published_at", "source"} <= set(s) for s in sources)


@pytest.mark.parametrize(
    ("tickers", "message"),
    [
        (["NVDA"], "tickers"),
        (["NVDA", "AMD", "AAPL", "MSFT", "INTC", "TSLA"], "tickers"),
        (["NVDA", "nvidia"], "must be different"),
        (["NVDA", "NVDIA"], "Did you mean NVDA?"),
    ],
)
def test_compare_tickers_bad_arguments(
    registry: ToolRegistry, tickers: list[str], message: str
) -> None:
    result = registry.call("compare_tickers", {"tickers": tickers})
    assert not result["ok"]
    assert message in result["error"]


def test_compare_tickers_with_no_data(registry: ToolRegistry) -> None:
    result = registry.call("compare_tickers", {"tickers": ["TSLA", "MSFT"]})
    assert result == {
        "ok": False,
        "error": "No prices or news for TSLA, MSFT between 2026-06-24 and 2026-06-30.",
    }
