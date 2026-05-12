import pandas as pd
from sqlalchemy import create_engine, text
import os

def load_data_to_db():
    """
    Loads cleaned data from CSV into a local SQLite database.
    """
    print("Starting database loading process...")
    input_file = "cleaned_news_data.csv"
    
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found. Please run transform.py first.")
        return
        
    # Read the cleaned data
    df = pd.read_csv(input_file)
    print(f"Loaded {len(df)} rows from CSV.")
    
    # -------------------------------------------------------------------------
    # Note on PostgreSQL:
    # If you ever install PostgreSQL locally (via Postgres.app or Homebrew),
    # you would simply change the engine connection string below to:
    # engine = create_engine('postgresql://username:password@localhost/financial_news')
    # -------------------------------------------------------------------------
    
    # We are using SQLite for local, zero-setup testing. It works exactly like Postgres.
    db_file = "financial_news.db"
    engine = create_engine(f"sqlite:///{db_file}")
    
    # Write the data to a SQL table called 'articles'
    # 'replace' will overwrite the table if it exists, 'append' adds to it.
    df.to_sql('articles', con=engine, if_exists='replace', index=False)
    
    print(f"✅ Successfully loaded {len(df)} records into the 'articles' table in {db_file}")
    
    # Verify the insertion by querying it back
    with engine.connect() as connection:
        result = connection.execute(text("SELECT COUNT(*) FROM articles")).fetchone()
        print(f"Verification: Found {result[0]} rows in the database.")

if __name__ == "__main__":
    load_data_to_db()
