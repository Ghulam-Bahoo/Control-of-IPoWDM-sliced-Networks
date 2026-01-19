"""
FastAPI dependencies for IP SDN Controller
"""

from typing import Generator
import logging

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from config.settings import settings
from core.linkdb_client import LinkDBClient
from core.path_computer import PathComputer
from core.connection_manager import ConnectionManager
from core.kafka_manager import KafkaManager
from core.agent_dispatcher import AgentDispatcher
from core.qot_monitor import QoTMonitor


logger = logging.getLogger(__name__)


# Dependency: Link Database Client
def get_linkdb() -> Generator[LinkDBClient, None, None]:
    """Get Link Database client instance."""
    linkdb = LinkDBClient()
    try:
        yield linkdb
    finally:
        linkdb.close()


# Dependency: Path Computer
def get_path_computer(linkdb: LinkDBClient = Depends(get_linkdb)) -> PathComputer:
    """Get Path Computer instance."""
    return PathComputer(linkdb)


# Dependency: Connection Manager
def get_connection_manager(
    linkdb: LinkDBClient = Depends(get_linkdb),
    path_computer: PathComputer = Depends(get_path_computer)
) -> ConnectionManager:
    """Get Connection Manager instance."""
    return ConnectionManager(linkdb, path_computer)


# Dependency: Kafka Manager
def get_kafka_manager() -> Generator[KafkaManager, None, None]:
    """Get Kafka Manager instance."""
    kafka = KafkaManager()
    try:
        yield kafka
    finally:
        kafka.close()


# Dependency: Agent Dispatcher
def get_agent_dispatcher(
    kafka: KafkaManager = Depends(get_kafka_manager)
) -> AgentDispatcher:
    """Get Agent Dispatcher instance."""
    return AgentDispatcher(kafka)


# Dependency: QoT Monitor
def get_qot_monitor(
    connection_manager: ConnectionManager = Depends(get_connection_manager),
    kafka: KafkaManager = Depends(get_kafka_manager),
    agent_dispatcher: AgentDispatcher = Depends(get_agent_dispatcher)
) -> QoTMonitor:
    """Get QoT Monitor instance."""
    return QoTMonitor(connection_manager, kafka, agent_dispatcher)


# Dependency: Check if controller is healthy
def check_controller_health(
    linkdb: LinkDBClient = Depends(get_linkdb),
    kafka: KafkaManager = Depends(get_kafka_manager)
) -> bool:
    """Check if controller components are healthy."""
    try:
        # Check Link DB
        if not linkdb.health_check():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Link Database unavailable"
            )
        
        # Check Kafka
        kafka_health = kafka.health_check()
        if kafka_health['status'] == 'unhealthy':
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Kafka unavailable: {kafka_health.get('error')}"
            )
        
        return True
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Health check failed: {str(e)}"
        )