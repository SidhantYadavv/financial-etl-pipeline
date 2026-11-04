"""Initial schema: tickers, articles, article_tickers, sentiment, daily_prices.

Revision ID: 0001
Revises:
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickers",
        sa.Column("symbol", sa.String(10), primary_key=True),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_articles_published_at", "articles", ["published_at"])
    op.create_table(
        "article_tickers",
        sa.Column(
            "article_id",
            sa.BigInteger(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(10), sa.ForeignKey("tickers.symbol"), primary_key=True),
    )
    op.create_index("ix_article_tickers_symbol", "article_tickers", ["symbol", "article_id"])
    op.create_table(
        "sentiment",
        sa.Column(
            "article_id",
            sa.BigInteger(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.Text(), primary_key=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
    )
    op.create_table(
        "daily_prices",
        sa.Column("symbol", sa.String(10), sa.ForeignKey("tickers.symbol"), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_prices")
    op.drop_table("sentiment")
    op.drop_index("ix_article_tickers_symbol", table_name="article_tickers")
    op.drop_table("article_tickers")
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_table("articles")
    op.drop_table("tickers")
