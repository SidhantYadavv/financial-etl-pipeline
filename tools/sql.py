"""run_sql: read-only SQL over the data tables, with layered defenses.

1. Parse with Postgres' own grammar (pglast) and accept exactly one SELECT that only
   reads allowlisted tables and calls allowlisted functions. System catalogs are
   readable by every Postgres role, so this allowlist is what keeps them out.
2. Run the re-generated SQL (never the raw input) wrapped in an outer LIMIT.
3. Connect as `agent_readonly` (SELECT on the data tables only), in a READ ONLY
   transaction with a 3s statement timeout, on a fresh connection that is then
   discarded so nothing a query does to session state outlives the call.
"""

from typing import Any

from pglast import ast, parse_sql
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Visitor
from psycopg import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field

from db.session import READONLY_STATEMENT_TIMEOUT_MS, get_readonly_engine, readonly_database_url
from tools.common import engine, news_date_range, price_date_range
from tools.registry import Tool, ToolError, ToolResult

MAX_ROWS = 200
MAX_QUERY_CHARS = 5000
ALLOWED_TABLES = frozenset(
    {"tickers", "articles", "article_tickers", "sentiment", "daily_prices", "fetch_log"}
)
ALLOWED_FUNCTIONS = frozenset(
    {
        # aggregates
        "count", "sum", "avg", "min", "max", "stddev", "stddev_samp", "stddev_pop",
        "variance", "var_samp", "var_pop", "corr", "covar_pop", "covar_samp",
        "regr_slope", "regr_intercept", "regr_r2", "percentile_cont", "percentile_disc",
        "mode", "bool_and", "bool_or", "every", "string_agg", "array_agg",
        # window functions
        "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile",
        "lag", "lead", "first_value", "last_value", "nth_value",
        # math
        "abs", "round", "ceil", "ceiling", "floor", "trunc", "sqrt", "cbrt", "power",
        "exp", "ln", "log", "log10", "sign", "mod", "width_bucket",
        # dates and times
        "date_trunc", "date_part", "extract", "age", "now", "make_date", "to_char",
        "to_date", "to_timestamp", "timezone", "date_bin", "generate_series", "isfinite",
        # text
        "lower", "upper", "initcap", "length", "char_length", "substring", "substr",
        "left", "right", "trim", "btrim", "ltrim", "rtrim", "replace", "concat",
        "concat_ws", "split_part", "strpos", "position", "starts_with", "regexp_replace",
        "regexp_match", "regexp_like", "regexp_count", "format",
        # arrays
        "array_length", "cardinality", "unnest", "array_to_string", "array_position",
    }
)  # fmt: skip
WRITE_NODES = (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.MergeStmt)


class _Checker(Visitor):
    """Collects every reason a parsed SELECT is not allowed."""

    def __init__(self, cte_names: set[str]) -> None:
        super().__init__()
        self.cte_names = cte_names
        self.problems: list[str] = []

    def visit(self, ancestors: Any, node: Any) -> None:
        if isinstance(node, WRITE_NODES):
            kind = type(node).__name__.removesuffix("Stmt").upper()
            self.problems.append(f"{kind} is not allowed; run_sql is read-only")

    def visit_SelectStmt(self, ancestors: Any, node: ast.SelectStmt) -> None:
        if node.intoClause is not None:
            self.problems.append("SELECT ... INTO is not allowed")
        if node.lockingClause:
            self.problems.append("FOR UPDATE/SHARE locking clauses are not allowed")

    def visit_RangeVar(self, ancestors: Any, node: ast.RangeVar) -> None:
        name = node.relname
        if node.schemaname not in (None, "public"):
            self.problems.append(f"table {node.schemaname}.{name} is not allowed")
        elif name not in ALLOWED_TABLES and (node.schemaname or name not in self.cte_names):
            self.problems.append(f"table {name} is not allowed")

    def visit_FuncCall(self, ancestors: Any, node: ast.FuncCall) -> None:
        parts = [p.sval for p in node.funcname]
        name = parts[-1].lower()
        if (len(parts) > 1 and parts[0] != "pg_catalog") or name not in ALLOWED_FUNCTIONS:
            self.problems.append(f"function {'.'.join(parts)}() is not allowed")


