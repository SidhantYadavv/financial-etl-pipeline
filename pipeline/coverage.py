"""Which (ticker, day) news windows are fetched, and which to fetch next.

Every NewsAPI request covers one ticker and one New York calendar day. fetch_log
records each answered request, so a run can spend its budget on the gaps and the
next run resumes where it stopped:

1. days never fetched with a one-day window, newest first;
2. provisional days (fetched before NewsAPI's delay had passed), newest first.

Complete and capped days are never refetched: the same query would return the
same articles.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.dialects.postgresql import insert

from db.models import FetchLog
from pipeline.extract import MARKET_TZ, NEWS_API_MAX_DAYS, FetchedWindow, NewsRequest

# Free-tier NewsAPI only serves articles 24 hours after publication.
NEWS_API_DELAY = timedelta(hours=24)

COMPLETE, CAPPED, PROVISIONAL, NOT_FETCHED = "complete", "capped", "provisional", "not_fetched"

UPDATED_COLUMNS = ("fetched_at", "articles_returned", "total_results", "capped", "settled")

Statuses = Mapping[tuple[str, date], str]


def fetchable_days(now: datetime) -> list[date]:
    """Whole New York days NewsAPI can still serve, newest first. Today is still running."""
    today = now.astimezone(MARKET_TZ).date()
    oldest = (now - timedelta(days=NEWS_API_MAX_DAYS)).astimezone(MARKET_TZ).date() + timedelta(
        days=1
    )
    count = (today - oldest).days
    return [today - timedelta(days=i) for i in range(1, count + 1)]


def is_settled(day: date, fetched_at: datetime) -> bool:
    """True once every article published that day had become visible to the API."""
    return fetched_at >= NewsRequest("", day).end + NEWS_API_DELAY


def plan_gap_fill(symbols: Sequence[str], statuses: Statuses, now: datetime) -> list[NewsRequest]:
    """All windows still worth fetching, in the order to fetch them."""
    days = fetchable_days(now)
    missing = [NewsRequest(s, d) for d in days for s in symbols if (s, d) not in statuses]
    provisional = [
        NewsRequest(s, d) for d in days for s in symbols if statuses.get((s, d)) == PROVISIONAL
    ]
    return missing + provisional


def read_statuses(conn: Connection) -> dict[tuple[str, date], str]:
    rows = conn.execute(select(FetchLog.symbol, FetchLog.day, FetchLog.status))
    return {(symbol, day): status for symbol, day, status in rows}


def record_fetches(conn: Connection, fetched: Iterable[FetchedWindow], now: datetime) -> int:
    """Upsert one row per answered request; a refetch replaces a provisional row."""
    rows = [
        {
            "symbol": f.request.symbol,
            "day": f.request.day,
            "fetched_at": now,
            "articles_returned": f.articles_returned,
            "total_results": f.total_results,
            "capped": f.capped,
            "settled": is_settled(f.request.day, now),
        }
        for f in fetched
    ]
    if not rows:
        return 0
    statement = insert(FetchLog).values(rows)
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[FetchLog.symbol, FetchLog.day],
            set_={column: statement.excluded[column] for column in UPDATED_COLUMNS},
        )
    )
    return len(rows)


@dataclass(frozen=True)
class TickerCoverage:
    symbol: str
    days: int
    with_articles: int
    complete: int
    capped: int
    provisional: int
    not_fetched: int


def coverage_report(engine: Engine, now: datetime) -> tuple[list[date], list[TickerCoverage]]:
    """Per ticker, over the days NewsAPI can still serve."""
    days = fetchable_days(now)
    first, last = days[-1], days[0]
    with engine.connect() as conn:
        statuses = read_statuses(conn)
        symbols = conn.execute(text("SELECT symbol FROM tickers ORDER BY symbol")).scalars().all()
        with_articles = dict(
            conn.execute(
                text(
                    """
                    SELECT t.symbol, count(DISTINCT
                           (a.published_at AT TIME ZONE 'America/New_York')::date)
                    FROM articles a JOIN article_tickers t ON t.article_id = a.id
                    WHERE a.published_at >= (CAST(:first AS date)::timestamp
                                             AT TIME ZONE 'America/New_York')
                      AND a.published_at < ((CAST(:last AS date) + 1)::timestamp
                                            AT TIME ZONE 'America/New_York')
                    GROUP BY t.symbol
                    """
                ),
                {"first": first, "last": last},
            ).all()
        )
    report = []
    for symbol in symbols:
        counts = {COMPLETE: 0, CAPPED: 0, PROVISIONAL: 0, NOT_FETCHED: 0}
        for day in days:
            counts[statuses.get((symbol, day), NOT_FETCHED)] += 1
        report.append(
            TickerCoverage(
                symbol=symbol,
                days=len(days),
                with_articles=with_articles.get(symbol, 0),
                complete=counts[COMPLETE],
                capped=counts[CAPPED],
                provisional=counts[PROVISIONAL],
                not_fetched=counts[NOT_FETCHED],
            )
        )
    return days, report


def format_report(days: list[date], report: list[TickerCoverage]) -> str:
    header = ("ticker", "days", "with data", "complete", "capped", "provisional", "missing")
    lines = [
        f"News coverage {days[-1]} to {days[0]} ({len(days)} New York days NewsAPI can serve)",
        "  complete: fetched in full; capped: hit the 100-article limit (a sample of the day);",
        "  provisional: fetched before NewsAPI's 24h delay passed; missing: not fetched yet.",
        "",
        "{:<7}{:>6}{:>11}{:>10}{:>8}{:>13}{:>9}".format(*header),
    ]
    for r in report:
        lines.append(
            f"{r.symbol:<7}{r.days:>6}{r.with_articles:>11}{r.complete:>10}{r.capped:>8}"
            f"{r.provisional:>13}{r.not_fetched:>9}"
        )
    missing = sum(r.not_fetched for r in report)
    provisional = sum(r.provisional for r in report)
    lines += [
        "",
        f"Requests still needed: {missing + provisional} "
        f"({missing} missing, {provisional} provisional)",
    ]
    return "\n".join(lines)
