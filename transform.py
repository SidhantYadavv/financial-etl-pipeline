import pandas as pd
import json
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
import os
import ssl

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# Download the VADER lexicon for sentiment analysis
nltk.download('vader_lexicon')

def transform_news_data():
    """
    Loads raw JSON data, cleans it, adds sentiment analysis, and saves to CSV.
    """
    print("Starting data transformation...")
    input_file = "raw_news_data.json"
    output_file = "cleaned_news_data.csv"
    
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found. Please run extract.py first.")
        return
        
    with open(input_file, "r") as f:
        data = json.load(f)
        
    articles = data.get("articles", [])
    if not articles:
        print("No articles found in the JSON file.")
        return
        
    # Convert to Pandas DataFrame
    df = pd.DataFrame(articles)
    print(f"Loaded {len(df)} articles.")
    
    # Extract source name from the nested 'source' dictionary
    df['source_name'] = df['source'].apply(lambda x: x.get('name') if isinstance(x, dict) else None)
    
    # Keep only necessary columns
    columns_to_keep = ['title', 'author', 'source_name', 'publishedAt', 'url']
    df = df[columns_to_keep]
    
    # --- CLEANING ---
    print("Cleaning data...")
    # Drop rows missing title or url (essential for our database)
    df = df.dropna(subset=['title', 'url'])
    
    # Remove duplicates based on title
    df = df.drop_duplicates(subset=['title'])
    
    # Fill missing authors with 'Unknown'
    df['author'] = df['author'].fillna('Unknown')
    
    # Convert publishedAt to standard datetime
    df['publishedAt'] = pd.to_datetime(df['publishedAt']).dt.strftime('%Y-%m-%d %H:%M:%S')
    
    # --- SENTIMENT ANALYSIS ---
    print("Running Sentiment Analysis on headlines...")
    sia = SentimentIntensityAnalyzer()
    
    def get_sentiment(title):
        score = sia.polarity_scores(title)
        compound = score['compound']
        
        if compound >= 0.05:
            label = "Positive"
        elif compound <= -0.05:
            label = "Negative"
        else:
            label = "Neutral"
            
        return compound, label

    # Apply sentiment function
    sentiment_results = df['title'].apply(get_sentiment)
    
    # Split the results into two new columns
    df['sentiment_score'] = [res[0] for res in sentiment_results]
    df['sentiment_label'] = [res[1] for res in sentiment_results]
    
    # --- SAVE ---
    df.to_csv(output_file, index=False)
    print(f"✅ Successfully transformed data and saved to {output_file}")
    print(f"Final count: {len(df)} cleaned articles ready for loading.")

if __name__ == "__main__":
    transform_news_data()
