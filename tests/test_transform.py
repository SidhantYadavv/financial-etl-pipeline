from datetime import UTC, datetime

import pytest

from pipeline.transform import (
    compile_alias_patterns,
    match_tickers,
    score_sentiment,
    transform_articles,
)
from tests.conftest import news_article

ALIASES = {
    "NVDA": ["Nvidia", "NVDA"],
    "META": ["Meta Platforms", "Meta", "Facebook"],
    "AAPL": ["Apple", "AAPL"],
    "INTC": ["Intel", "INTC"],
    "JNJ": ["Johnson & Johnson", "J&J", "JNJ"],
    "JPM": ["JPMorgan", "J.P. Morgan", "JPM"],
    "KO": ["Coca-Cola", "Coca Cola"],
}
PATTERNS = compile_alias_patterns(ALIASES)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Nvidia beats estimates", {"NVDA"}),
        ("NVDA slides after hours", {"NVDA"}),
        ("Facebook parent posts record ad sales", {"META"}),
        ("Apple and Nvidia lead the Nasdaq", {"AAPL", "NVDA"}),
        ("J&J raises guidance", {"JNJ"}),
        ("J.P. Morgan upgrades the bank sector", {"JPM"}),
        ("Coca-Cola volumes rise", {"KO"}),
        ("Shares of (NVDA) rose", {"NVDA"}),
    ],
)
def test_aliases_match(text: str, expected: set[str]) -> None:
    assert match_tickers(text, PATTERNS) == expected


@pytest.mark.parametrize("text", ["NVIDIA rallies", "nvidia rallies", "nViDiA rallies"])
def test_matching_is_case_insensitive(text: str) -> None:
    assert match_tickers(text, PATTERNS) == {"NVDA"}


@pytest.mark.parametrize(
    "text",
    [
        "New metadata standards for filings",
        "Metaverse hype fades",
        "Applebee's opens new locations",
        "Artificial intelligence spending grows",
        "Pineapple prices climb",
    ],
)
def test_no_partial_word_matches(text: str) -> None:
    assert match_tickers(text, PATTERNS) == set()


def test_empty_aliases_are_ignored() -> None:
    assert compile_alias_patterns({"XYZ": ["", "  "]}) == {}


def test_sentiment_labels() -> None:
    assert score_sentiment("Profits soar to a record, a great win")[1] == "positive"
    assert score_sentiment("Shares crash amid fraud scandal and losses")[1] == "negative"
    assert score_sentiment("The company held a meeting on Tuesday")[1] == "neutral"


def test_transform_cleans_dedupes_and_links() -> None:
    raw = [
        news_article("Nvidia hits record high", "https://x.com/1", "Strong demand for chips"),
        news_article("Nvidia hits record high", "https://x.com/1"),  # same url
        news_article("Markets wrap", "https://x.com/2", "Apple and Intel both fell"),
        news_article("Bond yields climb", "https://x.com/3"),  # no ticker
        news_article("[Removed]", "https://removed.com"),
        news_article("No url", ""),
        {"title": "Nvidia but no date", "url": "https://x.com/4", "publishedAt": None},
        {"url": "https://x.com/5"},
    ]

    articles = transform_articles(raw, ALIASES)

    assert [a.url for a in articles] == ["https://x.com/1", "https://x.com/2"]
    first, second = articles
    assert first.symbols == {"NVDA"}
    assert first.source == "Reuters"
    assert first.published_at == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
    assert first.sentiment_label == "positive"
    assert second.symbols == {"AAPL", "INTC"}  # matched through the description


def test_transform_handles_empty_input() -> None:
    assert transform_articles([], ALIASES) == []
