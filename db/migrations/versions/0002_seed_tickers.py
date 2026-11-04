"""Seed the initial ticker universe.

Aliases are matched case-insensitively on whole words, so ambiguous bare symbols
(KO, DIS, GS, BAC, META) are left out in favour of company names.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TICKERS = [
    ("AAPL", "Apple Inc.", ["Apple", "AAPL"]),
    ("MSFT", "Microsoft Corporation", ["Microsoft", "MSFT"]),
    ("NVDA", "NVIDIA Corporation", ["Nvidia", "NVDA"]),
    ("AMD", "Advanced Micro Devices, Inc.", ["Advanced Micro Devices", "AMD"]),
    ("INTC", "Intel Corporation", ["Intel", "INTC"]),
    ("TSLA", "Tesla, Inc.", ["Tesla", "TSLA"]),
    ("AMZN", "Amazon.com, Inc.", ["Amazon", "AMZN"]),
    ("GOOGL", "Alphabet Inc.", ["Alphabet", "Google", "GOOGL", "GOOG"]),
    ("META", "Meta Platforms, Inc.", ["Meta Platforms", "Meta", "Facebook"]),
    ("NFLX", "Netflix, Inc.", ["Netflix", "NFLX"]),
    ("JPM", "JPMorgan Chase & Co.", ["JPMorgan", "JP Morgan", "J.P. Morgan", "JPM"]),
    ("BAC", "Bank of America Corporation", ["Bank of America", "BofA"]),
    ("GS", "The Goldman Sachs Group, Inc.", ["Goldman Sachs", "Goldman"]),
    ("XOM", "Exxon Mobil Corporation", ["Exxon", "ExxonMobil", "Exxon Mobil", "XOM"]),
    ("CVX", "Chevron Corporation", ["Chevron", "CVX"]),
    ("JNJ", "Johnson & Johnson", ["Johnson & Johnson", "J&J", "JNJ"]),
    ("PFE", "Pfizer Inc.", ["Pfizer", "PFE"]),
    ("WMT", "Walmart Inc.", ["Walmart", "Wal-Mart", "WMT"]),
    ("KO", "The Coca-Cola Company", ["Coca-Cola", "Coca Cola"]),
    ("DIS", "The Walt Disney Company", ["Disney"]),
]

tickers = sa.table(
    "tickers",
    sa.column("symbol", sa.String),
    sa.column("company_name", sa.Text),
    sa.column("aliases", postgresql.ARRAY(sa.Text)),
)


def upgrade() -> None:
    op.bulk_insert(
        tickers,
        [{"symbol": s, "company_name": n, "aliases": a} for s, n, a in TICKERS],
    )


def downgrade() -> None:
    op.execute(tickers.delete().where(tickers.c.symbol.in_([s for s, _, _ in TICKERS])))
