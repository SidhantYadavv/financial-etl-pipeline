from prefect import task, flow
from extract import extract_news_data
from transform import transform_news_data
from load import load_data_to_db

@task(name="Extract Financial News", log_prints=True)
def run_extraction():
    """Task to extract data from APIs"""
    print("Starting Extraction Task...")
    extract_news_data()

@task(name="Transform and Clean Data", log_prints=True)
def run_transformation():
    """Task to transform JSON data and add sentiment scores"""
    print("Starting Transformation Task...")
    transform_news_data()

@task(name="Load to Database", log_prints=True)
def run_load():
    """Task to load cleaned CSV to SQLite/Postgres"""
    print("Starting Load Task...")
    load_data_to_db()

@flow(name="Daily Financial ETL Pipeline", log_prints=True)
def daily_etl_flow():
    """
    Main orchestration flow that runs the ETL steps in sequence.
    If extraction fails, the downstream tasks will fail automatically.
    """
    print("🚀 Initiating Daily Financial ETL Pipeline...")
    
    # Run the sequence
    run_extraction()
    run_transformation()
    run_load()
    
    print("🎉 Pipeline executed successfully!")

if __name__ == "__main__":
    # Run the flow locally
    daily_etl_flow()

# -----------------------------------------------------------------------------
# Deployment Note for Resume:
# To schedule this to run automatically every night at midnight on a cloud server,
# you would simply run this command in the terminal:
# 
# prefect deployment build orchestrate.py:daily_etl_flow -n "daily-etl" -a --cron "0 0 * * *"
# -----------------------------------------------------------------------------
