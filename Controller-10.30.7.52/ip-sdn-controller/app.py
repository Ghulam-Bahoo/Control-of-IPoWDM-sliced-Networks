# app.py
"""
Main FastAPI application for IP SDN Controller
"""

import signal
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from utils.logger import setup_logging
from api.routers import router as api_router

from core.kafka_manager import KafkaManager
from core.agent_dispatcher import AgentDispatcher
from core.connection_manager import ConnectionManager
from core.linkdb_client import LinkDBClient
from core.path_computer import PathComputer
from core.qot_monitor import QoTMonitor

# Global instances (used by routers via imports or dependency access patterns)
kafka_manager: KafkaManager | None = None
agent_dispatcher: AgentDispatcher | None = None
connection_manager: ConnectionManager | None = None
qot_monitor: QoTMonitor | None = None


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

        # Start Kafka consumer loop (must be running before discovery/heartbeats)
        kafka_manager.start_consuming()

        # Optional: send initial discovery
        agent_dispatcher.broadcast_discovery()

        print("All components initialized")
        print("Controller ready to accept connections")

        yield

    finally:
        # Shutdown
        print("\nShutting down controller...")

        if kafka_manager:
            try:
                kafka_manager.stop_consuming()
            except Exception:
                pass
            try:
                kafka_manager.close()
            except Exception:
                pass
            print("Kafka manager stopped")

        print("Controller shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    setup_logging()

    app = FastAPI(
        title=settings.API_TITLE,
        version=settings.API_VERSION,
        description=f"IP SDN Controller for {settings.VIRTUAL_OPERATOR}",
        lifespan=lifespan,
    )

    # CORS (restrict in production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routers
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/")
    async def root():
        return {
            "service": settings.API_TITLE,
            "version": settings.API_VERSION,
            "virtual_operator": settings.VIRTUAL_OPERATOR,
            "controller_id": settings.CONTROLLER_ID,
            "docs": f"http://{settings.API_HOST}:{settings.API_PORT}/docs",
            "health": f"http://{settings.API_HOST}:{settings.API_PORT}/api/v1/health",
        }

    return app


def signal_handler(sig, frame):
    print(f"\nReceived signal {sig}, initiating graceful shutdown...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


if __name__ == "__main__":
    app = create_app()
    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="info",
        access_log=True,
    )

