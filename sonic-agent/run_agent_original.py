#!/usr/bin/env python3
"""
Main entry point for SONiC Agent
"""

import os
import sys
import signal
import logging
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import settings
from core.agent_orchestrator import AgentOrchestrator


def setup_logging():
    """Setup logging configuration."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper())
    
    # Create log directory
    log_dir = os.path.dirname(settings.LOG_FILE)
    os.makedirs(log_dir, exist_ok=True)
    
    # Configure logging
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(settings.LOG_FILE),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    return logging.getLogger(__name__)


def signal_handler(signum, frame):
    """Handle termination signals."""
    logger = logging.getLogger(__name__)
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    sys.exit(0)


def main():
    """Main entry point."""
    # Setup logging
    logger = setup_logging()
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Log startup info
    logger.info("=" * 60)
    logger.info(f"Starting SONiC Agent: {settings.AGENT_ID}")
    logger.info(f"POP: {settings.POP_ID}, Router: {settings.ROUTER_ID}")
    logger.info(f"Virtual Operator: {settings.VIRTUAL_OPERATOR}")
    logger.info(f"Kafka Broker: {settings.KAFKA_BROKER}")
    logger.info(f"Config Topic: {settings.CONFIG_TOPIC}")
    logger.info(f"Monitoring Topic: {settings.MONITORING_TOPIC}")
    logger.info(f"Assigned Interfaces: {settings.ASSIGNED_TRANSCEIVERS}")
    logger.info("=" * 60)
    
    try:
        settings.validate_interface_mappings()
    except Exception:
        logger.exception("Failed to validate interface mappings")
    
    try:
        # Create and run agent
        agent = AgentOrchestrator()
        agent.run()
        
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

