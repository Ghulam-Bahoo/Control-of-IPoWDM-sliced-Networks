"""
Main FastAPI application for IP SDN Controller
"""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import signal
import sys

from config.settings import settings
from utils.logger import setup_logging
from api.routers import router as api_router
from core.kafka_manager import KafkaManager
from core.agent_dispatcher import AgentDispatcher
from core.connection_manager import ConnectionManager
from core.linkdb_client import LinkDBClient
from core.path_computer import PathComputer
from core.qot_monitor import QoTMonitor


# Global instances
kafka_manager = None
agent_dispatcher = None
connection_manager = None
qot_monitor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI.
    Handles startup and shutdown events.
    """
    global kafka_manager, agent_dispatcher, connection_manager, qot_monitor
    
    # Startup
    print(f"Starting IP SDN Controller for {settings.VIRTUAL_OPERATOR}")
    print(f"API: http://{settings.API_HOST}:{settings.API_PORT}")
    print(f"Kafka: {settings.KAFKA_BROKER}")
    print(f"Link DB: {settings.LINKDB_HOST}:{settings.LINKDB_PORT}")
    
    try:
        # Initialize core components
        linkdb = LinkDBClient()
        path_computer = PathComputer(linkdb)
        connection_manager = ConnectionManager(linkdb, path_computer)
        kafka_manager = KafkaManager()
        agent_dispatcher = AgentDispatcher(kafka_manager)
        qot_monitor = QoTMonitor(connection_manager, kafka_manager, agent_dispatcher)
        
        # Start Kafka consumer
        kafka_manager.start_consuming()
        
        # Send initial discovery
        agent_dispatcher.broadcast_discovery()
        
        print("All components initialized")
        print("Controller ready to accept connections")
        
        yield
        
    finally:
        # Shutdown
        print("\nShutting down controller...")
        
        if kafka_manager:
            kafka_manager.stop_consuming()
            kafka_manager.close()
            print("Kafka manager stopped")
        
        if connection_manager:
            # Clean up connections
            print("Connection manager cleaned up")
        
        print("Controller shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    
    # Setup logging
    setup_logging()
    
    # Create FastAPI app with lifespan
    app = FastAPI(
        title=settings.API_TITLE,
        version=settings.API_VERSION,
        description="IP SDN Controller for vOp2 - Implements all paper functionalities",
        lifespan=lifespan
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict this
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include API routers
    app.include_router(api_router, prefix="/api/v1")
    
    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "service": settings.API_TITLE,
            "version": settings.API_VERSION,
            "virtual_operator": settings.VIRTUAL_OPERATOR,
            "controller_id": settings.CONTROLLER_ID,
            "docs": f"http://{settings.API_HOST}:{settings.API_PORT}/docs",
            "health": f"http://{settings.API_HOST}:{settings.API_PORT}/api/v1/health",
            "paper_implementation": {
                "case_1": "vOp Activation - Done by Slice Manager",
                "case_2": "Connection Setup - Complete",
                "case_3": "QoT Reconfiguration - Complete",
                "kafka_communication": "Complete",
                "rest_api": "Complete"
            }
        }
    
    return app


# Signal handler for graceful shutdown
def signal_handler(sig, frame):
    print(f"\nReceived signal {sig}, initiating graceful shutdown...")
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


if __name__ == "__main__":
    # Create app
    app = create_app()
    
    # Run server
    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="info",
        access_log=True
    )