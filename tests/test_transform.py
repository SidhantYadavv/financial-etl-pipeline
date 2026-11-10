from datetime import UTC, datetime

import pytest

from pipeline.transform import (
    compile_alias_patterns,
    is_blocked_source,
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
    assert match_tickers(text, None, PATTERNS) == expected


@pytest.mark.parametrize("text", ["NVIDIA rallies", "nvidia rallies", "nViDiA rallies"])
def test_matching_is_case_insensitive(text: str) -> None:
    assert match_tickers(text, None, PATTERNS) == {"NVDA"}


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
    assert match_tickers(text, None, PATTERNS) == set()


def test_empty_aliases_are_ignored() -> None:
    assert compile_alias_patterns({"XYZ": ["", "  "]}) == {}


# The Surface review that was tagged NVDA because of "a fast Nvidia RTX graphics chip".
SURFACE_TITLE = (
    "Surface Laptop Ultra doubles Apple MacBook Pro's repairability score in iFixit teardown"
)
SURFACE_DESCRIPTION = (
    "With 20 ARM processor cores, a fast Nvidia RTX graphics chip and a Mini-LED display with "
    "a brightness of over 1,000 nits, the Microsoft Surface Laptop Ultra is an exciting "
    "alternative to the Apple MacBook Pro."
)
ALIASES_WITH_MSFT = ALIASES | {"MSFT": ["Microsoft", "MSFT"]}


def test_single_description_mention_is_not_enough() -> None:
    patterns = compile_alias_patterns(ALIASES_WITH_MSFT)
    # Apple is in the title; Microsoft is named once in the description, Nvidia once too.
    assert match_tickers(SURFACE_TITLE, SURFACE_DESCRIPTION, patterns) == {"AAPL"}


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Nvidia said demand was strong. Nvidia also raised guidance.", {"NVDA"}),
        ("Nvidia (NVDA) rose 3%.", {"NVDA"}),  # name and symbol count together
        ("Nvidia rose 3%.", set()),
        (None, set()),
    ],
)
def test_description_needs_two_mentions(description: str | None, expected: set[str]) -> None:
    assert match_tickers("Chip stocks rally", description, PATTERNS) == expected


@pytest.mark.parametrize(
    ("source", "url", "blocked"),
    [
        ("Slickdeals.net", "https://slickdeals.net/f/19412345", True),
        ("slickdeals.net", "https://example.com/x", True),
        (None, "https://www.dealnews.com/AMD-Ryzen/123.html", True),
        ("9to5Toys", "https://9to5toys.com/2026/11/01/deal", True),
        ("Pypi.org", "https://pypi.org/project/mlx-optiq/0.7.10/", True),
        ("Reuters", "https://www.reuters.com/technology/nvidia", False),
        ("Biztoc.com", "https://biztoc.com/x/abc", False),
        ("Notslickdeals", "https://notslickdeals.net/x", False),
    ],
)
def test_blocked_sources(source: str | None, url: str, blocked: bool) -> None:
    assert is_blocked_source(source, url) is blocked


def test_deal_listings_are_dropped_even_with_the_ticker_in_the_title() -> None:
    raw = [
        news_article(
            "AMD Ryzen 5 9600X 6-Core, 12-Thread Unlocked Desktop Processor $178",
            "https://slickdeals.net/f/19412345",
            "DealsPro1 via Amazon has AMD Ryzen 5 9600X for $178",
            source="Slickdeals.net",
        ),
        news_article(SURFACE_TITLE, "https://notebookcheck.net/1", SURFACE_DESCRIPTION),
    ]

    articles = transform_articles(raw, ALIASES_WITH_MSFT | {"AMD": ["AMD"]})

    assert [(a.url, a.symbols) for a in articles] == [
        ("https://notebookcheck.net/1", frozenset({"AAPL"}))
    ]


CEOS = {"NVDA": ["Jensen Huang"], "JPM": ["Jamie Dimon"], "AMD": ["Lisa Su"]}
CEO_PATTERNS = compile_alias_patterns(CEOS)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Jamie Dimon ignores the noise", {"JPM"}),
        ("Jensen Huang's keynote: what to expect", {"NVDA"}),
        ("AMD’s Lisa Su says the market is short-sighted about AI", {"AMD"}),
        ("Lisa Sutton joins the board of a regional bank", set()),  # whole words only
        ("Jensen Huang and Lisa Su share a stage", {"NVDA", "AMD"}),
    ],
)
def test_ceo_names_match_in_titles(title: str, expected: set[str]) -> None:
    assert match_tickers(title, None, {}, CEO_PATTERNS) == expected


def test_ceo_names_never_count_in_descriptions() -> None:
    description = "Jensen Huang spoke first. Later, Jensen Huang took questions."
    assert match_tickers("Conference roundup", description, {}, CEO_PATTERNS) == set()


def test_transform_links_ceo_headlines() -> None:
    raw = [
        news_article("Jamie Dimon ignores the noise", "https://x/1", "JPMorgan Chase CEO says"),
        news_article("Bank outlook", "https://x/2", "Jamie Dimon said rates... Jamie Dimon"),
    ]
    articles = transform_articles(raw, ALIASES, CEOS)
    assert [(a.url, a.symbols) for a in articles] == [("https://x/1", frozenset({"JPM"}))]


def test_sentiment_labels() -> None:
    assert score_sentiment("Profits soar to a record, a great win")[1] == "positive"
    assert score_sentiment("Shares crash amid fraud scandal and losses")[1] == "negative"
    assert score_sentiment("The company held a meeting on Tuesday")[1] == "neutral"


def test_transform_cleans_dedupes_and_links() -> None:
    raw = [
        news_article("Nvidia hits record high", "https://x.com/1", "Strong demand for chips"),
        news_article("Nvidia hits record high", "https://x.com/1"),  # same url
        news_article(
            "Markets wrap", "https://x.com/2", "Apple fell, Intel fell, then Apple and Intel..."
        ),
        news_article("Bond market notes", "https://x.com/6", "Apple was mentioned once"),
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
    assert second.symbols == {"AAPL", "INTC"}  # each named twice in the description


def test_transform_handles_empty_input() -> None:
    assert transform_articles([], ALIASES) == []
