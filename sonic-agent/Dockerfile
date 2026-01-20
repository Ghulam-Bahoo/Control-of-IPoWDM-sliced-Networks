FROM python:3.11-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy requirements FIRST (better caching)
COPY requirements.txt .

# INSTALL PACKAGES - with retry and verbose output
RUN echo "Installing Python packages..." && \
    pip install --no-cache-dir -r requirements.txt && \
    echo "Verifying installations..." && \
    python -c "import kafka; print(f'kafka-python: {kafka.__version__}')" && \
    python -c "import pydantic; print(f'pydantic: {pydantic.__version__}')"

# Copy the rest of the application
COPY . .

# Create necessary directories
RUN mkdir -p /var/log/sonic-agent

CMD ["python", "-m", "core.sonic_agent"]
