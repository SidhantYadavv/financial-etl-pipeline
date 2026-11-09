"""fetch_log: one row per (ticker, New York day) news window fetched from NewsAPI.

`status` is derived from the stored flags so the pipeline and the tools agree on it:
provisional (fetched before NewsAPI's delay had passed, so the day may be incomplete),
capped (the API's 100-article page was full, so the day is a sample) or complete.
Days with no row have not been fetched with a one-day window yet.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = "CASE WHEN NOT settled THEN 'provisional' WHEN capped THEN 'capped' ELSE 'complete' END"


def upgrade() -> None:
    op.create_table(
        "fetch_log",
        sa.Column("symbol", sa.String(10), sa.ForeignKey("tickers.symbol"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("articles_returned", sa.Integer(), nullable=False),
        sa.Column("total_results", sa.Integer(), nullable=False),
        sa.Column("capped", sa.Boolean(), nullable=False),
        sa.Column("settled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), sa.Computed(STATUS, persisted=True), nullable=False),
    )
    # Tools report coverage from this table, so the read-only agent role may read it.
    op.execute("GRANT SELECT ON fetch_log TO agent_readonly")


def downgrade() -> None:
    op.drop_table("fetch_log")
