# 1. Base Image: Lightweight Python 3.10
FROM python:3.10-slim

# 2. Set Working Directory inside the container
WORKDIR /app

# 3. Install System Dependencies (Required for some AI libraries)
RUN apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev && rm -rf /var/lib/apt/lists/*

# 4. Copy and Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. PRE-BAKE THE BRAIN (Crucial for Speed & Stability)
# This downloads the embedding model during the build, preventing timeouts on startup.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# 6. Copy the rest of the backend code
COPY . .

# 7. Expose the API Port
EXPOSE 8000

# 8. Start the Application
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]