"""Helpers shared by the tools: engine, ticker resolution and data-anchored dates."""

import difflib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, text

from db.session import get_readonly_engine, readonly_database_url
from tools.registry import ToolError

# News "days" are calendar days in the market's time zone, so they line up with trading days.
MARKET_TZ = "America/New_York"
SENTIMENT_MODEL = "vader"
NEWS_DATE = f"(a.published_at AT TIME ZONE '{MARKET_TZ}')::date"
NO_DATA_HINT = "Run `python -m pipeline backfill` to load data."

_COMPANY_SUFFIX = re.compile(
    r"^the\s+|,?\s+(inc|corp|corporation|company|co|group|platforms)\.?$|\s+&\s+co\.?$",
    re.IGNORECASE,
)


def engine() -> Engine:
    """Every tool connects as the read-only agent role."""
    return get_readonly_engine(readonly_database_url())


@dataclass(frozen=True)
class TickerInfo:
    symbol: str
    company_name: str
    aliases: list[str]


def all_tickers(conn: Connection) -> list[TickerInfo]:
    rows = conn.execute(text("SELECT symbol, company_name, aliases FROM tickers ORDER BY symbol"))
    return [TickerInfo(s, n, list(a)) for s, n, a in rows]


def short_company_name(name: str) -> str:
    """'The Walt Disney Company' -> 'Walt Disney', 'NVIDIA Corporation' -> 'NVIDIA'."""
    previous = None
    while previous != name:
        previous, name = name, _COMPANY_SUFFIX.sub("", name).strip()
    return name


def resolve_ticker(conn: Connection, raw: str) -> str:
    """Map a symbol, alias or company name (any case) to a tracked symbol."""
    key = raw.strip()
    tickers = all_tickers(conn)
    symbols = {t.symbol for t in tickers}
    if key.upper() in symbols:
        return key.upper()

    names: dict[str, set[str]] = {}
    for t in tickers:
        for name in [*t.aliases, t.company_name, short_company_name(t.company_name)]:
            names.setdefault(name.casefold(), set()).add(t.symbol)
    matches = names.get(key.casefold(), set())
    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        raise ToolError(f"'{raw}' is ambiguous: it could be {', '.join(sorted(matches))}.")

    vocabulary = {s.casefold(): {s} for s in symbols} | names
    close = difflib.get_close_matches(key.casefold(), list(vocabulary), n=3, cutoff=0.6)
    suggestions = sorted({s for c in close for s in vocabulary[c]})
    if suggestions:
        raise ToolError(f"Unknown ticker '{raw}'. Did you mean {' or '.join(suggestions)}?")
    raise ToolError(
        f"Unknown ticker '{raw}'. Tracked tickers: {', '.join(sorted(symbols))}. "
        "Use list_tickers to see company names."
    )


def news_date_range(conn: Connection) -> tuple[date, date]:
    """First and latest news dates in the database. "Recent" is relative to the latter."""
    first, last = conn.execute(
        text(f"SELECT min({NEWS_DATE}), max({NEWS_DATE}) FROM articles a")
    ).one()
    if last is None:
        raise ToolError(f"No news has been loaded yet. {NO_DATA_HINT}")
    return first, last


def price_date_range(conn: Connection) -> tuple[date, date]:
    """First and latest trading days with prices (the latest completed session)."""
    first, last = conn.execute(text("SELECT min(date), max(date) FROM daily_prices")).one()
    if last is None:
        raise ToolError(f"No prices have been loaded yet. {NO_DATA_HINT}")
    return first, last


def news_window_sql(alias: str = "a") -> str:
    """Index-friendly filter for articles published on NY dates :start..:end inclusive."""
    return (
        f"{alias}.published_at >= (CAST(:start AS date)::timestamp AT TIME ZONE '{MARKET_TZ}') "
        f"AND {alias}.published_at < "
        f"((CAST(:end AS date) + 1)::timestamp AT TIME ZONE '{MARKET_TZ}')"
    )


def calendar_window(end: date, days: int) -> tuple[date, date]:
    """The `days` calendar days ending on `end`, inclusive."""
    return end - timedelta(days=days - 1), end


def article_source(row: Any) -> dict[str, Any]:
    """Citation entry for an article row with id, title, source, published_at and url."""
    return {
        "id": row.id,
        "title": row.title,
        "source": row.source,
        "published_at": row.published_at,
        "url": row.url,
    }


def pct_change(start: float, end: float) -> float | None:
    return None if not start else (end - start) / start * 100
