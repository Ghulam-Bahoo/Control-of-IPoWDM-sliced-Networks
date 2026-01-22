"""
Agent Orchestrator - Main orchestrator for SONiC Agent
Implements all three study cases from the OFC paper
"""

import time
import threading
import logging
from typing import Dict, Any, Optional

from config.settings import settings
from core.cmis_driver import CMISDriver
from core.kafka_manager import KafkaManager
from core.telemetry_manager import TelemetryManager
from models.schemas import (
    AgentCapabilities, SetupConnectionCommand, TelemetrySample,
    HealthStatus, ErrorReport, QoTEvent
)


class AgentOrchestrator:
    """Main orchestrator for SONiC Agent."""
    
    def __init__(self):
        """Initialize agent orchestrator."""
        self.logger = logging.getLogger("agent-orchestrator")
        
        # Initialize components
        self.logger.info("Initializing agent components...")
        
        # CMIS Driver
        self.cmis_driver = CMISDriver(
            interface_mappings=settings.interface_mappings,
            mock_mode=settings.MOCK_HARDWARE
        )
        
        # Kafka Manager
        self.kafka_manager = KafkaManager(
            broker=settings.KAFKA_BROKER,
            config_topic=settings.CONFIG_TOPIC,
            monitoring_topic=settings.MONITORING_TOPIC
        )
        
        # Telemetry Manager
        self.telemetry_manager = TelemetryManager(
            cmis_driver=self.cmis_driver,
            kafka_manager=self.kafka_manager,
            interval_sec=settings.TELEMETRY_INTERVAL_SEC
        )
        
        # Active connections
        self.active_connections: Dict[str, Dict[str, Any]] = {}
        self.connection_lock = threading.RLock()
        
        # Agent state
        self.running = False
        self.stop_event = threading.Event()
        
        # Statistics
        self.start_time = time.time()
        self.commands_processed = 0
        self.commands_failed = 0
        
        self.logger.info("Agent orchestrator initialized")
    
    def run(self):
        """Run the main agent loop."""
        self.running = True
        self.logger.info("Starting agent main loop...")
        
        try:
            # Send initial capabilities (Case 1)
            self._send_capabilities()
            
            # Start telemetry manager
            self.telemetry_manager.start()
            
            # Send initial health check
            self._send_health_check()
            
            # Main command processing loop
            self._command_loop()
            
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        except Exception as e:
            self.logger.error(f"Agent runtime error: {e}", exc_info=True)
        finally:
            self.stop()
    
    def _command_loop(self):
        """Main command processing loop."""
        self.logger.info("Starting command processing loop...")
        
        last_health_check = time.time()
        
        while not self.stop_event.is_set():
            try:
                # Poll for commands
                messages = self.kafka_manager.poll_messages(timeout_ms=1000)
                
                for message in messages:
                    self._process_message(message.value)
                
                # Send periodic health check every 30 seconds
                current_time = time.time()
                if current_time - last_health_check >= 30:
                    self._send_health_check()
                    last_health_check = current_time
                
                # Log statistics every minute
                if int(current_time) % 60 == 0:
                    self.logger.info(
                        f"Agent stats: commands={self.commands_processed}, "
                        f"failed={self.commands_failed}, "
                        f"connections={len(self.active_connections)}"
                    )
                
            except Exception as e:
                self.logger.error(f"Error in command loop: {e}")
                time.sleep(1)
        
        self.logger.info("Command processing loop stopped")
    
    def _process_message(self, message: Dict[str, Any]):
        """Process incoming Kafka message."""
        try:
            message_type = message.get("type")
            action = message.get("action")
            
            self.logger.info(f"Processing message: type={message_type}, action={action}")
            
            if action == "setupConnection":
                self._handle_setup_connection(message)
            elif action == "teardownConnection":
                self._handle_teardown_connection(message)
            elif action == "reconfigConnection":
                self._handle_reconfig_connection(message)
            elif message_type == "healthCheck":
                self._handle_health_check(message)
            elif message_type == "getCapabilities":
                self._send_capabilities()
            else:
                self.logger.warning(f"Unknown message type: {message_type}")
                
            self.commands_processed += 1
            
        except Exception as e:
            self.commands_failed += 1
            self.logger.error(f"Failed to process message: {e}", exc_info=True)
            
            # Send error report
            error_report = ErrorReport(
                agent_id=settings.AGENT_ID,
                error_type="MessageProcessingError",
                error_message=str(e),
                command_id=message.get("command_id"),
                timestamp=time.time()
            )
            self.kafka_manager.send_monitoring_message(error_report.dict())
    
    def _handle_setup_connection(self, message: Dict[str, Any]):
        """Handle setupConnection command (Case 2 from paper)."""
        try:
            # Parse command
            command = SetupConnectionCommand(**message)
            self.logger.info(f"Setting up connection: {command.connection_id}")
            
            # Find endpoint for this agent
            for endpoint in command.endpoint_config:
                if (endpoint.get("pop_id") == settings.POP_ID and 
                    endpoint.get("node_id") == settings.ROUTER_ID):
                    
                    interface = endpoint.get("port_id")
                    app_code = endpoint.get("app")
                    tx_power = endpoint.get("tx_power_level")
                    
                    # Configure interface
                    result = self.cmis_driver.configure_interface(
                        interface=interface,
                        frequency_mhz=command.frequency,
                        app_code=app_code,
                        tx_power_dbm=tx_power
                    )
                    
                    if result["success"]:
                        # Store connection
                        with self.connection_lock:
                            self.active_connections[command.connection_id] = {
                                "interface": interface,
                                "frequency": command.frequency,
                                "app_code": app_code,
                                "tx_power": tx_power,
                                "configured_at": time.time()
                            }
                        
                        # Start telemetry session
                        session_id = self.telemetry_manager.start_session(
                            connection_id=command.connection_id,
                            interface=interface
                        )
                        
                        # Send success response
                        response = {
                            "type": "setupConnectionResult",
                            "connection_id": command.connection_id,
                            "agent_id": settings.AGENT_ID,
                            "success": True,
                            "interface": interface,
                            "session_id": session_id,
                            "timestamp": time.time()
                        }
                        self.kafka_manager.send_monitoring_message(response)
                        
                        self.logger.info(f"Connection {command.connection_id} setup successful on {interface}")
                    else:
                        # Send error response
                        error_response = {
                            "type": "setupConnectionResult",
                            "connection_id": command.connection_id,
                            "agent_id": settings.AGENT_ID,
                            "success": False,
                            "error": result.get("error", "Unknown error"),
                            "timestamp": time.time()
                        }
                        self.kafka_manager.send_monitoring_message(error_response)
                        
                        self.logger.error(f"Connection {command.connection_id} setup failed: {result.get('error')}")
                    
                    break  # Process only our endpoint
            
        except Exception as e:
            self.logger.error(f"Failed to handle setupConnection: {e}", exc_info=True)
            
            # Send error
            error_response = {
                "type": "error",
                "agent_id": settings.AGENT_ID,
                "error_type": "SetupConnectionError",
                "error_message": str(e),
                "command_id": message.get("command_id", "unknown"),
                "timestamp": time.time()
            }
            self.kafka_manager.send_monitoring_message(error_response)
    
    def _handle_teardown_connection(self, message: Dict[str, Any]):
        """Handle teardownConnection command."""
        connection_id = message.get("parameters", {}).get("connection_id")
        
        if not connection_id:
            self.logger.error("No connection_id in teardown command")
            return
        
        with self.connection_lock:
            if connection_id in self.active_connections:
                interface = self.active_connections[connection_id]["interface"]
                
                # Stop telemetry session
                # Note: Need to find session ID - for now, stop all sessions for this connection
                for session in self.telemetry_manager.get_active_sessions():
                    if session.connection_id == connection_id:
                        self.telemetry_manager.stop_session(session.session_id)
                
                # Remove connection
                del self.active_connections[connection_id]
                
                # Send response
                response = {
                    "type": "teardownConnectionResult",
                    "connection_id": connection_id,
                    "agent_id": settings.AGENT_ID,
                    "success": True,
                    "timestamp": time.time()
                }
                self.kafka_manager.send_monitoring_message(response)
                
                self.logger.info(f"Torn down connection {connection_id} on {interface}")
            else:
                self.logger.warning(f"Connection {connection_id} not found")
    
    def _handle_reconfig_connection(self, message: Dict[str, Any]):
        """Handle reconfigConnection command (Case 3 from paper)."""
        connection_id = message.get("parameters", {}).get("connection_id")
        tx_power = message.get("parameters", {}).get("tx_power_level")
        
        if not connection_id or tx_power is None:
            self.logger.error("Missing parameters in reconfig command")
            return
        
        with self.connection_lock:
            if connection_id in self.active_connections:
                interface = self.active_connections[connection_id]["interface"]
                
                # Adjust TX power
                result = self.cmis_driver.adjust_tx_power(interface, tx_power)
                
                if result["success"]:
                    # Update connection record
                    self.active_connections[connection_id]["tx_power"] = tx_power
                    
                    # Send response
                    response = {
                        "type": "reconfigConnectionResult",
                        "connection_id": connection_id,
                        "agent_id": settings.AGENT_ID,
                        "success": True,
                        "new_power": tx_power,
                        "timestamp": time.time()
                    }
                    self.kafka_manager.send_monitoring_message(response)
                    
                    self.logger.info(f"Reconfigured {connection_id} TX power to {tx_power}dBm")
                else:
                    # Send error
                    error_response = {
                        "type": "reconfigConnectionResult",
                        "connection_id": connection_id,
                        "agent_id": settings.AGENT_ID,
                        "success": False,
                        "error": result.get("error", "Unknown error"),
                        "timestamp": time.time()
                    }
                    self.kafka_manager.send_monitoring_message(error_response)
                    
                    self.logger.error(f"Reconfiguration failed for {connection_id}: {result.get('error')}")
            else:
                self.logger.warning(f"Connection {connection_id} not found")
    
    def _handle_health_check(self, message: Dict[str, Any]):
        """Handle health check request."""
        self._send_health_check()
    
    def _send_capabilities(self):
        """Send agent capabilities (Fig. 2a format)."""
        try:
            capabilities = AgentCapabilities(
                agent_id=settings.AGENT_ID,
                pop_id=settings.POP_ID,
                node_id=settings.ROUTER_ID,
                interfaces=[]
            )
            
            # Get capabilities for each assigned interface
            for interface in settings.ASSIGNED_TRANSCEIVERS:
                iface_caps = self.cmis_driver.get_capabilities(interface)
                capabilities.interfaces.append(iface_caps)
            
            # Send to monitoring topic
            self.kafka_manager.send_monitoring_message(capabilities.dict())
            
            self.logger.info(f"Sent capabilities for {len(capabilities.interfaces)} interfaces")
            
        except Exception as e:
            self.logger.error(f"Failed to send capabilities: {e}")
    
    def _send_health_check(self):
        """Send health check message."""
        try:
            # Get interface status
            interfaces = []
            for interface in settings.ASSIGNED_TRANSCEIVERS:
                status = self.cmis_driver.get_interface_status(interface)
                interfaces.append(status)
            
            # Determine overall health
            healthy_interfaces = sum(1 for i in interfaces if i.get("operational"))
            status = "healthy" if healthy_interfaces > 0 else "degraded"
            
            # Check Kafka connection
            if not self.kafka_manager.check_connection():
                status = "degraded"
            
            # Check telemetry manager
            if not self.telemetry_manager.is_healthy():
                status = "degraded"
            
            # Create health status
            health_status = HealthStatus(
                agent_id=settings.AGENT_ID,
                status=status,
                pop_id=settings.POP_ID,
                router_id=settings.ROUTER_ID,
                uptime=time.time() - self.start_time,
                interfaces=interfaces,
                issues=[] if status == "healthy" else ["Some components degraded"],
                timestamp=time.time()
            )
            
            # Send health check
            self.kafka_manager.send_health_message(health_status.dict())
            
            self.logger.debug(f"Sent health check: status={status}")
            
        except Exception as e:
            self.logger.error(f"Failed to send health check: {e}")
    
    def stop(self):
        """Stop the agent gracefully."""
        if not self.running:
            return
        
        self.logger.info("Stopping agent...")
        self.running = False
        self.stop_event.set()
        
        # Stop telemetry manager
        self.telemetry_manager.stop()
        
        # Close Kafka connections
        self.kafka_manager.close()
        
        # Send final health check
        try:
            final_health = HealthStatus(
                agent_id=settings.AGENT_ID,
                status="stopped",
                pop_id=settings.POP_ID,
                router_id=settings.ROUTER_ID,
                uptime=time.time() - self.start_time,
                interfaces=[],
                issues=["Agent stopped"],
                timestamp=time.time()
            )
            self.kafka_manager.send_health_message(final_health.dict())
        except Exception:
            pass
        
        self.logger.info("Agent stopped successfully")
