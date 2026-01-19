# SONiC Agent for vOp2 IPoWDM Controller

## Overview
Professional SONiC agent implementing Kafka-based control for NeoPhotonics 400G ZR modules.

## Features
- Complete CMIS 5.0 configuration support
- Kafka-based command/telemetry interface
- Multi-connection telemetry management
- Production-grade error handling
- Health monitoring and self-healing

## Hardware Requirements
- Edgecore AS9726-32DB switch
- NeoPhotonics QDDMA400700C2000 400G ZR modules
- SONiC 202311.2 or later

## Quick Start
1. Configure environment:
   ```bash
   cp config/.env.example config/.env
   nano config/.env  # Edit Kafka and interface settings