"""
Module: Slice Manager REST API Server
Description: FastAPI application with complete REST endpoints
Author: AI Developer
Date: 2024
"""

import logging
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uvicorn
from datetime import datetime

from config.settings import settings
from core.slice_orchestrator import slice_orchestrator
from models.schemas import (
    VOpActivationRequest, VOpStatusResponse, HealthCheckResponse
)
from api.dependencies import get_slice_orchestrator


# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    # Startup
    logger.info(f"Starting {settings.API_TITLE} v{settings.API_VERSION}")
    logger.info(f"Listening on {settings.API_HOST}:{settings.API_PORT}")
    logger.info(f"Kafka Broker: {settings.KAFKA_BROKER}")
    logger.info(f"LinkDB: {settings.LINKDB_HOST}:{settings.LINKDB_PORT}")
    
    # Health check on startup
    health = slice_orchestrator.health_check()
    if not health['linkdb_connected']:
        logger.error("Link Database connection failed on startup!")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Slice Manager")
    slice_orchestrator.kafka_admin.close()
    slice_orchestrator.linkdb.close()


# Create FastAPI application
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description="Multi-tenant Slice Manager for SONiC Kafka-based SDN Control Plane",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Custom exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "path": request.url.path,
            "method": request.method
        }
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": str(exc) if settings.LOG_LEVEL == "DEBUG" else None
        }
    )


# Health check endpoint
@app.get("/health", response_model=HealthCheckResponse)
async def health_check(
    orchestrator=Depends(get_slice_orchestrator)
):
    """Health check endpoint for load balancers and monitoring."""
    health_info = orchestrator.health_check()
    
    return HealthCheckResponse(
        status="healthy" if health_info['linkdb_connected'] else "degraded",
        timestamp=health_info['timestamp'],
        kafka_connected=health_info['kafka_connected'],
        linkdb_connected=health_info['linkdb_connected'],
        version=settings.API_VERSION
    )


# Virtual operator management endpoints
@app.post("/api/v1/vops", response_model=VOpStatusResponse, status_code=status.HTTP_201_CREATED)
async def activate_virtual_operator(
    request: VOpActivationRequest,
    orchestrator=Depends(get_slice_orchestrator)
):
    """
    Activate a new virtual operator (Slice Manager's main function).
    
    This implements Case 1 from the paper: Activation of new virtual operator.
    """
    logger.info(f"Activation request received for {request.vop_id}")
    
    # Check if vOp already exists
    existing_status = orchestrator.get_vop_status(request.vop_id)
    if existing_status and existing_status.status != "FAILED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Virtual operator {request.vop_id} already exists"
        )
    
    # Activate the virtual operator
    response = orchestrator.activate_virtual_operator(request)
    
    if response.status == "FAILED":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=response.message
        )
    
    logger.info(f"Successfully activated {request.vop_id}")
    return response


@app.get("/api/v1/vops", response_model=list)
async def list_virtual_operators(
    orchestrator=Depends(get_slice_orchestrator)
):
    """List all active virtual operators."""
    vops = orchestrator.list_active_vops()
    return vops


@app.get("/api/v1/vops/{vop_id}", response_model=VOpStatusResponse)
async def get_virtual_operator(
    vop_id: str,
    orchestrator=Depends(get_slice_orchestrator)
):
    """Get status of a specific virtual operator."""
    status_info = orchestrator.get_vop_status(vop_id)
    
    if not status_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Virtual operator {vop_id} not found"
        )
    
    return status_info


@app.delete("/api/v1/vops/{vop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_virtual_operator(
    vop_id: str,
    orchestrator=Depends(get_slice_orchestrator)
):
    """Deactivate a virtual operator and release its resources."""
    logger.info(f"Deactivation request received for {vop_id}")
    
    # Check if vOp exists
    existing_status = orchestrator.get_vop_status(vop_id)
    if not existing_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Virtual operator {vop_id} not found"
        )
    
    # Deactivate in Link DB
    success = orchestrator.linkdb.deactivate_vop(vop_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deactivate {vop_id}"
        )
    
    # Delete Kafka topics
    try:
        orchestrator.kafka_admin.delete_vop_topics(vop_id)
    except Exception as e:
        logger.warning(f"Failed to delete topics for {vop_id}: {e}")
        # Continue deactivation even if topic deletion fails
    
    logger.info(f"Successfully deactivated {vop_id}")
    return None


# Kafka topic management endpoints
@app.post("/api/v1/topics/{vop_id}")
async def create_vop_topics(
    vop_id: str,
    orchestrator=Depends(get_slice_orchestrator)
):
    """Create Kafka topics for a virtual operator."""
    try:
        topic_info = orchestrator.kafka_admin.create_vop_topics(vop_id)
        return {
            "message": f"Topics created for {vop_id}",
            "topics": topic_info
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create topics: {str(e)}"
        )


# Utility endpoints
@app.get("/api/v1/interfaces/check/{pop_id}/{router_id}/{interface_name}")
async def check_interface_availability(
    pop_id: str,
    router_id: str,
    interface_name: str,
    orchestrator=Depends(get_slice_orchestrator)
):
    """Check if an interface is available for assignment."""
    is_available = orchestrator.linkdb.check_interface_availability(
        pop_id, router_id, interface_name
    )
    
    return {
        "pop_id": pop_id,
        "router_id": router_id,
        "interface": interface_name,
        "available": is_available,
        "timestamp": datetime.utcnow().isoformat()
    }


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "endpoints": {
            "health": "/health",
            "activate_vop": "POST /api/v1/vops",
            "list_vops": "GET /api/v1/vops",
            "get_vop": "GET /api/v1/vops/{vop_id}",
            "deactivate_vop": "DELETE /api/v1/vops/{vop_id}"
        },
        "documentation": "/docs",
        "openapi": "/openapi.json"
    }


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.LOG_LEVEL == "DEBUG"
    )
