#!/bin/bash

echo "=== Deploying IPoWDM Controller with AKHQ ==="

# Build controller image
echo "Step 1: Building controller image..."
docker build -t ipowdm-controller:latest -f Dockerfile.controller .

# Stop existing containers
echo "Step 2: Stopping existing containers..."
docker stop ipowdm-controller akhq 2>/dev/null
docker rm ipowdm-controller akhq 2>/dev/null

# Deploy controller and AKHQ
echo "Step 3: Deploying controller and AKHQ..."
docker-compose -f docker-compose-controller.yml up -d

echo "Step 4: Deployment complete! Checking status..."
docker ps | grep -E "(ipowdm-controller|akhq)"

echo ""
echo "=== Access Points ==="
echo "AKHQ UI: http://10.30.7.52:8080"
echo "Controller: Running on host network"
echo ""
echo "To view controller logs: docker logs -f ipowdm-controller"
echo "To view AKHQ logs: docker logs -f akhq"
