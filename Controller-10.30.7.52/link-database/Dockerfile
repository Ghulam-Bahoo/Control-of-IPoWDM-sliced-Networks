# Dockerfile for Link Database
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY link_database/ ./link_database/

# Create non-root user
RUN useradd -m -u 1000 linkdb && \
    chown -R linkdb:linkdb /app
USER linkdb

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "link_database.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]