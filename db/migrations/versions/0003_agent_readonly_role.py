"""Read-only Postgres role for agent tools.

Roles are cluster-wide, so the dev and test databases share this role. The password
comes from AGENT_READONLY_PASSWORD and is never stored in the repository.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09

"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "agent_readonly"
DATA_TABLES = "tickers, articles, article_tickers, sentiment, daily_prices"


def upgrade() -> None:
    password = os.getenv("AGENT_READONLY_PASSWORD")
    if not password:
        raise RuntimeError("AGENT_READONLY_PASSWORD must be set to create the agent role.")
    quoted_password = password.replace("'", "''")

    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE};
            END IF;
        END $$
        """
    )
    op.execute(
        f"ALTER ROLE {ROLE} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
        f"NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20 PASSWORD '{quoted_password}'"
    )
    op.execute(f"ALTER ROLE {ROLE} SET default_transaction_read_only = on")
    op.execute(f"ALTER ROLE {ROLE} SET statement_timeout = '3s'")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON {DATA_TABLES} TO {ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON {DATA_TABLES} FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    # Another database in the cluster may still grant to this role; keep it if so.
    op.execute(
        f"""
        DO $$ BEGIN
            DROP ROLE IF EXISTS {ROLE};
        EXCEPTION WHEN dependent_objects_still_exist THEN
            RAISE NOTICE 'Role {ROLE} is still used by another database; not dropped';
        END $$
        """
    )
