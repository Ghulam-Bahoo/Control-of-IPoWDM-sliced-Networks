
# IPoWDM Link Database

Link Database for IPoWDM optical networks. Stores interconnection details and spectrum occupation as described in the OFC26 paper.

## Features

- **Topology Management**: Store POPs, routers, and optical links
- **Frequency Allocation**: First-fit algorithm for spectrum assignment
- **Connection Tracking**: End-to-end optical connection management
- **Multi-tenancy**: Support for virtual operators (vOp1, vOp2, etc.)
- **Real-time Updates**: Kafka integration for event publishing
- **REST API**: Full CRUD operations via HTTP API
- **Redis Backend**: High-performance data storage

## Architecture
┌─────────────────────────────────────────────────┐
│ Link Database │
│ (FastAPI + Redis) │
├─────────────────────────────────────────────────┤
│ • POP Management │
│ • Link Management │
│ • Frequency Allocation (First-Fit) │
│ • Connection Tracking │
│ • Kafka Integration │
└─────────────────┬───────────────────────────────┘
│ REST API
┌────────▼────────┐
│ IP SDN │
│ Controller │
│ (Future) │
└─────────────────┘

# Quick Start

### Prerequisites

- Docker and Docker Compose
- Kafka running at `10.30.7.52:9092`

### Deployment
Link Database MVP:
├── Database Schema
├── REST API (FastAPI)
├── Kafka Consumer/Producer
├── First-fit Algorithm
└── Basic CRUD Operations

1. Clone or create the project structure:
   ```bash
   mkdir link-database
   cd link-database

# Rebuild and restart
sudo docker-compose down
sudo docker-compose build --no-cache
sudo docker-compose up -d

# MODULE 1: LINK DATABASE
Minimal Viable Product Scope:
text
## Link Database MVP:
├── Database Schema
├── REST API (FastAPI)
├── Kafka Consumer/Producer
├── First-fit Algorithm
└── Basic CRUD Operations
Implementation Steps:
Phase 1A: Database design + CRUD API
Phase 1B: First-fit frequency allocation
Phase 1C: Kafka integration
Phase 1D: Testing with mock data

## Summary:
✅ Redis connected - Data storage working
✅ All API endpoints working - GET/POST operations functional
✅ Topology view working - Shows POPs, links, connections
✅ Connection allocation working - First-fit algorithm (simplified)
✅ Data persistence - New data survives restarts

## What Works:
✅ Create/List POPs

✅ Create/List optical links

✅ Allocate connections with frequency assignment

✅ View complete network topology

✅ Health monitoring

# Module 2 - Slice Manager
Now implement the Slice Manager for Study Case 1: Activation of new virtual operator from the paper.

## Slice Manager Responsibilities:
    Receive requests for new virtual operators (vOp1, vOp2, etc.)

    Create Kafka topics - config_vOpX and monitoring_vOpX

    Allocate resources from Link Database

    Deploy SONiC agents for the operator

    Manage isolation between operators

Slice Manager Implementation:
Directory Structure:

text
slice-manager/
├── __init__.py
├── main.py              # FastAPI app
├── models.py            # Data models
├── kafka_manager.py     # Topic management
├── link_db_client.py    # Link Database client
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
API Design:
python
# POST /api/slices - Create new virtual operator slice
{
  "vop_id": "vOp3",
  "name": "Cloud Provider C",
  "requested_pops": ["pop1", "pop2"],
  "requested_interfaces_per_pop": 4
}

# Response:
{
  "vop_id": "vOp3",
  "status": "activated",
  "kafka_topics": {
    "config": "config_vOp3",
    "monitoring": "monitoring_vOp3"
  },
  "assigned_resources": {
    "pop1": ["Ethernet48", "Ethernet52"],
    "pop2": ["Ethernet48", "Ethernet52"]
  }
}