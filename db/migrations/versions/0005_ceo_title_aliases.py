"""Title-only aliases: CEO names that link a headline to a ticker.

A headline like "Jamie Dimon ignores the noise" is about JPMorgan even though the
company is not named. These names only count in the title, and only names that
almost always mean that company are used. Left out as ambiguous: Elon Musk (TSLA;
most of his headlines are about SpaceX, X, xAI or politics), David Solomon (GS),
Mike Wirth (CVX) and Greg Peters (NFLX), which are common names. Recent former
CEOs who stay on as chair (Tim Cook, James Quincey, Bob Iger) are kept, since
headlines naming them are still about the company. Names current as of
November 2026.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TITLE_ALIASES = {
    "AAPL": ["John Ternus", "Tim Cook"],
    "MSFT": ["Satya Nadella"],
    "NVDA": ["Jensen Huang"],
    "AMD": ["Lisa Su"],
    "INTC": ["Lip-Bu Tan"],
    "AMZN": ["Andy Jassy"],
    "GOOGL": ["Sundar Pichai"],
    "META": ["Mark Zuckerberg"],
    "NFLX": ["Ted Sarandos"],
    "JPM": ["Jamie Dimon"],
    "BAC": ["Brian Moynihan"],
    "XOM": ["Darren Woods"],
    "JNJ": ["Joaquin Duato"],
    "PFE": ["Albert Bourla"],
    "WMT": ["John Furner"],
    "KO": ["Henrique Braun", "James Quincey"],
    "DIS": ["Josh D'Amaro", "Josh D’Amaro", "Bob Iger"],
}


def upgrade() -> None:
    op.add_column(
        "tickers",
        sa.Column(
            "title_aliases", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
    )
    tickers = sa.table(
        "tickers",
        sa.column("symbol", sa.String),
        sa.column("title_aliases", postgresql.ARRAY(sa.Text)),
    )
    for symbol, names in TITLE_ALIASES.items():
        op.execute(tickers.update().where(tickers.c.symbol == symbol).values(title_aliases=names))


def downgrade() -> None:
    op.drop_column("tickers", "title_aliases")
