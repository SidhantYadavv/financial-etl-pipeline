"""web_search: provider interface only. A real provider is plugged in later.

Until one is configured the tool returns ok: false, so the agent can never cite
made-up results as if they were real.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from tools.registry import Tool, ToolError, ToolResult

MAX_RESULTS = 10


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str
    published_at: datetime | None = None
    source: str | None = None


class WebSearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[WebResult]: ...


class FakeWebSearch:
    """Deterministic provider for tests. Never configure it for real use."""

    def __init__(self, results: list[WebResult] | None = None) -> None:
        self.results = results or []
        self.queries: list[str] = []

    def search(self, query: str, limit: int) -> list[WebResult]:
        self.queries.append(query)
        return self.results[:limit]


_provider: WebSearchProvider | None = None


def set_provider(provider: WebSearchProvider | None) -> None:
    global _provider
    _provider = provider


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=300, description="What to search the web for.")
    limit: int = Field(default=5, ge=1, le=MAX_RESULTS, description="Max results to return.")


def web_search(params: WebSearchInput) -> ToolResult:
    if _provider is None:
        raise ToolError(
            "Web search is not configured yet. Answer from the news and price tools, "
            "and say that live web results were not available."
        )
    results = _provider.search(params.query, params.limit)[: params.limit]
    if not results:
        raise ToolError(f"No web results for '{params.query}'. Try a broader query.")
    sources = [
        {
            "id": f"web-{i}",
            "title": r.title,
            "source": r.source,
            "published_at": r.published_at,
            "url": r.url,
        }
        for i, r in enumerate(results, start=1)
    ]
    return ToolResult(
        data={
            "query": params.query,
            "results": [
                {"id": s["id"], "title": r.title, "snippet": r.snippet, "source": r.source}
                for s, r in zip(sources, results, strict=True)
            ],
        },
        as_of=datetime.now(UTC).date(),
        sources=sources,
    )


WEB_SEARCH = Tool(
    name="web_search",
    description=(
        "Search the live web for context the local news database does not have, such as "
        "events from today or background on a company. Returns titles, snippets and URLs "
        "with ids you can cite. Prefer search_news for anything about tracked tickers in "
        "the database's date range. May return an error if web search is not configured."
    ),
    input_model=WebSearchInput,
    fn=web_search,
)
