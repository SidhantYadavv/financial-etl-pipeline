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
tools/ agent/ api/ evals/   placeholders for later phases
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

Every load uses `INSERT ... ON CONFLICT DO NOTHING` inside one transaction, so re-running never duplicates rows.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Docker

### Setup
```bash
cp .env.example .env              # then add your NEWS_API_KEY
docker compose up -d              # Postgres 17 on localhost:5434
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m nltk.downloader vader_lexicon
alembic upgrade head              # create schema and seed tickers
```

On macOS with the python.org installer, run `Install Certificates.command` from the Python folder first if the NLTK download fails with `CERTIFICATE_VERIFY_FAILED`.

### Running the pipeline
```bash
python -m pipeline backfill   # last 30 days of news and prices (~80 NewsAPI requests)
python -m pipeline run        # one daily run (20 NewsAPI requests)
python -m pipeline serve      # stay running and trigger the daily run at 11:00 UTC
```

The NewsAPI free tier allows 100 requests per day and only the last month of articles. `NEWS_API_REQUEST_BUDGET` caps the requests a single run may make; anything over budget is logged as skipped.

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
