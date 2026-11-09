# Financial News ETL Pipeline

A production-grade Data Engineering pipeline that automates the extraction, transformation, and loading (ETL) of financial news data. The project features a modern React/Next.js dashboard to visualize market sentiment and trends.

---

## Overview

This project implements an automated ETL workflow to monitor financial markets:
1. **Extract**: Pulls news per ticker from NewsAPI and daily prices from yfinance.
2. **Transform**: Processes raw text, performs sentiment analysis, and cleans data using Python.
3. **Load**: Stores articles, ticker links, sentiment and daily prices in PostgreSQL.
4. **Visualize**: A real-time dashboard built with Next.js and Tailwind CSS.

---

## Tech Stack

- **Pipeline**: Python 3.11+, Prefect 3, NewsAPI, yfinance, NLTK VADER
- **Database**: PostgreSQL 17, SQLAlchemy 2, Alembic
- **Frontend**: Next.js, React (still reads the legacy SQLite snapshot; moves to the API in Phase 3)
- **Quality**: pytest, Ruff, GitHub Actions

---

## Project Structure

```
pipeline/   extract, transform, load and the Prefect flow
db/         SQLAlchemy models and Alembic migrations (incl. ticker seed)
tests/      offline pytest suite (mocked APIs, real Postgres)
tools/      typed agent tools, registry and developer CLI
agent/ api/ evals/   placeholders for later phases
frontend/   Next.js dashboard
```

### Schema

| Table | Key | Purpose |
|---|---|---|
| `tickers` | `symbol` | Tracked companies and the aliases used to match news |
| `articles` | `id`, unique `url` | One row per news article |
| `article_tickers` | `(article_id, symbol)` | Which tickers an article mentions |
| `sentiment` | `(article_id, model)` | One score per article per model (`vader` today) |
| `daily_prices` | `(symbol, date)` | Completed daily OHLCV bars |
| `fetch_log` | `(symbol, day)` | Which ticker/New York days of news were fetched: `complete`, `capped` or `provisional` |

Every load uses `INSERT ... ON CONFLICT DO NOTHING` inside one transaction, so re-running never duplicates rows. `fetch_log` rows are written in the same transaction as the articles they describe.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Docker

### Setup
```bash
cp .env.example .env              # then add NEWS_API_KEY and AGENT_READONLY_PASSWORD
docker compose up -d              # Postgres 17 on localhost:5434
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m nltk.downloader vader_lexicon
alembic upgrade head              # schema, ticker seed and the agent_readonly role
```

On macOS with the python.org installer, run `Install Certificates.command` from the Python folder first if the NLTK download fails with `CERTIFICATE_VERIFY_FAILED`.

### Running the pipeline
```bash
python -m pipeline run        # spend today's request budget filling news gaps, plus 7 days of prices
python -m pipeline backfill   # the same news gap-filling, plus 30 days of prices
python -m pipeline serve      # stay running and trigger the daily run at 11:00 UTC
python -m pipeline coverage   # per ticker: days with data, complete, capped, provisional, missing
python -m pipeline rematch    # re-apply the ticker matching rules to stored articles
```

Each NewsAPI request covers one ticker and one New York calendar day, because a request returns
at most 100 articles and a wider window lets one busy day crowd out the rest. With 20 tickers and
~28 days that NewsAPI's free tier can serve, a full month is ~560 requests, more than the 100 a
day the free tier allows. So each run plans its requests from `fetch_log`:

1. days never fetched, newest first;
2. provisional days, newest first. The free tier delays articles by 24 hours, so a day fetched
   less than 24 hours after it ended may be missing articles and is fetched again later.

Complete days and capped days (which hit the 100-article limit) are not fetched again, since the
same query would return the same articles. `NEWS_API_REQUEST_BUDGET` caps the requests one run
makes. A run that stops early (budget, rate limit, crash) leaves the rest for the next run. At 80
requests a day, an empty database fills in about a week.

An article is linked to a ticker only if a company alias (name or symbol) appears in its title,
or at least twice in its description. Deal and coupon sites (Slickdeals and similar) and PyPI
release notes are skipped (`BLOCKED_SOURCES` in `pipeline/transform.py`).

### Agent tools
Seven tools the research agent will call: `list_tickers`, `search_news`, `sentiment_trend`,
`price_history`, `compare_tickers`, `run_sql` and `web_search` (a stub until a provider is chosen).
Every call returns `{"ok": true, "data", "as_of", "sources"}` or `{"ok": false, "error"}`, and
date windows such as "last 7 days" end at the latest date in the database, not today.

```bash
python -m tools --list                                            # every tool's JSON Schema
python -m tools sentiment_trend --json '{"ticker": "NVDA", "days": 7}'
python -m tools run_sql --json '{"query": "SELECT count(*) FROM articles"}'
```

`sentiment_trend` and `compare_tickers` report how many days in the period were fully fetched,
and add `warnings` when a result rests on incomplete or capped days.

All tools connect as the `agent_readonly` Postgres role (SELECT on the data tables only,
read-only transactions, 3s statement timeout). `run_sql` additionally parses each query with
Postgres' own grammar and only runs a single SELECT over allowlisted tables and functions,
capped at 200 rows.

### Tests and lint
```bash
pytest
ruff check . && ruff format --check .
```

DB tests run against `TEST_DATABASE_URL` (created automatically by `docker compose`) and reset its schema at the start of each session.

### Frontend
```bash
cd frontend && npm install && npm run dev
```

---

Created by Sidhant Yadav
