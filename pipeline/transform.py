"""Clean raw NewsAPI articles, link them to tickers and score sentiment."""

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

from nltk.sentiment.vader import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

SENTIMENT_MODEL = "vader"


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


def match_tickers(text: str, patterns: Mapping[str, re.Pattern[str]]) -> set[str]:
    return {symbol for symbol, pattern in patterns.items() if pattern.search(text)}


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
) -> list[CleanArticle]:
    """Drop incomplete or duplicate articles and those that mention no tracked ticker."""
    patterns = compile_alias_patterns(aliases_by_symbol)
    seen_urls: set[str] = set()
    cleaned: list[CleanArticle] = []
    dropped = unmatched = 0

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

        description = _clean_text(raw.get("description"))
        text = f"{title}. {description}" if description else title
        symbols = match_tickers(text, patterns)
        if not symbols:
            unmatched += 1
            continue

        seen_urls.add(url)
        score, label = score_sentiment(text)
        source = raw.get("source")
        cleaned.append(
            CleanArticle(
                url=url,
                title=title,
                description=description,
                source=_clean_text(source.get("name")) if isinstance(source, dict) else None,
                published_at=published_at,
                symbols=frozenset(symbols),
                sentiment_score=score,
                sentiment_label=label,
            )
        )

    logger.info(
        "Transform: %d articles kept, %d incomplete, %d without a ticker match",
        len(cleaned),
        dropped,
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
