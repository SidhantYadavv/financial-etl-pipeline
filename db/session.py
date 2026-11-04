"""Engine creation from DATABASE_URL."""

import os
from functools import cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine


def database_url() -> str:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env.")
    return url


@cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)
