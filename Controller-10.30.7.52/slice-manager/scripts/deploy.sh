#!/bin/bash
# scripts/deploy.sh

set -e

echo "Deploying Slice Manager..."

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if docker-compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "ERROR: docker-compose is not installed. Please install docker-compose."
    exit 1
fi

# Create necessary directories
mkdir -p data/redis

# Copy environment file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "WARNING: Please edit .env file with your configuration before starting."
fi

# Clean up any existing containers
echo "Cleaning up any existing containers..."
docker-compose down --remove-orphans 2>/dev/null || true

# Build and start containers
echo "Starting Slice Manager stack..."
docker-compose up -d --build

# Wait for services to be ready
echo "Waiting for services to be ready (30 seconds)..."
sleep 30

# Display status without health check
echo "Container status:"
docker-compose ps

echo ""
echo "Slice Manager deployment complete!"
echo "API URL: http://localhost:8082"
echo "API Docs: http://localhost:8082/docs"
echo ""
echo "To check logs: docker-compose logs"
echo "To check health manually: curl http://localhost:8082/health"
echo "To stop services: docker-compose down"