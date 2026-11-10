"""Clean raw NewsAPI articles, link them to tickers and score sentiment."""

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any
from urllib.parse import urlparse

from nltk.sentiment.vader import SentimentIntensityAnalyzer

from pipeline.config import BLOCKED_SOURCES

logger = logging.getLogger(__name__)

SENTIMENT_MODEL = "vader"

# A passing mention in the description ("...with an Nvidia RTX GPU") is not news about
# the company; a title mention or repeated description mentions are.
MIN_DESCRIPTION_MENTIONS = 2


@dataclass(frozen=True)
class CleanArticle:
    url: str
    title: str
    description: str | None
    source: str | None
    published_at: datetime
    symbols: frozenset[str]
    sentiment_score: float
    sentiment_label: str


def compile_alias_patterns(
    aliases_by_symbol: Mapping[str, Iterable[str]],
) -> dict[str, re.Pattern[str]]:
    """One case-insensitive, whole-word pattern per symbol."""
    patterns = {}
    for symbol, aliases in aliases_by_symbol.items():
        terms = sorted({a.strip() for a in aliases if a.strip()}, key=len, reverse=True)
        if terms:
            alternation = "|".join(re.escape(term) for term in terms)
            # Lookarounds instead of \b so aliases ending in punctuation ("J&J") still work.
            patterns[symbol] = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)
    return patterns


def match_tickers(
    title: str,
    description: str | None,
    patterns: Mapping[str, re.Pattern[str]],
    title_patterns: Mapping[str, re.Pattern[str]] | None = None,
) -> set[str]:
    """Symbols named in the title, or at least twice in the description.

    `title_patterns` (CEO names) only count in the title: a description that quotes
    a CEO is often about something else.
    """
    matched = {
        symbol
        for symbol, pattern in patterns.items()
        if pattern.search(title)
        or len(pattern.findall(description or "")) >= MIN_DESCRIPTION_MENTIONS
    }
    matched |= {s for s, pattern in (title_patterns or {}).items() if pattern.search(title)}
    return matched


def is_blocked_source(source: str | None, url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    name = (source or "").casefold().removeprefix("www.")
    names = {name, f"{name}.com"}
    return bool(names & BLOCKED_SOURCES) or any(
        host == domain or host.endswith(f".{domain}") for domain in BLOCKED_SOURCES
    )


@cache
def get_analyzer() -> SentimentIntensityAnalyzer:
    try:
        return SentimentIntensityAnalyzer()
    except LookupError as exc:
        raise RuntimeError(
            "VADER lexicon not found. Run: python -m nltk.downloader vader_lexicon"
        ) from exc


def score_sentiment(text: str) -> tuple[float, str]:
    compound = get_analyzer().polarity_scores(text)["compound"]
    if compound >= 0.05:
        return compound, "positive"
    if compound <= -0.05:
        return compound, "negative"
    return compound, "neutral"


def transform_articles(
    raw_articles: Iterable[Mapping[str, Any]],
    aliases_by_symbol: Mapping[str, Iterable[str]],
    title_aliases_by_symbol: Mapping[str, Iterable[str]] | None = None,
) -> list[CleanArticle]:
    """Drop incomplete, duplicate and blocked-source articles and those about no ticker."""
    patterns = compile_alias_patterns(aliases_by_symbol)
    title_patterns = compile_alias_patterns(title_aliases_by_symbol or {})
    seen_urls: set[str] = set()
    cleaned: list[CleanArticle] = []
    dropped = blocked = unmatched = 0

    for raw in raw_articles:
        url = _clean_text(raw.get("url"))
        title = _clean_text(raw.get("title"))
        published_at = _parse_timestamp(raw.get("publishedAt"))
        # NewsAPI replaces taken-down articles with the title "[Removed]".
        if not url or not title or title == "[Removed]" or published_at is None:
            dropped += 1
            continue
        if url in seen_urls:
            continue

        source = raw.get("source")
        source_name = _clean_text(source.get("name")) if isinstance(source, dict) else None
        if is_blocked_source(source_name, url):
            blocked += 1
            continue

        description = _clean_text(raw.get("description"))
        symbols = match_tickers(title, description, patterns, title_patterns)
        if not symbols:
            unmatched += 1
            continue

        seen_urls.add(url)
        score, label = score_sentiment(f"{title}. {description}" if description else title)
        cleaned.append(
            CleanArticle(
                url=url,
                title=title,
                description=description,
                source=source_name,
                published_at=published_at,
                symbols=frozenset(symbols),
                sentiment_score=score,
                sentiment_label=label,
            )
        )

    logger.info(
        "Transform: %d articles kept, %d incomplete, %d from blocked sources, "
        "%d without a ticker match",
        len(cleaned),
        dropped,
        blocked,
        unmatched,
    )
    return cleaned


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
