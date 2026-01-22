# link_database/main.py - CORRECTED ENDPOINTS
import asyncio
import json
import logging
import os
from typing import Dict, List, Optional, Any
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aioredis

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("link-database")

# Pydantic Models
class POPCreate(BaseModel):
    pop_id: str
    name: str
    location: str
    operator: str = "telco"

class OpticalLinkCreate(BaseModel):
    link_id: str
    pop_a: str
    pop_b: str
    distance_km: float
    fiber_type: str = "SMF"

class ConnectionRequest(BaseModel):
    connection_id: str
    pop_a: str
    pop_b: str
    bandwidth: int
    virtual_operator: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown"""
    # Startup
    logger.info("Starting Link Database...")
    
    # Connect to Redis
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    logger.info(f"Connecting to Redis at {redis_url}")
    
    app.state.redis = await aioredis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True
    )
    
    # Test Redis
    await app.state.redis.ping()
    logger.info("✓ Redis connected")
    
    # Initialize sample data
    await initialize_database(app.state.redis)
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    await app.state.redis.close()

async def initialize_database(redis):
    """Initialize with sample data"""
    # Check if already initialized
    if await redis.exists("pops"):
        logger.info("Database already initialized")
        return
    
    logger.info("Initializing sample data...")
    
    # Sample POPs
    sample_pops = [
        {"pop_id": "pop1", "name": "DC1", "location": "40.7128,-74.0060", "operator": "telco"},
        {"pop_id": "pop2", "name": "DC2", "location": "34.0522,-118.2437", "operator": "telco"},
        {"pop_id": "pop3", "name": "DC3", "location": "51.5074,-0.1278", "operator": "telco"},
    ]
    
    for pop in sample_pops:
        await redis.hset(f"pop:{pop['pop_id']}", mapping=pop)
        await redis.sadd("pops", pop['pop_id'])
    
    # Sample links
    sample_links = [
        {"link_id": "link-pop1-pop2", "pop_a": "pop1", "pop_b": "pop2", "distance_km": 100.5, "fiber_type": "SMF"},
        {"link_id": "link-pop2-pop3", "pop_a": "pop2", "pop_b": "pop3", "distance_km": 150.2, "fiber_type": "SMF"},
    ]
    
    for link in sample_links:
        await redis.hset(f"link:{link['link_id']}", mapping=link)
        await redis.sadd("links", link['link_id'])
    
    logger.info("Sample data initialized")

# Create FastAPI app
app = FastAPI(
    title="IPoWDM Link Database",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "IPoWDM Link Database",
        "version": "1.0.0",
        "status": "operational",
        "endpoints": [
            {"method": "GET", "path": "/"},
            {"method": "GET", "path": "/health"},
            {"method": "GET", "path": "/api/pops"},
            {"method": "POST", "path": "/api/pops"},
            {"method": "GET", "path": "/api/links"},
            {"method": "POST", "path": "/api/links"},
            {"method": "GET", "path": "/api/topology"},
            {"method": "POST", "path": "/api/connections/allocate"},
        ]
    }

@app.get("/health")
async def health():
    """Health check"""
    try:
        await app.state.redis.ping()
        redis_status = "connected"
    except:
        redis_status = "disconnected"
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "redis": redis_status
    }

@app.get("/api/pops")
async def get_pops():
    """Get all POPs"""
    pop_ids = await app.state.redis.smembers("pops")
    pops = []
    
    for pop_id in pop_ids:
        pop_data = await app.state.redis.hgetall(f"pop:{pop_id}")
        if pop_data:
            pops.append(pop_data)
    
    return {"pops": pops, "count": len(pops)}

@app.post("/api/pops")
async def create_pop(pop: POPCreate):
    """Create a new POP"""
    if await app.state.redis.hexists(f"pop:{pop.pop_id}", "pop_id"):
        raise HTTPException(400, f"POP {pop.pop_id} already exists")
    
    pop_data = {
        "pop_id": pop.pop_id,
        "name": pop.name,
        "location": pop.location,
        "operator": pop.operator,
        "created_at": datetime.utcnow().isoformat()
    }
    
    await app.state.redis.hset(f"pop:{pop.pop_id}", mapping=pop_data)
    await app.state.redis.sadd("pops", pop.pop_id)
    
    return {"message": "POP created", "pop": pop_data}

@app.get("/api/links")
async def get_links():
    """Get all optical links - FIXED: Now correctly defined as GET"""
    link_ids = await app.state.redis.smembers("links")
    links = []
    
    for link_id in link_ids:
        link_data = await app.state.redis.hgetall(f"link:{link_id}")
        if link_data:
            links.append(link_data)
    
    return {"links": links, "count": len(links)}

@app.post("/api/links")
async def create_link(link: OpticalLinkCreate):
    """Create a new optical link"""
    if await app.state.redis.hexists(f"link:{link.link_id}", "link_id"):
        raise HTTPException(400, f"Link {link.link_id} already exists")
    
    # Check POPs exist
    if not await app.state.redis.hexists(f"pop:{link.pop_a}", "pop_id"):
        raise HTTPException(404, f"POP {link.pop_a} not found")
    if not await app.state.redis.hexists(f"pop:{link.pop_b}", "pop_id"):
        raise HTTPException(404, f"POP {link.pop_b} not found")
    
    link_data = {
        "link_id": link.link_id,
        "pop_a": link.pop_a,
        "pop_b": link.pop_b,
        "distance_km": link.distance_km,
        "fiber_type": link.fiber_type,
        "created_at": datetime.utcnow().isoformat()
    }
    
    await app.state.redis.hset(f"link:{link.link_id}", mapping=link_data)
    await app.state.redis.sadd("links", link.link_id)
    
    return {"message": "Link created", "link": link_data}

@app.post("/api/connections/allocate")
async def allocate_connection(request: ConnectionRequest):
    """Allocate frequency for a connection - FIXED: Simple implementation"""
    # Check if POPs exist
    if not await app.state.redis.hexists(f"pop:{request.pop_a}", "pop_id"):
        raise HTTPException(404, f"POP {request.pop_a} not found")
    if not await app.state.redis.hexists(f"pop:{request.pop_b}", "pop_id"):
        raise HTTPException(404, f"POP {request.pop_b} not found")
    
    # Simple frequency allocation (mock)
    frequency = 193100  # Mock frequency
    
    connection_data = {
        "connection_id": request.connection_id,
        "pop_a": request.pop_a,
        "pop_b": request.pop_b,
        "bandwidth": request.bandwidth,
        "virtual_operator": request.virtual_operator,
        "frequency": frequency,
        "status": "allocated",
        "created_at": datetime.utcnow().isoformat()
    }
    
    await app.state.redis.hset(f"connection:{request.connection_id}", mapping=connection_data)
    await app.state.redis.sadd("connections", request.connection_id)
    
    return {
        "message": "Connection allocated",
        "connection": connection_data,
        "frequency": frequency,
        "available_slots": [191300, 191350, 193100, 193150]  # Mock
    }

@app.get("/api/topology")
async def get_topology():
    """Get complete network topology"""
    # Get POPs
    pop_ids = await app.state.redis.smembers("pops")
    pops = []
    for pop_id in pop_ids:
        pop_data = await app.state.redis.hgetall(f"pop:{pop_id}")
        if pop_data:
            pops.append(pop_data)
    
    # Get links
    link_ids = await app.state.redis.smembers("links")
    links = []
    for link_id in link_ids:
        link_data = await app.state.redis.hgetall(f"link:{link_id}")
        if link_data:
            links.append(link_data)
    
    # Get connections
    connection_ids = await app.state.redis.smembers("connections")
    connections = []
    for conn_id in connection_ids:
        conn_data = await app.state.redis.hgetall(f"connection:{conn_id}")
        if conn_data:
            connections.append(conn_data)
    
    return {
        "pops": pops,
        "links": links,
        "connections": connections,
        "summary": {
            "total_pops": len(pops),
            "total_links": len(links),
            "total_connections": len(connections),
            "timestamp": datetime.utcnow().isoformat()
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
