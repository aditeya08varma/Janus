# 1. Base Image: Match your local version (3.12) to prevent "it works on my machine" bugs
FROM python:3.12-slim

# 2. Set Environment Variables
# PYTHONDONTWRITEBYTECODE: Prevents Python from writing .pyc files to disk
# PYTHONUNBUFFERED: CRITICAL for Render. Forces logs to stream immediately to the console.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3. Set Working Directory
WORKDIR /app

# 4. Install System Dependencies
# gcc and libffi-dev are required for some of the high-performance async libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 5. Copy and Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. PRE-BAKE THE BRAIN (The "Pro" Move)
# This downloads the embedding model during the build phase.
# If you didn't do this, your first user request would timeout while waiting for the download.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# 7. Copy the rest of the backend code
COPY . .

# 8. Start the Application
# Using shell execution allows $PORT to be expanded correctly by Render
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}