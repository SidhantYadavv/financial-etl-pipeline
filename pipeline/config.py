"""Runtime settings read from the environment (and .env)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from db.session import database_url

DEFAULT_REQUEST_BUDGET = 80


@dataclass(frozen=True)
class Settings:
    news_api_key: str
    database_url: str
    request_budget: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        api_key = os.getenv("NEWS_API_KEY")
        if not api_key:
            raise RuntimeError("NEWS_API_KEY is not set. Copy .env.example to .env.")
        budget = int(os.getenv("NEWS_API_REQUEST_BUDGET", DEFAULT_REQUEST_BUDGET))
        return cls(news_api_key=api_key, database_url=database_url(), request_budget=budget)
