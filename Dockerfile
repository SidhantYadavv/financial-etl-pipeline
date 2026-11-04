# Use the official Python image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Ensure NLTK data is downloaded into the container
RUN python -m nltk.downloader vader_lexicon

# Long-running process that triggers the daily flow on its cron schedule
CMD ["python", "-m", "pipeline", "serve"]
