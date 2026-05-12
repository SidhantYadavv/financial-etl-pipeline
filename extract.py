import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load the API keys from the .env file
load_dotenv()
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

def extract_news_data():
    """
    Connects to the NewsAPI to fetch the latest financial news.
    """
    print(f"Starting extraction at {datetime.now()}...")
    
    BASE_URL = "https://newsapi.org/v2/everything"
    
    # We will search for financial news
    params = {
        "q": "finance OR economy OR stock market",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 50, # Fetch the top 50 articles
        "apiKey": NEWS_API_KEY
    }
    
    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status() # Raise an error for bad status codes
        
        data = response.json()
        
        # We will save the raw data to a JSON file so Phase 2 (Transform) can read it locally
        output_file = "raw_news_data.json"
        with open(output_file, "w") as f:
            json.dump(data, f, indent=4)
            
        print(f"✅ Successfully extracted data and saved to {output_file}")
        return output_file
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error extracting data: {e}")
        return None

if __name__ == "__main__":
    extract_news_data()
