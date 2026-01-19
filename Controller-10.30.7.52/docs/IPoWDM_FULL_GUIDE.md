# IPoWDM Kafka‑Based Multi‑Tenant SDN Control Plane


- ✅ **Module A — Link Database**: FastAPI service + Redis datastore + Docker deployment + health checks
- ✅ **Module B — Slice Manager**: FastAPI service implementing **Case 1 (vOp activation)**, Kafka topic provisioning, LinkDB interface reservations, deployment hook
- ✅ **Module C — IP‑SDN Controller (Phase 1 & Phase 2)**: LinkDB client, path computation (Dijkstra + slot sizing), connection manager (lifecycle / state), plus verification scripts

> Note: This document intentionally focuses on **successfully implemented steps and working artifacts**. Anything not built yet is listed in the roadmap.

---

## Table of Contents

1. [System architecture](#system-architecture)
2. [Control plane addresses, ports, and naming](#control-plane-addresses-ports-and-naming)
3. [Module A — Link Database](#module-a--link-database)
   - [A.1 Purpose and responsibilities](#a1-purpose-and-responsibilities)
   - [A.2 Successful implementation steps](#a2-successful-implementation-steps)
   - [A.3 Directory structure](#a3-directory-structure)
   - [A.4 File-by-file guide](#a4-file-by-file-guide)
   - [A.5 Configuration](#a5-configuration)
   - [A.6 Runbook](#a6-runbook)
   - [A.7 Verification](#a7-verification)
   - [A.8 Troubleshooting](#a8-troubleshooting)
4. [Module B — Slice Manager](#module-b--slice-manager)
   - [B.1 Purpose and responsibilities](#b1-purpose-and-responsibilities)
   - [B.2 Successful implementation steps](#b2-successful-implementation-steps)
   - [B.3 Directory structure](#b3-directory-structure)
   - [B.4 File-by-file guide](#b4-file-by-file-guide)
   - [B.5 API surface + examples](#b5-api-surface--examples)
   - [B.6 Configuration](#b6-configuration)
   - [B.7 Runbook](#b7-runbook)
   - [B.8 Verification](#b8-verification)
   - [B.9 Troubleshooting](#b9-troubleshooting)
5. [Module C — IP‑SDN Controller (Phase 1 & 2)](#module-c--ip-sdn-controller-phase-1--2)
   - [C.1 Purpose and responsibilities](#c1-purpose-and-responsibilities)
   - [C.2 Successful implementation steps](#c2-successful-implementation-steps)
   - [C.3 Directory structure](#c3-directory-structure)
   - [C.4 File-by-file guide](#c4-file-by-file-guide)
   - [C.5 Configuration](#c5-configuration)
   - [C.6 Phase-2 verification runbook](#c6-phase-2-verification-runbook)
   - [C.7 Troubleshooting](#c7-troubleshooting)
6. [End‑to‑end flows implemented](#end-to-end-flows-implemented)
7. [Operational notes](#operational-notes)
8. [Roadmap (remaining work)](#roadmap-remaining-work)
9. [Appendix: Command cheat sheet](#appendix-command-cheat-sheet)

---

## System architecture

This project follows the OFC paper architecture: a Kafka-based control plane that supports **multi-tenancy** via **virtual operators (vOps)**.

**Per‑vOp isolation** is achieved using dedicated Kafka topics:
- `config_<vOpId>` → controller/slice-manager publishes commands, SONiC agents consume
- `monitoring_<vOpId>` → SONiC agents publish telemetry, controller consumes

High-level components:
- **Kafka Broker**: central publish/subscribe message bus
- **SONiC agents**: run on Edgecore SONiC switches; consume config commands and publish telemetry
- **Link Database**: stores topology + interface ownership + spectrum/slot occupancy
- **Slice Manager**: Case 1 (operator activation); creates Kafka topics + reserves interfaces + triggers controller deployment
- **IP‑SDN Controller**: Case 2/3 (connection setup + QoT reconfiguration). In this chat window we implemented Phase 1/2 core logic.

```mermaid
flowchart LR
  U[Operator / Orchestrator] -->|REST| SM[Slice Manager]
  SM -->|Redis ops| LDB[(Link DB Redis)]
  SM -->|Kafka Admin| K[(Kafka Broker)]
  SM -->|deploy-controller.sh| C[IP-SDN Controller (per vOp)]
  C -->|Redis ops| LDB
  C -->|publish setup/reconfig| K
  A1[SONiC Agent POP1] -->|telemetry| K
  A2[SONiC Agent POP2] -->|telemetry| K
  K -->|config commands| A1
  K -->|config commands| A2
```

---

## Control plane addresses, ports, and naming

Control plane VM: **10.30.7.52**

### Services (as deployed during the chats)

- **Kafka Broker**: `10.30.7.52:9092`
- **Link Database API**: `http://localhost:8000`
- **Link Database Redis**:
  - container name: `link-db-redis`
  - host mapped: `localhost:6379`
- **Slice Manager API**: `http://localhost:8082`
  - container listens on 8080, host mapping is `8082:8080`
- **AKHQ (Kafka UI)**: typically `http://10.30.7.52:8081`

### Naming conventions

- **vOps**: `vOp1`, `vOp2`, ...
- **Kafka topics per vOp**:
  - `config_vOp2`
  - `monitoring_vOp2`
- **Containers** (examples):
  - `link-database`, `link-db-redis`
  - `slice-manager`, `akhq-slice-manager`

---

# Module A — Link Database

## A.1 Purpose and responsibilities

The **Link Database** is the control‑plane datastore + API responsible for:

- Maintaining **physical topology** (POPs, routers, links)
- Maintaining **interface allocation** (which vOp owns which interface)
- Maintaining **spectrum / slot occupancy** for optical links
- Providing API endpoints used by Slice Manager and Controller to:
  - read topology
  - reserve/release resources
  - allocate spectrum using a simple policy (first‑fit)

---

## A.2 Successful implementation steps

These are the key steps that were successfully completed for Link Database:

1. **Created Link DB project skeleton** (FastAPI + Redis + Docker)
2. **Fixed Docker build failure** caused by missing `link_database/` package directory in the build context
3. **Resolved Docker permission issues** (needed `sudo docker-compose ...` on the VM)
4. **Fixed ASGI import error**:
   - error: `Could not import module "link_database.main"`
   - fix: ensured `link_database/__init__.py` exists and `link_database/main.py` is present in container `/app/link_database/`
5. **Improved /health diagnostics** to verify Redis and Kafka connectivity
6. **Validated the service** using:
   - `curl http://localhost:8000/`
   - `curl http://localhost:8000/health`

---

## A.3 Directory structure

Deployed as `~/link-database` on the VM.

```text
link-database/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── deploy.sh
└── link_database/
    ├── __init__.py
    ├── main.py
    ├── schema.py
    └── first_fit.py
```

---

## A.4 File-by-file guide

### `Dockerfile`
- Builds a Python image that runs:
  - `uvicorn link_database.main:app --host 0.0.0.0 --port 8000`
- Copies the repo into `/app` and installs `requirements.txt`.

### `docker-compose.yml`
Creates two services:

- `link-database`
  - exposes `8000:8000`
  - environment points Redis to the compose service name (`redis`) by default
- `redis` (container name `link-db-redis`)
  - image `redis:7-alpine`
  - exposes `6379:6379`
  - persistent volume `redis-data`

### `deploy.sh`
- Convenience wrapper used during setup to:
  - `up` / `down` services
  - run basic health checks (curl `/health`)
  - print logs when failures occur

### `link_database/main.py`
- FastAPI application
- Core concerns:
  - Startup connectivity checks (Redis; optional Kafka)
  - `/` and `/health` endpoints
  - Topology CRUD endpoints (POPs/links)
  - Resource management endpoints (interfaces / slots)

**Typical endpoints implemented/used in the chats:**
- `GET /` (service banner)
- `GET /health`
- `GET /api/topology`
- `GET /api/frequencies/{link_id}` (spectrum slots)
- `POST /api/connections/allocate` (allocate spectrum)

### `link_database/schema.py`
- Shared data models and enums used by API + allocator
- Examples:
  - `ConnectionStatus`
  - `FrequencySlotStatus`
  - topology entities (POPs, routers, links)

### `link_database/first_fit.py`
- Implements a **first‑fit** allocation strategy:
  - find contiguous available slots
  - allocate them for a requested connection

---

## A.5 Configuration

Set via environment variables in `docker-compose.yml`:

- `REDIS_URL` (example used): `redis://redis:6379`
- `KAFKA_BROKER` (example used): `10.30.7.52:9092`
- `LOG_LEVEL`: `INFO`

**Important note:**
- Inside docker-compose network, the Redis hostname is `redis` (service name).
- From the host OS, Redis is reachable at `localhost:6379` (because we mapped the port).

---

## A.6 Runbook

From the VM:

```bash
cd ~/link-database

# Start
sudo docker-compose up -d

# Watch logs
sudo docker-compose logs -f link-database

# Stop
sudo docker-compose down
```

---

## A.7 Verification

```bash
# API banner
curl http://localhost:8000/

# Health
curl http://localhost:8000/health

# (Optional) From inside container
sudo docker exec link-database curl -s http://localhost:8000/health

# Redis reachable on host
redis-cli -h localhost -p 6379 ping
```

---

## A.8 Troubleshooting

### `Could not import module "link_database.main"`
**Cause:** missing `link_database/` package, missing `__init__.py`, or files not copied into image.

**Fix (what we did):** ensure directory exists and is copied:

```bash
mkdir -p link_database
touch link_database/__init__.py
# ensure main.py exists in link_database/
```

### Docker permission errors
**Cause:** user not in docker group.

**Fix used:** run with `sudo`:

```bash
sudo docker-compose up -d
```

### Redis hostname confusion
- Inside compose: use `redis` (service name)
- From host: use `localhost`

---

# Module B — Slice Manager

## B.1 Purpose and responsibilities

The **Slice Manager** implements **Case 1: Virtual Operator activation**.

When a new vOp is requested, Slice Manager:

1. Validates the activation request
2. Ensures requested interfaces are available
3. Reserves interfaces in Link Database (Redis)
4. Creates vOp‑specific Kafka topics:
   - `config_<vOpId>`
   - `monitoring_<vOpId>`
5. Stores vOp metadata/state in Link Database
6. Triggers controller deployment via a hook script (`deploy-controller.sh`)

---

## B.2 Successful implementation steps

Key successful steps completed for Slice Manager:

1. **Created modular project structure** (`config/`, `models/`, `core/`, `api/`, `scripts/`)
2. Implemented **Kafka topic provisioning** via an admin client
3. Implemented **LinkDB integration** for:
   - vOp state storage
   - interface reservation and availability checks
4. Implemented **REST API** endpoints for vOp lifecycle
5. Fixed **Pydantic v2 issues**:
   - replaced deprecated `regex=` with `pattern=`
6. Fixed Docker runtime problems:
   - **Redis host port 6379 collision** (LinkDB already exposes 6379)
   - resolved by not exposing Slice Manager’s internal Redis to the host / or removing duplicate redis service and using LinkDB redis
7. Fixed **HTTP port collision**:
   - 8080 was already in use
   - Slice Manager standardized on host port **8082** (`8082:8080`)
8. Verified Slice Manager successfully running:
   - `curl http://localhost:8082/health` returned OK
9. Successfully activated `vOp2` and verified listing:
   - `GET /api/v1/vops` returned `vOp2` with status ACTIVE

---

## B.3 Directory structure

Deployed as `~/slice-manager` (or in your case also appeared as `~/slice_manager` in terminal output).

```text
slice-manager/
├── app.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── README.md
├── config/
│   ├── __init__.py
│   └── settings.py
├── models/
│   ├── __init__.py
│   └── schemas.py
├── core/
│   ├── __init__.py
│   ├── kafka_admin.py
│   ├── linkdb_client.py
│   └── slice_orchestrator.py
├── api/
│   ├── __init__.py
│   └── dependencies.py
└── scripts/
    ├── deploy.sh
    ├── health_check.py
    └── deploy-controller.sh   # copied from your environment
```

---

## B.4 File-by-file guide

### `app.py`
FastAPI app exposing northbound API.

Typical endpoints implemented/used:
- `GET /health`
- `POST /api/v1/vops` (activate)
- `GET /api/v1/vops` (list)
- `GET /api/v1/vops/{vop_id}` (details)
- `DELETE /api/v1/vops/{vop_id}` (deactivate)
- (in chats) interface check endpoint existed in examples:
  - `GET /api/v1/interfaces/check/{pop}/{router}/{iface}`

### `config/settings.py`
Central configuration via environment variables:
- Kafka broker
- LinkDB Redis host/port
- API listen host/port
- path to deploy script

### `models/schemas.py`
Pydantic models for:
- vOp activation request
- interface assignment format
- vOp status responses

### `core/kafka_admin.py`
Kafka Admin client wrapper to:
- create topics
- verify topics exist

### `core/linkdb_client.py`
Client wrapper for LinkDB Redis operations:
- reserve interfaces
- query interface availability
- store vOp metadata/status

### `core/slice_orchestrator.py`
Orchestration logic that ties together:
- request validation
- LinkDB reservation
- Kafka topic provisioning
- controller deployment hook

### `api/dependencies.py`
FastAPI dependency wiring:
- instantiate settings
- build Kafka admin client
- build LinkDB client

### `scripts/deploy.sh`
Docker wrapper used during deployment:
- starts containers
- prints API URL and docs URL
- runs health checks (against port 8082)

### `scripts/health_check.py`
Connectivity checks:
- REST health endpoint
- Kafka reachability (admin client)
- LinkDB reachability

### `scripts/deploy-controller.sh`
Hook script (existing in your environment) invoked by Slice Manager to deploy the per‑vOp controller.

---

## B.5 API surface + examples

### Health
```bash
curl http://localhost:8082/health
```

### List vOps
```bash
curl http://localhost:8082/api/v1/vops
```

### Activate vOp2 (Case 1)
Example request format used during the chats:

```bash
curl -X POST http://localhost:8082/api/v1/vops   -H "Content-Type: application/json"   -d '{
    "vop_id": "vOp2",
    "tenant_name": "Operator B",
    "description": "Second virtual operator",
    "interface_assignments": [
      {
        "pop_id": "pop1",
        "router_id": "router1",
        "interfaces": ["Ethernet56"]
      }
    ]
  }'
```

### vOp2 details
```bash
curl http://localhost:8082/api/v1/vops/vOp2
```

---

## B.6 Configuration

Environment (via `.env` or compose `environment:`):

- `KAFKA_BROKER=10.30.7.52:9092`
- LinkDB Redis connection:
  - if Slice Manager is on the same Docker network: `LINKDB_HOST=link-db-redis`
  - if running from host: `LINKDB_HOST=localhost`
  - `LINKDB_PORT=6379`
- `LOG_LEVEL=INFO`

**Ports**
- container: 8080
- host mapping (final working): `8082:8080`

---

## B.7 Runbook

```bash
cd ~/slice-manager

# Start
sudo docker-compose up -d

# Logs
sudo docker-compose logs -f slice-manager

# Stop
sudo docker-compose down
```

---

## B.8 Verification

```bash
# Health
curl -s http://localhost:8082/health

# List vOps
curl -s http://localhost:8082/api/v1/vops

# OpenAPI
curl -s http://localhost:8082/openapi.json | head -n 30
```

Expected: Slice Manager returns **HTTP 200** and lists active vOps (e.g., `vOp2`).

---

## B.9 Troubleshooting

### `Bind for 0.0.0.0:6379 failed: port is already allocated`
**Cause:** LinkDB already maps Redis to host 6379.

**Fix used:** do not map Slice Manager’s internal Redis to host OR remove the extra Redis service and use LinkDB redis.

### `failed to bind host port ... 8080 ... address already in use`
**Cause:** another service already uses 8080.

**Fix used:** change mapping to `8082:8080`.

### Slice Manager can’t reach LinkDB Redis
**Cause:** Docker DNS only resolves service names inside the same network.

**Working options:**
- Use `localhost:6379` (LinkDB maps Redis to host)
- Or connect Slice Manager container to LinkDB network / share a compose network

---

# Module C — IP‑SDN Controller (Phase 1 & 2)

## C.1 Purpose and responsibilities

The **IP‑SDN Controller** is the orchestration brain for:

- **Case 2: End‑to‑end connection setup**
  - compute path
  - allocate spectrum slots
  - send `setupConnection` to agents via Kafka
- **Case 3: Reconfiguration** (QoT‑triggered)

In this chat window we implemented **Phase 1 & Phase 2 core logic**:

- ✅ Configuration layer
- ✅ Pydantic models
- ✅ LinkDB Redis client
- ✅ Path computation engine (Dijkstra + slot sizing + contiguous slot search)
- ✅ Connection manager (lifecycle + state)
- ✅ Verification tooling (`verify_phase2.py`, `quick_check.py`, `test_phase2.py`, `run_test.sh`)

Kafka manager, REST API endpoints, QoT monitor, and agent dispatcher are **not implemented yet** in this window (see roadmap).

---

## C.2 Successful implementation steps

1. Defined the controller architecture and decided on a clean rewrite
2. Created the directory skeleton (skipping a full `tests/` folder per your request)
3. Implemented Phase 1:
   - `config/settings.py`
   - `models/schemas.py`
   - `core/linkdb_client.py`
4. Implemented Phase 2:
   - `core/path_computer.py`
   - `core/connection_manager.py`
5. Added Phase‑2 verification scripts and fixed import issues:
   - scripts initially failed when run from inside `scripts/` because modules weren’t on `PYTHONPATH`
   - fixed by using `PYTHONPATH="."` or by inserting project root into `sys.path`
6. Identified and documented LinkDB hostname behavior:
   - `link-db-redis` works only inside Docker network
   - from host execution use `localhost`

---

## C.3 Directory structure

As created during the chats:

```text
ip-sdn-controller/
├── config/
│   ├── __init__.py
│   └── settings.py
├── models/
│   ├── __init__.py
│   └── schemas.py
├── core/
│   ├── __init__.py
│   ├── linkdb_client.py
│   ├── path_computer.py
│   └── connection_manager.py
├── api/
│   └── __init__.py
├── utils/
│   └── __init__.py
├── scripts/
│   ├── verify_phase2.py
│   └── quick_check.py
├── test_phase2.py
└── run_test.sh
```

> `api/` and `utils/` are present as placeholders for Phase 3/4, but Phase 1/2 work lives in `config/`, `models/`, `core/`, and `scripts/`.

---

## C.4 File-by-file guide

### `config/settings.py`
Configuration is environment‑driven (Pydantic settings). Includes:
- API host/port defaults
- Kafka broker address
- vOp identifiers + topic names
- LinkDB Redis host/port
- log level

### `models/schemas.py`
Pydantic models for controller inputs/outputs and internal objects, including:
- connection request parameters (source/destination POP, bandwidth, modulation, etc.)
- connection status enums
- link / topology structures

### `core/linkdb_client.py`
Redis client wrapper for LinkDB operations:
- health check
- topology retrieval
- interface checks (ownership / availability)
- spectrum slot retrieval and allocation/release primitives

### `core/path_computer.py`
Computes a feasible optical path:
- shortest path computation (Dijkstra)
- slot requirement estimation based on bandwidth + modulation
- contiguous slot search along path segments
- path validation utilities

### `core/connection_manager.py`
Connection lifecycle orchestration:
- create connection object and compute required path resources
- validate feasibility
- hold state transitions (planned/active/failed, etc.)
- integrate slot computation and (in later phases) command dispatch

### `scripts/verify_phase2.py`
A test/verification runner that exercises:
- LinkDB connectivity
- topology fetch
- path computation
- slot requirement calculation
- connection manager create/release flows

### `scripts/quick_check.py`
A quick sanity check script.

### `test_phase2.py` + `run_test.sh`
Local test harness; `run_test.sh` ensures correct module resolution:

```bash
PYTHONPATH="." python3 test_phase2.py
```

---

## C.5 Configuration

Typical `.env` used during verification:

```bash
# LinkDB
LINKDB_HOST=localhost
LINKDB_PORT=6379

# Kafka
KAFKA_BROKER=10.30.7.52:9092
VIRTUAL_OPERATOR=vOp2
CONFIG_TOPIC=config_vOp2
MONITORING_TOPIC=monitoring_vOp2

# API (future)
API_HOST=0.0.0.0
API_PORT=8083

LOG_LEVEL=INFO
```

---

## C.6 Phase-2 verification runbook

From the controller project root:

```bash
cd ~/ip-sdn-controller

# Option A: run quick check
python3 scripts/quick_check.py

# Option B: run full Phase-2 verification
python3 scripts/verify_phase2.py

# Option C: run harness that sets PYTHONPATH
chmod +x run_test.sh
./run_test.sh
```

---

## C.7 Troubleshooting

### Import errors when running scripts inside `scripts/`
**Symptom:** `ModuleNotFoundError: No module named 'core'`

**Fix used:** run from project root with PYTHONPATH:

```bash
PYTHONPATH="." python3 scripts/verify_phase2.py
```

(or add project root to `sys.path` in the script).

### LinkDB Redis hostname confusion
- From host OS: use `localhost:6379`
- Inside Docker network: `link-db-redis` resolves (if attached to same network)

---

# End-to-end flows implemented

## Case 1 — vOp activation (✅ implemented)

**Trigger:** REST request to Slice Manager.

**Flow:**
1. Client calls `POST /api/v1/vops`
2. Slice Manager validates payload
3. Slice Manager checks/reserves requested interfaces in LinkDB Redis
4. Slice Manager creates Kafka topics `config_<vOpId>` and `monitoring_<vOpId>`
5. Slice Manager stores vOp status as ACTIVE
6. Slice Manager calls `deploy-controller.sh` to spin up controller for that vOp

## Case 2 — Connection setup (⚠️ core logic implemented)

In this window we implemented the controller’s **Phase‑2 internal logic**:
- topology read
- shortest path computation
- slot sizing
- feasibility checks
- connection lifecycle object management

**Not yet wired in this window:**
- REST API endpoint to accept connection requests
- Kafka command dispatch to SONiC agents

---

# Operational notes

## Logs

```bash
# LinkDB
sudo docker-compose logs -f link-database

# Slice Manager
sudo docker-compose logs -f slice-manager

# Redis container logs
sudo docker-compose logs -f redis
```

## Kafka topic checks

Use AKHQ UI (if running) or Kafka CLI:

```bash
# list topics
kafka-topics.sh --bootstrap-server 10.30.7.52:9092 --list

# consume telemetry
kafka-console-consumer.sh --bootstrap-server 10.30.7.52:9092 --topic monitoring_vOp2 --from-beginning

# produce config message (manual)
kafka-console-producer.sh --broker-list 10.30.7.52:9092 --topic config_vOp2
```

## Docker networking rule of thumb

- Service names like `redis` or `link-db-redis` are resolvable only **inside Docker networks**.
- For host‑executed scripts, prefer `localhost` on mapped ports.

---

# Roadmap (remaining work)

These were explicitly identified as **next steps** beyond what was implemented in the chat window:

1. **IP‑SDN Controller Phase 3**
   - Kafka manager (producer/consumer)
   - agent dispatcher for `setupConnection` / `reconfigConnection`
2. **IP‑SDN Controller Phase 4**
   - REST API endpoints (FastAPI app, routers, dependencies)
3. **QoT monitoring + reconfiguration loop**
   - consume `monitoring_<vOp>` telemetry
   - detect degradation and trigger reconfiguration (Case 3)
4. **Full integration tests with SONiC agents**
   - validate actual commands are executed on the switches

---

# Appendix: Command cheat sheet

## Link Database

```bash
cd ~/link-database
sudo docker-compose up -d
curl http://localhost:8000/health
sudo docker-compose logs -f link-database
```

## Slice Manager

```bash
cd ~/slice-manager
sudo docker-compose up -d
curl http://localhost:8082/health
curl http://localhost:8082/api/v1/vops
sudo docker-compose logs -f slice-manager
```

## Activate vOp2

```bash
curl -X POST http://localhost:8082/api/v1/vops   -H "Content-Type: application/json"   -d '{
    "vop_id": "vOp2",
    "tenant_name": "Operator B",
    "description": "Second virtual operator",
    "interface_assignments": [
      {"pop_id":"pop1","router_id":"router1","interfaces":["Ethernet56"]}
    ]
  }'
```

## Controller Phase‑2 verification

```bash
cd ~/ip-sdn-controller
PYTHONPATH="." python3 scripts/verify_phase2.py
```

---

**End of document.**
