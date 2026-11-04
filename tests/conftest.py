"""Shared fixtures. DB tests run against a real Postgres database (TEST_DATABASE_URL)."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_URL = "postgresql+psycopg://etl:etl@localhost:5434/financial_news_test"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    load_dotenv(ROOT / ".env")
    return os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_URL)


@pytest.fixture(scope="session")
def migrated_engine(test_database_url: str) -> Iterator[Engine]:
    """Build the schema from scratch with the real migrations, once per session."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", test_database_url)
    config.attributes["configure_logger"] = False
    engine = create_engine(test_database_url)
    # Start from an empty schema even if an interrupted run left data behind.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(migrated_engine: Engine) -> Engine:
    """Empty data tables (seeded tickers are kept) before each test."""
    with migrated_engine.begin() as conn:
        conn.execute(
            text("TRUNCATE articles, article_tickers, sentiment, daily_prices RESTART IDENTITY")
        )
    return migrated_engine


def count_rows(engine: Engine) -> dict[str, int]:
    tables = ["tickers", "articles", "article_tickers", "sentiment", "daily_prices"]
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in tables}


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def news_article(
    title: str,
    url: str,
    description: str | None = None,
    published_at: str = "2026-11-02T14:30:00Z",
    source: str = "Reuters",
) -> dict[str, Any]:
    return {
        "source": {"id": None, "name": source},
        "author": "Reporter",
        "title": title,
        "description": description,
        "url": url,
        "publishedAt": published_at,
        "content": None,
    }
