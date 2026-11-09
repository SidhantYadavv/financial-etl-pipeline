from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from tools import default_registry, web
from tools.web import FakeWebSearch, WebResult

RESULTS = [
    WebResult(
        title=f"Result {i}",
        url=f"https://example.test/{i}",
        snippet=f"Snippet {i}",
        published_at=datetime(2026, 11, 1, tzinfo=UTC),
        source="Example",
    )
    for i in range(1, 8)
]


@pytest.fixture
def fake() -> Iterator[FakeWebSearch]:
    provider = FakeWebSearch(RESULTS)
    web.set_provider(provider)
    yield provider
    web.set_provider(None)


def test_unconfigured_search_refuses_instead_of_inventing_results() -> None:
    result = default_registry().call("web_search", {"query": "nvidia"})
    assert not result["ok"]
    assert "not configured" in result["error"]


def test_fake_provider_results_are_citable(fake: FakeWebSearch) -> None:
    result = default_registry().call("web_search", {"query": "nvidia export", "limit": 3})

    assert result["ok"], result
    assert fake.queries == ["nvidia export"]
    assert [r["id"] for r in result["data"]["results"]] == ["web-1", "web-2", "web-3"]
    assert result["sources"][0] == {
        "id": "web-1",
        "title": "Result 1",
        "source": "Example",
        "published_at": "2026-11-01T00:00:00+00:00",
        "url": "https://example.test/1",
    }
    assert result["as_of"] == datetime.now(UTC).date().isoformat()
    assert date.fromisoformat(result["as_of"])


def test_empty_web_results(fake: FakeWebSearch) -> None:
    fake.results = []
    result = default_registry().call("web_search", {"query": "nothing matches"})
    assert not result["ok"]
    assert "No web results" in result["error"]


@pytest.mark.parametrize("args", [{"query": "x"}, {"query": "ok query", "limit": 11}, {}])
def test_web_search_bad_arguments(args: dict) -> None:
    assert not default_registry().call("web_search", args)["ok"]