def validate_select(sql: str) -> str:
    """Return normalized SQL for one allowed SELECT, or raise ToolError explaining why not."""
    try:
        statements = parse_sql(sql)
    except ParseError as exc:
        raise ToolError(f"SQL syntax error: {exc}") from exc
    if len(statements) != 1:
        raise ToolError(f"Send exactly one SQL statement (got {len(statements)}).")
    statement = statements[0].stmt
    if not isinstance(statement, ast.SelectStmt):
        kind = type(statement).__name__.removesuffix("Stmt").upper()
        raise ToolError(f"Only SELECT queries are allowed (got {kind}).")

    cte_names: set[str] = set()

    class _CteCollector(Visitor):
        def visit_CommonTableExpr(self, ancestors: Any, node: ast.CommonTableExpr) -> None:
            cte_names.add(node.ctename)

    _CteCollector()(statement)
    checker = _Checker(cte_names)
    checker(statement)
    if checker.problems:
        problems = "; ".join(dict.fromkeys(checker.problems))
        raise ToolError(
            f"Query rejected: {problems}. Allowed tables: {', '.join(sorted(ALLOWED_TABLES))}. "
            "Only SELECT with common aggregate, window, math, date and text functions is allowed."
        )
    return RawStream()(statement)


def execute_readonly(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run already-validated SQL as agent_readonly on a fresh, read-only connection."""
    wrapped = f"SELECT * FROM ({sql}) AS agent_query LIMIT {MAX_ROWS + 1}"
    readonly = get_readonly_engine(readonly_database_url(), pooled=False)
    raw = readonly.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(wrapped)
        columns = [c.name for c in cursor.description]
        return columns, cursor.fetchall()
    except pg_errors.QueryCanceled as exc:
        seconds = READONLY_STATEMENT_TIMEOUT_MS // 1000
        raise ToolError(
            f"Query cancelled after the {seconds}s time limit. Filter by symbol or date, "
            "aggregate, or add a LIMIT."
        ) from exc
    except pg_errors.InsufficientPrivilege as exc:
        raise ToolError(f"Permission denied: {_first_line(exc)}") from exc
    except pg_errors.ReadOnlySqlTransaction as exc:
        raise ToolError("run_sql is read-only; the query tried to write.") from exc
    except pg_errors.Error as exc:
        raise ToolError(f"Query failed: {_first_line(exc)}") from exc
    finally:
        raw.rollback()
        raw.close()


class RunSqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=MAX_QUERY_CHARS,
        description="One PostgreSQL SELECT (or WITH ... SELECT) statement.",
    )


def run_sql(params: RunSqlInput) -> ToolResult:
    sql = validate_select(params.query)
    columns, rows = execute_readonly(sql)
    with engine().connect() as conn:
        as_of = max(news_date_range(conn)[1], price_date_range(conn)[1])
    return ToolResult(
        data={
            "columns": columns,
            "rows": [list(r) for r in rows[:MAX_ROWS]],
            "row_count": min(len(rows), MAX_ROWS),
            "truncated": len(rows) > MAX_ROWS,
            "max_rows": MAX_ROWS,
        },
        as_of=as_of,
    )


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0]


RUN_SQL = Tool(
    name="run_sql",
    description=(
        "Run one read-only PostgreSQL SELECT for questions the other tools cannot answer. "
        "Tables: tickers(symbol, company_name, aliases text[]); "
        "articles(id, url, title, description, source, published_at timestamptz, fetched_at); "
        "article_tickers(article_id, symbol); "
        "sentiment(article_id, model, score float -1..1, label); "
        "daily_prices(symbol, date, open, high, low, close, volume); "
        "fetch_log(symbol, day, status complete|capped|provisional, articles_returned): "
        "one row per ticker and New York day of news fetched, no row = not fetched. "
        "Join articles to tickers through article_tickers. Only these tables and common "
        "aggregate/window/math/date/text functions are allowed; no writes, no system tables. "
        f"Results are capped at {MAX_ROWS} rows (truncated=true if there were more) and "
        f"queries stop after {READONLY_STATEMENT_TIMEOUT_MS // 1000}s. Select article id, "
        "title and url if you want to cite articles; prefer search_news for citations."
    ),
    input_model=RunSqlInput,
    fn=run_sql,
)
