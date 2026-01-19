FROM ubuntu:20.04

WORKDIR /app

# Install Python 3.9 and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.9 \
    python3-pip \
    python3.9-dev \
    python3.9-distutils \
    python3.9-venv \
    && ln -s /usr/bin/python3.9 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN python -m pip install --upgrade pip

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY core /app/core
COPY config /app/config
COPY models /app/models
COPY scripts /app/scripts

CMD ["python", "-m", "core.sonic_agent"]