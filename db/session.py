"""Engine creation from DATABASE_URL."""

import os
from functools import cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.pool import NullPool

READONLY_ROLE = "agent_readonly"
READONLY_STATEMENT_TIMEOUT_MS = 3000
# Applied on every connection, on top of the same settings stored on the role itself.
READONLY_OPTIONS = (
    f"-c default_transaction_read_only=on -c statement_timeout={READONLY_STATEMENT_TIMEOUT_MS}"
)


def database_url() -> str:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env.")
    return url


def readonly_database_url() -> str:
    """DATABASE_URL with the credentials swapped for the read-only agent role."""
    load_dotenv()
    password = os.getenv("AGENT_READONLY_PASSWORD")
    if not password:
        raise RuntimeError("AGENT_READONLY_PASSWORD is not set. Copy .env.example to .env.")
    url = make_url(database_url()).set(username=READONLY_ROLE, password=password)
    return url.render_as_string(hide_password=False)


@cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)


@cache
def get_readonly_engine(url: str, pooled: bool = True) -> Engine:
    """Engine for agent tools. Unpooled engines give each call a fresh session."""
    pool_args = {"pool_pre_ping": True} if pooled else {"poolclass": NullPool}
    return create_engine(url, connect_args={"options": READONLY_OPTIONS}, **pool_args)
