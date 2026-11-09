"""run_sql safety. Each layer is tested on its own, and the database is checked after each."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import Engine, text

from db.session import get_readonly_engine, readonly_database_url
from tests.conftest import count_rows
from tools.registry import ToolError, ToolRegistry
from tools.sql import MAX_ROWS, execute_readonly, validate_select

pytestmark = pytest.mark.usefixtures("tools_db")


def snapshot(engine: Engine) -> dict[str, Any]:
    """Everything an attack could change: row counts, content, tables and the role."""
    with engine.connect() as conn:
        return {
            "counts": count_rows(engine),
            "titles": conn.execute(
                text("SELECT md5(string_agg(title, '|' ORDER BY id)) FROM articles")
            ).scalar_one(),
            "tables": conn.execute(
                text("SELECT array_agg(tablename ORDER BY tablename) FROM pg_tables "
                     "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")
            ).scalar_one(),
            "role": conn.execute(
                text("SELECT row(rolsuper, rolcreaterole, rolcreatedb) FROM pg_roles "
                     "WHERE rolname = 'agent_readonly'")
            ).scalar_one(),
        }  # fmt: skip


@pytest.fixture
def unchanged(tools_db: Engine) -> Iterator[None]:
    before = snapshot(tools_db)
    yield
    assert snapshot(tools_db) == before


def test_select_returns_rows(registry: ToolRegistry) -> None:
    result = registry.call(
        "run_sql",
        {"query": "SELECT symbol, count(*) AS n FROM article_tickers GROUP BY 1 ORDER BY 1"},
    )

    assert result["ok"], result
    assert result["data"]["columns"] == ["symbol", "n"]
    assert result["data"]["rows"] == [["AAPL", 31], ["AMD", 5], ["NVDA", 32]]
    assert result["data"]["truncated"] is False
    assert result["as_of"] == "2026-06-30"


def test_fetch_log_is_readable(registry: ToolRegistry) -> None:
    result = registry.call(
        "run_sql",
        {"query": "SELECT status, count(*) FROM fetch_log WHERE symbol = 'NVDA' GROUP BY 1 "
                  "ORDER BY 1"},
    )  # fmt: skip
    assert result["data"]["rows"] == [["capped", 1], ["complete", 27], ["provisional", 1]]


def test_realistic_agent_query_is_allowed(registry: ToolRegistry) -> None:
    query = """
        WITH daily AS (
            SELECT (a.published_at AT TIME ZONE 'America/New_York')::date AS day,
                   round(avg(s.score)::numeric, 3) AS avg_score, count(*) AS n
            FROM articles a
            JOIN article_tickers t ON t.article_id = a.id
            JOIN sentiment s ON s.article_id = a.id
            WHERE t.symbol = 'NVDA' AND a.title ILIKE '%nvidia%'
            GROUP BY 1
        )
        SELECT day, avg_score, n, extract(dow FROM day) AS weekday,
               lag(avg_score) OVER (ORDER BY day) AS prev
        FROM daily ORDER BY day DESC LIMIT 3
    """
    result = registry.call("run_sql", {"query": query})
    assert result["ok"], result
    assert result["data"]["rows"][0][0] == "2026-06-30"


def test_row_cap_and_truncation(registry: ToolRegistry) -> None:
    result = registry.call("run_sql", {"query": "SELECT g FROM generate_series(1, 500) AS g"})
    assert result["data"]["row_count"] == MAX_ROWS
    assert result["data"]["truncated"] is True
    assert result["data"]["rows"][-1] == [MAX_ROWS]


def test_query_with_its_own_limit_is_not_truncated(registry: ToolRegistry) -> None:
    result = registry.call("run_sql", {"query": "SELECT id FROM articles ORDER BY id LIMIT 5;"})
    assert result["data"]["row_count"] == 5
    assert result["data"]["truncated"] is False


def test_trailing_comment_cannot_break_out_of_the_wrapper(registry: ToolRegistry) -> None:
    query = "SELECT id FROM articles -- ) AS q; SELECT * FROM pg_roles"
    result = registry.call("run_sql", {"query": query})
    assert result["ok"], result
    assert result["data"]["truncated"] is False


REJECTED = [
    ("DROP TABLE articles", "Only SELECT"),
    ("DELETE FROM articles", "Only SELECT"),
    ("UPDATE articles SET title = 'pwned'", "Only SELECT"),
    ("INSERT INTO tickers VALUES ('EVIL', 'Evil', '{}')", "Only SELECT"),
    ("TRUNCATE articles", "Only SELECT"),
    ("CREATE TABLE stolen (id int)", "Only SELECT"),
    ("GRANT ALL ON articles TO PUBLIC", "Only SELECT"),
    ("SET ROLE etl", "Only SELECT"),
    ("COPY articles TO STDOUT", "Only SELECT"),
    ("EXPLAIN ANALYZE DELETE FROM articles", "Only SELECT"),
    ("SELECT 1; DROP TABLE articles", "exactly one"),
    ("SELECT 1; SELECT 2", "exactly one"),
    ("WITH gone AS (DELETE FROM articles RETURNING id) SELECT * FROM gone", "DELETE"),
    ("WITH x AS (UPDATE tickers SET company_name = '' RETURNING 1) SELECT 1", "UPDATE"),
    ("SELECT * INTO stolen FROM articles", "INTO"),
    ("SELECT * FROM articles FOR UPDATE", "FOR UPDATE"),
    ("SELECT pg_sleep(10)", "pg_sleep"),
    ("SELECT * FROM pg_catalog.pg_roles", "pg_catalog.pg_roles"),
    ("SELECT * FROM pg_roles", "pg_roles"),
    ("SELECT usename, passwd FROM pg_shadow", "pg_shadow"),
    ("SELECT * FROM information_schema.tables", "information_schema"),
    ("SELECT * FROM alembic_version", "alembic_version"),
    ("SELECT * FROM public.alembic_version", "alembic_version"),
    ("WITH pg_roles AS (SELECT 1) SELECT * FROM pg_catalog.pg_roles", "pg_catalog"),
    ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
    ("SELECT query_to_xml('SELECT * FROM pg_roles', true, true, '')", "query_to_xml"),
    ("SELECT current_setting('data_directory')", "current_setting"),
    ("SELECT nextval('articles_id_seq')", "nextval"),
    ("SELECT lo_import('/etc/passwd')", "lo_import"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT public.lower('x')", "public.lower"),
    ("SELEC * FROM articles", "syntax error"),
]


@pytest.mark.usefixtures("unchanged")
@pytest.mark.parametrize(("query", "reason"), REJECTED)
def test_unsafe_queries_are_rejected(registry: ToolRegistry, query: str, reason: str) -> None:
    result = registry.call("run_sql", {"query": query})
    assert not result["ok"], result
    assert reason in result["error"]


def test_rejected_query_never_reaches_the_database() -> None:
    with pytest.raises(ToolError):
        validate_select("SELECT 1; DROP TABLE articles")


def test_normalized_sql_drops_comments() -> None:
    assert validate_select("SELECT id FROM articles -- trailing") == "SELECT id FROM articles"


@pytest.mark.usefixtures("unchanged")
def test_slow_query_is_cancelled_by_the_timeout(registry: ToolRegistry) -> None:
    started = time.monotonic()
    result = registry.call(
        "run_sql", {"query": "SELECT count(*) FROM generate_series(1, 2000000000)"}
    )
    elapsed = time.monotonic() - started

    assert not result["ok"]
    assert "cancelled after the 3s time limit" in result["error"]
    assert elapsed < 6


def test_pg_sleep_is_cancelled_even_if_the_parser_were_bypassed() -> None:
    started = time.monotonic()
    with pytest.raises(ToolError, match="cancelled"):
        execute_readonly("SELECT pg_sleep(10)")
    assert time.monotonic() - started < 6


@pytest.fixture
def raw_readonly() -> Iterator[Any]:
    """A raw psycopg connection as agent_readonly: no parser, no wrapper."""
    raw = get_readonly_engine(readonly_database_url(), pooled=False).raw_connection()
    yield raw
    raw.rollback()
    raw.close()


@pytest.mark.usefixtures("unchanged")
@pytest.mark.parametrize(
    ("statement", "error"),
    [
        ("DELETE FROM articles", pg_errors.ReadOnlySqlTransaction),
        ("INSERT INTO tickers VALUES ('EVIL', 'Evil', '{}')", pg_errors.ReadOnlySqlTransaction),
        ("CREATE TEMP TABLE stolen (id int)", pg_errors.ReadOnlySqlTransaction),
        ("DROP TABLE articles", pg_errors.ReadOnlySqlTransaction),
        ("SELECT * FROM alembic_version", pg_errors.InsufficientPrivilege),
        ("SET ROLE etl", pg_errors.InsufficientPrivilege),
        ("SELECT set_config('role', 'etl', false)", pg_errors.InsufficientPrivilege),
    ],
)
def test_role_and_read_only_transaction_block_writes(
    raw_readonly: Any, statement: str, error: type[Exception]
) -> None:
    with pytest.raises(error):
        raw_readonly.cursor().execute(statement)


@pytest.mark.usefixtures("unchanged")
@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM articles",
        "UPDATE tickers SET company_name = 'x'",
        "INSERT INTO daily_prices VALUES ('NVDA', '2030-01-01', 1, 1, 1, 1, 1)",
        "DELETE FROM fetch_log",
        "TRUNCATE articles",
        "DROP TABLE articles",
    ],
)
def test_grants_block_writes_even_in_a_read_write_transaction(
    raw_readonly: Any, statement: str
) -> None:
    cursor = raw_readonly.cursor()
    cursor.execute("SET TRANSACTION READ WRITE")  # simulate the read-only layer failing
    with pytest.raises((pg_errors.InsufficientPrivilege, pg_errors.WrongObjectType)):
        cursor.execute(statement)


def test_connection_settings(raw_readonly: Any) -> None:
    cursor = raw_readonly.cursor()
    cursor.execute(
        "SELECT current_user, current_setting('transaction_read_only'), "
        "current_setting('statement_timeout')"
    )
    assert cursor.fetchone() == ("agent_readonly", "on", "3s")
