#!/usr/bin/env python3
"""
Main SONiC Agent Application
"""

import os
import sys
import signal
import threading
import time
import logging
import traceback
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

# Add app directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.settings import settings
from core.cmis_driver import CMISDriver, CMISConfiguration
from core.kafka_manager import KafkaManager
from core.telemetry_manager import TelemetryManager
from models.schemas import (
    AgentCommand, CommandResponse, TelemetryData,
    ConnectionConfig, HealthStatus
)


class AgentState(str, Enum):
    """Agent state enumeration."""
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass
class AgentMetrics:
    """Agent performance metrics."""
    messages_received: int = 0
    messages_processed: int = 0
    commands_failed: int = 0
    telemetry_sent: int = 0
    avg_processing_time: float = 0.0
    start_time: float = 0.0
    
    @property
    def uptime(self) -> float:
        """Get agent uptime in seconds."""
        return time.time() - self.start_time if self.start_time else 0.0
    
    @property
    def success_rate(self) -> float:
        """Get command success rate."""
        if self.messages_processed == 0:
            return 0.0
        return (self.messages_processed - self.commands_failed) / self.messages_processed * 100


class SonicAgent:
    """Main SONiC Agent class."""
    
    def __init__(self):
        """Initialize the SONiC Agent."""
        self.state = AgentState.STARTING
        self.metrics = AgentMetrics()
        self.metrics.start_time = time.time()
        
        # Initialize components
        self._setup_logging()
        self._setup_signal_handlers()
        
        self.logger.info("=" * 60)
        self.logger.info(f"Starting SONiC Agent: {settings.agent_id}")
        self.logger.info(f"Virtual Operator: {settings.virtual_operator}")
        self.logger.info(f"Config Topic: {settings.config_topic}")
        self.logger.info(f"Monitoring Topic: {settings.monitoring_topic}")
        self.logger.info("=" * 60)
        
        try:
            # Initialize hardware driver
            self.logger.info("Initializing CMIS driver...")
            self.cmis_driver = CMISDriver(settings.hardware_config)
            
            # Initialize Kafka manager
            self.logger.info("Initializing Kafka manager...")
            self.kafka_manager = KafkaManager(settings.kafka_config)
            
            # Initialize telemetry manager
            self.logger.info("Initializing telemetry manager...")
            self.telemetry_manager = TelemetryManager(
                cmis_driver=self.cmis_driver,
                kafka_manager=self.kafka_manager,
                interval_sec=settings.interval_sec,
                max_sessions=settings.max_telemetry_sessions
            )
            
            # Create stop event
            self.stop_event = threading.Event()
            
            # Create worker threads
            self.command_thread = threading.Thread(
                target=self._command_processor_loop,
                name="command-processor",
                daemon=True
            )
            
            self.health_thread = threading.Thread(
                target=self._health_monitor_loop,
                name="health-monitor",
                daemon=True
            )
            
            self.state = AgentState.RUNNING
            self.logger.info("Agent initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize agent: {e}", exc_info=True)
            self.state = AgentState.STOPPED
            raise
    
    def _setup_logging(self):
        """Setup logging configuration."""
        # Create log directory
        log_dir = os.path.dirname(settings.log_file)
        os.makedirs(log_dir, exist_ok=True)
        
        # Configure logging
        logging.basicConfig(
            level=getattr(logging, settings.log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Add file handler with rotation
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            settings.log_file,
            maxBytes=settings.log_max_size_mb * 1024 * 1024,
            backupCount=settings.log_backup_count
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        
        # Get logger
        self.logger = logging.getLogger(f"sonic-agent.{settings.agent_id}")
        self.logger.addHandler(file_handler)
        
        if settings.enable_debug:
            self.logger.setLevel(logging.DEBUG)
            self.logger.debug("Debug logging enabled")
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGHUP, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
    
    def _command_processor_loop(self):
        """Process incoming Kafka commands."""
        self.logger.info("Command processor started")
        
        while not self.stop_event.is_set():
            try:
                # Poll for messages
                messages = self.kafka_manager.poll_messages(
                    settings.config_topic,
                    timeout_ms=1000
                )
                
                for message in messages:
                    self.metrics.messages_received += 1
                    self._process_command(message)
                    
            except Exception as e:
                self.logger.error(f"Error in command processor: {e}")
                self.metrics.commands_failed += 1
                time.sleep(1)
        
        self.logger.info("Command processor stopped")
    
    def _process_command(self, message: Dict[str, Any]):
        """Process a single command."""
        start_time = time.time()
        command_id = message.get('command_id', 'unknown')
        
        try:
            # Parse command
            command = AgentCommand(**message)
            self.logger.info(f"Processing command: {command.action} (ID: {command_id})")
            
            # Check if this message is for us
            if command.target_pop and command.target_pop != settings.pop_id:
                self.logger.debug(f"Message filtered by POP: {command.target_pop}")
                return
            
            # Process based on action
            response = self._execute_command(command)
            self.metrics.messages_processed += 1
            
            # Send response
            self.kafka_manager.send_message(
                topic=settings.monitoring_topic,
                key=command_id,
                value=response.dict()
            )
            
            # Update metrics
            processing_time = time.time() - start_time
            self.metrics.avg_processing_time = (
                (self.metrics.avg_processing_time * (self.metrics.messages_processed - 1) + processing_time) /
                self.metrics.messages_processed
            )
            
            self.logger.info(f"Command processed: {command.action} in {processing_time:.3f}s")
            
        except Exception as e:
            self.logger.error(f"Failed to process command {command_id}: {e}", exc_info=True)
            self.metrics.commands_failed += 1
            
            # Send error response
            error_response = CommandResponse(
                status="error",
                command_id=command_id,
                agent_id=settings.agent_id,
                timestamp=time.time(),
                error=str(e)[:200]
            )
            
            self.kafka_manager.send_message(
                topic=settings.monitoring_topic,
                key=command_id,
                value=error_response.dict()
            )
    
    def _execute_command(self, command: AgentCommand) -> CommandResponse:
        """Execute a command and return response."""
        # Initialize response
        response = CommandResponse(
            status="success",
            command_id=command.command_id,
            agent_id=settings.agent_id,
            timestamp=time.time()
        )
        
        try:
            if command.action == "getInterfaceStatus":
                result = self.cmis_driver.get_interface_status(command.parameters.interface)
                response.data = {"status": result}
                
            elif command.action == "getPluggableInfo":
                result = self.cmis_driver.get_pluggable_info()
                response.data = {"pluggables": result}
                
            elif command.action == "setupConnection":
                config = ConnectionConfig(**command.parameters.dict())
                result = self.cmis_driver.setup_connection(config)
                response.data = {"connection": result}
                
                # Start telemetry for this connection
                if result.get("success"):
                    self.telemetry_manager.start_session(
                        connection_id=config.connection_id,
                        interfaces=config.endpoints
                    )
                
            elif command.action == "reconfigConnection":
                config = ConnectionConfig(**command.parameters.dict())
                result = self.cmis_driver.reconfig_connection(config)
                response.data = {"reconfiguration": result}
                
            elif command.action == "startTelemetry":
                connection_id = command.parameters.connection_id
                self.telemetry_manager.start_session(connection_id)
                response.data = {"telemetry_started": True}
                
            elif command.action == "stopTelemetry":
                connection_id = command.parameters.connection_id
                self.telemetry_manager.stop_session(connection_id)
                response.data = {"telemetry_stopped": True}
                
            elif command.action == "healthCheck":
                health = self.get_health_status()
                response.data = {"health": health.dict()}
                
            elif command.action == "interfaceControl":
                interface = command.parameters.interface
                state = command.parameters.admin_state
                result = self.cmis_driver.control_interface(interface, state)
                response.data = {"control_result": result}
                
            else:
                raise ValueError(f"Unknown action: {command.action}")
            
        except Exception as e:
            response.status = "error"
            response.error = str(e)
            self.logger.error(f"Command execution failed: {e}")
        
        return response
    
    def _health_monitor_loop(self):
        """Monitor agent health and send periodic updates."""
        self.logger.info("Health monitor started")
        last_health_check = 0
        
        while not self.stop_event.is_set():
            try:
                current_time = time.time()
                
                # Send health check every 30 seconds
                if current_time - last_health_check >= 30:
                    health_status = self.get_health_status()
                    
                    self.kafka_manager.send_message(
                        topic=settings.health_topic,
                        key=settings.agent_id,
                        value=health_status.dict()
                    )
                    
                    last_health_check = current_time
                    self.logger.debug(f"Health check sent: {health_status.status}")
                
                time.sleep(5)
                
            except Exception as e:
                self.logger.error(f"Health monitor error: {e}")
                time.sleep(10)
        
        self.logger.info("Health monitor stopped")
    
    def get_health_status(self) -> HealthStatus:
        """Get current health status of the agent."""
        try:
            # Check hardware
            hardware_ok = self.cmis_driver.check_health()
            
            # Check Kafka
            kafka_ok = self.kafka_manager.check_connection()
            
            # Check telemetry manager
            telemetry_ok = self.telemetry_manager.is_healthy()
            
            # Determine overall status
            if not hardware_ok:
                status = "degraded"
                issues = ["Hardware connectivity issues"]
            elif not kafka_ok:
                status = "degraded"
                issues = ["Kafka connectivity issues"]
            elif not telemetry_ok:
                status = "degraded"
                issues = ["Telemetry manager issues"]
            else:
                status = "healthy"
                issues = []
            
            return HealthStatus(
                agent_id=settings.agent_id,
                pop_id=settings.pop_id,
                router_id=settings.router_id,
                virtual_operator=settings.virtual_operator,
                status=status,
                uptime=self.metrics.uptime,
                messages_received=self.metrics.messages_received,
                messages_processed=self.metrics.messages_processed,
                success_rate=self.metrics.success_rate,
                telemetry_sessions=len(self.telemetry_manager.sessions),
                issues=issues,
                timestamp=time.time()
            )
            
        except Exception as e:
            self.logger.error(f"Failed to get health status: {e}")
            return HealthStatus(
                agent_id=settings.agent_id,
                pop_id=settings.pop_id,
                router_id=settings.router_id,
                virtual_operator=settings.virtual_operator,
                status="error",
                uptime=self.metrics.uptime,
                error=str(e),
                timestamp=time.time()
            )
    
    def run(self):
        """Run the agent main loop."""
        try:
            self.logger.info("Starting agent main loop...")
            
            # Start worker threads
            self.command_thread.start()
            self.health_thread.start()
            self.telemetry_manager.start()
            
            # Main loop - just wait for stop event
            while not self.stop_event.is_set():
                time.sleep(1)
                
                # Log metrics every minute
                if int(time.time()) % 60 == 0:
                    self.logger.info(
                        f"Metrics: received={self.metrics.messages_received}, "
                        f"processed={self.metrics.messages_processed}, "
                        f"success_rate={self.metrics.success_rate:.1f}%"
                    )
            
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        except Exception as e:
            self.logger.error(f"Agent runtime error: {e}", exc_info=True)
        finally:
            self.stop()
    
    def stop(self):
        """Stop the agent gracefully."""
        if self.state == AgentState.STOPPING or self.state == AgentState.STOPPED:
            return
        
        self.state = AgentState.STOPPING
        self.logger.info("Stopping agent...")
        
        # Set stop event
        self.stop_event.set()
        
        # Stop telemetry manager
        try:
            self.telemetry_manager.stop()
        except Exception as e:
            self.logger.error(f"Error stopping telemetry manager: {e}")
        
        # Wait for threads
        if self.command_thread.is_alive():
            self.command_thread.join(timeout=5)
        
        if self.health_thread.is_alive():
            self.health_thread.join(timeout=5)
        
        # Close Kafka connections
        try:
            self.kafka_manager.close()
        except Exception as e:
            self.logger.error(f"Error closing Kafka manager: {e}")
        
        # Send final health check
        try:
            final_health = self.get_health_status()
            final_health.status = "stopped"
            self.kafka_manager.send_message(
                topic=settings.health_topic,
                key=settings.agent_id,
                value=final_health.dict()
            )
        except Exception as e:
            self.logger.warning(f"Could not send final health check: {e}")
        
        self.state = AgentState.STOPPED
        self.logger.info("Agent stopped successfully")


def main():
    """Main entry point."""
    agent = None
    
    try:
        agent = SonicAgent()
        agent.run()
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        if agent:
            agent.stop()
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()
        if agent:
            agent.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
