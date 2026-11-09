"""Agent tools. `default_registry()` is what the agent loop will use."""

from tools.news import LIST_TICKERS, SEARCH_NEWS, SENTIMENT_TREND
from tools.prices import COMPARE_TICKERS, PRICE_HISTORY
from tools.registry import Tool, ToolRegistry
from tools.sql import RUN_SQL
from tools.web import WEB_SEARCH

ALL_TOOLS: list[Tool] = [
    LIST_TICKERS,
    SEARCH_NEWS,
    SENTIMENT_TREND,
    PRICE_HISTORY,
    COMPARE_TICKERS,
    RUN_SQL,
    WEB_SEARCH,
]


def default_registry() -> ToolRegistry:
    return ToolRegistry(ALL_TOOLS)
