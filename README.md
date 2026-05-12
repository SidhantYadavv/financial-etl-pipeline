# Financial News ETL Pipeline

A production-grade Data Engineering pipeline that automates the extraction, transformation, and loading (ETL) of financial news data. The project features a modern React/Next.js dashboard to visualize market sentiment and trends.

---

## Overview

This project implements an automated ETL workflow to monitor financial markets:
1. **Extract**: Scrapes the latest financial news from various APIs/Sources.
2. **Transform**: Processes raw text, performs sentiment analysis, and cleans data using Python.
3. **Load**: Stores structured data in a PostgreSQL/SQLite database for persistence.
4. **Visualize**: A real-time dashboard built with Next.js and Tailwind CSS.

---

## Tech Stack

- **Backend**: Python (pandas, SQLAlchemy, BeautifulSoup/NewsAPI)
- **Database**: SQLite/PostgreSQL
- **Frontend**: Next.js, React, Lucide React, Tailwind CSS
- **Containerization**: Docker & Docker Compose
- **Orchestration**: Python-based automation

---

## Project Structure

- `extract.py`: Handles data retrieval from news sources.
- `transform.py`: Cleans and enriches data (sentiment analysis, formatting).
- `load.py`: Manages database connections and data insertion.
- `orchestrate.py`: The main controller that runs the full ETL cycle.
- `frontend/`: Next.js web application for data visualization.
- `Dockerfile` & `docker-compose.yml`: Containerization setup for easy deployment.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js & npm (for frontend)
- Docker (optional)

### Setup
1. **Clone the repository**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/financial-etl-pipeline.git
   cd financial-etl-pipeline
   ```

2. **Backend Setup**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Frontend Setup**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

4. **Run the ETL Pipeline**:
   ```bash
   python orchestrate.py
   ```

---

Created by Sidhant Yadav
