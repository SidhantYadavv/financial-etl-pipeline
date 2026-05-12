# Financial ETL Pipeline - Deployment Guide

This project is fully containerized using Docker, making it completely "Cloud Ready." This means you can run this exact pipeline on your laptop, an AWS EC2 server, or a Google Cloud VM without having to manually install Python, Pandas, or any dependencies.

## Option 1: Running Locally with Docker

If you have Docker Desktop installed on your machine, you can run the entire pipeline with a single command:

```bash
docker-compose up --build
```

This will:
1. Build an isolated Linux environment (the container).
2. Install all the necessary packages (Pandas, Prefect, NLTK).
3. Automatically load your `.env` API keys.
4. Run `orchestrate.py` and execute the Extract -> Transform -> Load sequence.

---

## Option 2: Deploying to the Cloud (AWS EC2)

To have this run automatically in the cloud 24/7 without your laptop needing to be on, you can deploy it to an AWS Virtual Machine (EC2).

### Step 1: Create an EC2 Instance
1. Create a free-tier AWS Account.
2. Go to the **EC2 Dashboard** and click **Launch Instance**.
3. Select **Ubuntu** as the Operating System.
4. Launch the instance and connect to it using SSH (or AWS EC2 Instance Connect).

### Step 2: Install Docker on the Server
Once you are logged into the AWS terminal, install Docker:
```bash
sudo apt update
sudo apt install -y docker.io docker-compose
```

### Step 3: Clone Your Code
```bash
git clone https://github.com/YOUR_GITHUB/financial-etl-pipeline.git
cd financial-etl-pipeline
```

### Step 4: Add Your API Keys
Your `.env` file is excluded from GitHub for security. You must recreate it on the server:
```bash
nano .env
# Paste your GOOGLE_API_KEY and NEWS_API_KEY in this file and save it.
```

### Step 5: Run the Pipeline (CRON Scheduling)
You can manually run it on the server anytime using:
```bash
sudo docker-compose up
```

**To automate it to run every night at midnight:**
1. Open the crontab: `crontab -e`
2. Add this line to the bottom of the file (replace with your actual folder path):
   ```bash
   0 0 * * * cd /home/ubuntu/financial-etl-pipeline && sudo docker-compose up
   ```

You now have a fully automated, cloud-hosted, Dockerized ETL pipeline!
