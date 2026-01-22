"""
Connection Manager for IP SDN Controller
Manages connection lifecycle and state transitions
"""

import logging
import uuid
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from config.settings import settings
from models.schemas import (
    ConnectionRequest, ConnectionResponse, ConnectionStatus,
    PathSegment, QoTelemetry, ReconfigReason
)
from core.linkdb_client import LinkDBClient
from core.path_computer import PathComputer


logger = logging.getLogger(__name__)


class ConnectionEvent(str, Enum):
    """Events that trigger state transitions."""
    SETUP_REQUESTED = "SETUP_REQUESTED"
    SETUP_COMPLETED = "SETUP_COMPLETED"
    SETUP_FAILED = "SETUP_FAILED"
    DEGRADATION_DETECTED = "DEGRADATION_DETECTED"
    RECONFIG_REQUESTED = "RECONFIG_REQUESTED"
    RECONFIG_COMPLETED = "RECONFIG_COMPLETED"
    RECONFIG_FAILED = "RECONFIG_FAILED"
    TEARDOWN_REQUESTED = "TEARDOWN_REQUESTED"
    TEARDOWN_COMPLETED = "TEARDOWN_COMPLETED"
    TEARDOWN_FAILED = "TEARDOWN_FAILED"


@dataclass
class Connection:
    """Connection state container."""
    connection_id: str
    source_pop: str
    destination_pop: str
    source_interface: Optional[str]
    destination_interface: Optional[str]
    path: List[PathSegment]
    bandwidth_gbps: float
    modulation: str
    status: ConnectionStatus
    setup_time: datetime
    estimated_osnr: Optional[float]
    qot_history: List[QoTelemetry] = field(default_factory=list)
    last_reconfig_time: Optional[datetime] = None
    reconfig_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConnectionManager:
    """
    Manages connection lifecycle with state machine.
    """
    
    # State transition table: current_state -> event -> next_state
    STATE_TRANSITIONS = {
        ConnectionStatus.PENDING: {
            ConnectionEvent.SETUP_REQUESTED: ConnectionStatus.SETUP_IN_PROGRESS,
            ConnectionEvent.SETUP_FAILED: ConnectionStatus.FAILED,
        },
        ConnectionStatus.SETUP_IN_PROGRESS: {
            ConnectionEvent.SETUP_COMPLETED: ConnectionStatus.ACTIVE,
            ConnectionEvent.SETUP_FAILED: ConnectionStatus.FAILED,
            ConnectionEvent.TEARDOWN_REQUESTED: ConnectionStatus.TEARDOWN_IN_PROGRESS,
        },
        ConnectionStatus.ACTIVE: {
            ConnectionEvent.DEGRADATION_DETECTED: ConnectionStatus.DEGRADED,
            ConnectionEvent.RECONFIG_REQUESTED: ConnectionStatus.RECONFIGURING,
            ConnectionEvent.TEARDOWN_REQUESTED: ConnectionStatus.TEARDOWN_IN_PROGRESS,
        },
        ConnectionStatus.DEGRADED: {
            ConnectionEvent.RECONFIG_REQUESTED: ConnectionStatus.RECONFIGURING,
            ConnectionEvent.TEARDOWN_REQUESTED: ConnectionStatus.TEARDOWN_IN_PROGRESS,
        },
        ConnectionStatus.RECONFIGURING: {
            ConnectionEvent.RECONFIG_COMPLETED: ConnectionStatus.ACTIVE,
            ConnectionEvent.RECONFIG_FAILED: ConnectionStatus.DEGRADED,
            ConnectionEvent.TEARDOWN_REQUESTED: ConnectionStatus.TEARDOWN_IN_PROGRESS,
        },
        ConnectionStatus.TEARDOWN_IN_PROGRESS: {
            ConnectionEvent.TEARDOWN_COMPLETED: ConnectionStatus.TERMINATED,
            ConnectionEvent.TEARDOWN_FAILED: ConnectionStatus.FAILED,
        },
        ConnectionStatus.FAILED: {
            ConnectionEvent.TEARDOWN_REQUESTED: ConnectionStatus.TEARDOWN_IN_PROGRESS,
        },
        ConnectionStatus.TERMINATED: {
            # Terminal state - no transitions
        },
    }
    
    def __init__(self, linkdb_client: LinkDBClient, path_computer: PathComputer):
        self.linkdb = linkdb_client
        self.path_computer = path_computer
        self.connections: Dict[str, Connection] = {}
        self._load_existing_connections()
    
    def _load_existing_connections(self) -> None:
        """Load existing connections from Link Database."""
        try:
            # Get connection IDs from Link DB
            conn_ids = self.linkdb._client.smembers('connections') or []
            
            for conn_id in conn_ids:
                try:
                    conn_key = f"connection:{conn_id}"
                    conn_data = self.linkdb._client.hgetall(conn_key)
                    
                    if conn_data:
                        # Parse connection data
                        status = ConnectionStatus(conn_data.get('status', 'TERMINATED'))
                        
                        # Only load active/configured connections
                        if status not in [ConnectionStatus.TERMINATED, ConnectionStatus.FAILED]:
                            # Reconstruct connection object
                            connection = Connection(
                                connection_id=conn_id,
                                source_pop=conn_data.get('source_pop', ''),
                                destination_pop=conn_data.get('destination_pop', ''),
                                source_interface=conn_data.get('source_interface'),
                                destination_interface=conn_data.get('destination_interface'),
                                path=[],  # Would need to parse from JSON
                                bandwidth_gbps=float(conn_data.get('bandwidth_gbps', 0)),
                                modulation=conn_data.get('modulation', 'DP-16QAM'),
                                status=status,
                                setup_time=datetime.fromisoformat(conn_data.get('setup_time', '1970-01-01')),
                                estimated_osnr=float(conn_data['estimated_osnr']) if 'estimated_osnr' in conn_data else None
                            )
                            
                            self.connections[conn_id] = connection
                            logger.info(f"Loaded existing connection: {conn_id} ({status})")
                            
                except Exception as e:
                    logger.error(f"Failed to load connection {conn_id}: {e}")
            
            logger.info(f"Loaded {len(self.connections)} existing connections")
            
        except Exception as e:
            logger.error(f"Failed to load existing connections: {e}")
    
    def _transition_state(self, connection: Connection, event: ConnectionEvent) -> bool:
        """
        Attempt state transition based on event.
        
        Args:
            connection: Connection object
            event: Triggering event
            
        Returns:
            True if transition successful
        """
        current_state = connection.status
        transitions = self.STATE_TRANSITIONS.get(current_state, {})
        
        if event not in transitions:
            logger.warning(f"Invalid transition: {current_state} -> {event}")
            return False
        
        new_state = transitions[event]
        old_state = connection.status
        connection.status = new_state
        
        logger.info(f"Connection {connection.connection_id}: {old_state} -> {new_state} ({event})")
        return True
    
    def create_connection(self, request: ConnectionRequest) -> Optional[ConnectionResponse]:
        """
        Create a new connection (Case 2 from paper).
        
        Args:
            request: Connection request
            
        Returns:
            Connection response if successful, None otherwise
        """
        try:
            # Generate connection ID if not provided
            connection_id = request.connection_id or f"conn-{uuid.uuid4().hex[:8]}"
            
            # Validate path
            is_valid, message = self.path_computer.validate_path(
                request.source_pop, 
                request.destination_pop,
                request.source_interface,
                request.destination_interface
            )
            
            if not is_valid:
                logger.error(f"Path validation failed: {message}")
                return None
            
            # Compute complete path with spectrum allocation
            path_segments, error = self.path_computer.compute_complete_path(
                request.source_pop,
                request.destination_pop,
                request.bandwidth_gbps,
                request.modulation.value
            )
            
            if error:
                logger.error(f"Path computation failed: {error}")
                return None
            
            # Create connection object
            connection = Connection(
                connection_id=connection_id,
                source_pop=request.source_pop,
                destination_pop=request.destination_pop,
                source_interface=request.source_interface,
                destination_interface=request.destination_interface,
                path=path_segments,
                bandwidth_gbps=request.bandwidth_gbps,
                modulation=request.modulation.value,
                status=ConnectionStatus.PENDING,
                setup_time=datetime.utcnow(),
                estimated_osnr=self.path_computer.estimate_path_osnr(
                    [seg.link_id for seg in path_segments]
                ),
                metadata={
                    "qos_requirements": request.qos_requirements or {},
                    "created_by": "api",
                    "paper_case": "Case 2: Setup end-to-end connection"
                }
            )
            
            # Store in memory
            self.connections[connection_id] = connection
            
            # Create record in Link Database
            conn_data = {
                'source_pop': request.source_pop,
                'destination_pop': request.destination_pop,
                'source_interface': request.source_interface or '',
                'destination_interface': request.destination_interface or '',
                'bandwidth_gbps': str(request.bandwidth_gbps),
                'modulation': request.modulation.value,
                'status': ConnectionStatus.PENDING.value,
                'estimated_osnr': str(connection.estimated_osnr) if connection.estimated_osnr else '',
                'path_links': json.dumps([seg.link_id for seg in path_segments]),
                'details': json.dumps({
                    'paper_case': 'Case 2: Setup end-to-end connection',
                    'qos': request.qos_requirements or {}
                })
            }
            
            if not self.linkdb.create_connection_record(connection_id, conn_data):
                logger.error(f"Failed to create connection record in Link DB: {connection_id}")
                del self.connections[connection_id]
                return None
            
            # Allocate interfaces
            if request.source_interface:
                # Find router for source interface
                source_routers = self.path_computer.topology[request.source_pop].router_ids
                for router_id in source_routers:
                    if not self.linkdb.allocate_interface(
                        request.source_pop, router_id, 
                        request.source_interface, connection_id
                    ):
                        continue  # Try next router
                    break
            
            if request.destination_interface:
                # Find router for destination interface
                dest_routers = self.path_computer.topology[request.destination_pop].router_ids
                for router_id in dest_routers:
                    if not self.linkdb.allocate_interface(
                        request.destination_pop, router_id,
                        request.destination_interface, connection_id
                    ):
                        continue  # Try next router
                    break
            
            # Allocate spectrum slots
            for segment in path_segments:
                if segment.allocated_slots:
                    self.linkdb.allocate_spectrum_slots(
                        segment.link_id, connection_id, segment.allocated_slots
                    )
            
            # Transition to setup in progress
            self._transition_state(connection, ConnectionEvent.SETUP_REQUESTED)
            self.linkdb.update_connection_status(connection_id, connection.status.value)
            
            # Create response
            response = ConnectionResponse(
                connection_id=connection_id,
                status=connection.status,
                source_pop=connection.source_pop,
                destination_pop=connection.destination_pop,
                source_interface=connection.source_interface,
                destination_interface=connection.destination_interface,
                path=connection.path,
                bandwidth_gbps=connection.bandwidth_gbps,
                modulation=request.modulation,
                setup_time=connection.setup_time,
                estimated_osnr=connection.estimated_osnr,
                message=f"Connection created successfully. Ready for agent setup."
            )
            
            logger.info(f"Created connection {connection_id}: {request.source_pop} -> {request.destination_pop}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to create connection: {e}")
            return None
    
    def get_connection(self, connection_id: str) -> Optional[Connection]:
        """Get connection by ID."""
        return self.connections.get(connection_id)
    
    def get_connection_response(self, connection_id: str) -> Optional[ConnectionResponse]:
        """Get connection as API response."""
        connection = self.get_connection(connection_id)
        if not connection:
            return None
        
        return ConnectionResponse(
            connection_id=connection.connection_id,
            status=connection.status,
            source_pop=connection.source_pop,
            destination_pop=connection.destination_pop,
            source_interface=connection.source_interface,
            destination_interface=connection.destination_interface,
            path=connection.path,
            bandwidth_gbps=connection.bandwidth_gbps,
            modulation=connection.modulation,
            setup_time=connection.setup_time,
            estimated_osnr=connection.estimated_osnr,
            message=f"Status: {connection.status}"
        )
    
    def list_connections(self, status_filter: Optional[ConnectionStatus] = None) -> List[ConnectionResponse]:
        """List all connections, optionally filtered by status."""
        responses = []
        
        for connection in self.connections.values():
            if status_filter and connection.status != status_filter:
                continue
            
            response = ConnectionResponse(
                connection_id=connection.connection_id,
                status=connection.status,
                source_pop=connection.source_pop,
                destination_pop=connection.destination_pop,
                source_interface=connection.source_interface,
                destination_interface=connection.destination_interface,
                path=connection.path,
                bandwidth_gbps=connection.bandwidth_gbps,
                modulation=connection.modulation,
                setup_time=connection.setup_time,
                estimated_osnr=connection.estimated_osnr,
                message=f"Status: {connection.status}"
            )
            responses.append(response)
        
        return responses
    
    def update_connection_status(self, connection_id: str, 
                               status: ConnectionStatus, 
                               details: Optional[Dict] = None) -> bool:
        """
        Update connection status.
        
        Args:
            connection_id: Connection ID
            status: New status
            details: Additional details
            
        Returns:
            True if update successful
        """
        connection = self.get_connection(connection_id)
        if not connection:
            logger.error(f"Connection {connection_id} not found")
            return False
        
        old_status = connection.status
        connection.status = status
        
        # Update in Link DB
        success = self.linkdb.update_connection_status(connection_id, status.value, details)
        
        if success:
            logger.info(f"Updated connection {connection_id}: {old_status} -> {status}")
        
        return success
    
    def complete_setup(self, connection_id: str) -> bool:
        """
        Mark connection setup as completed.
        
        Args:
            connection_id: Connection ID
            
        Returns:
            True if successful
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return False
        
        if self._transition_state(connection, ConnectionEvent.SETUP_COMPLETED):
            return self.update_connection_status(
                connection_id, 
                connection.status,
                {"setup_completed_at": datetime.utcnow().isoformat()}
            )
        
        return False
    
    def mark_degraded(self, connection_id: str, telemetry: QoTelemetry) -> bool:
        """
        Mark connection as degraded based on QoT metrics.
        
        Args:
            connection_id: Connection ID
            telemetry: Degradation telemetry
            
        Returns:
            True if state transition successful
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return False
        
        # Add to QoT history
        connection.qot_history.append(telemetry)
        
        # Keep only last 100 readings
        if len(connection.qot_history) > 100:
            connection.qot_history = connection.qot_history[-100:]
        
        # Check if already degraded
        if connection.status == ConnectionStatus.DEGRADED:
            return True
        
        # Transition to degraded
        if self._transition_state(connection, ConnectionEvent.DEGRADATION_DETECTED):
            details = {
                "degradation_detected_at": datetime.utcnow().isoformat(),
                "degradation_metrics": {
                    "osnr": telemetry.osnr,
                    "pre_fec_ber": telemetry.pre_fec_ber,
                    "agent_id": telemetry.agent_id
                }
            }
            return self.update_connection_status(connection_id, connection.status, details)
        
        return False
    
    def start_reconfiguration(self, connection_id: str, reason: ReconfigReason) -> bool:
        """
        Start reconfiguration process (Case 3 from paper).
        
        Args:
            connection_id: Connection ID
            reason: Reason for reconfiguration
            
        Returns:
            True if state transition successful
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return False
        
        # Transition to reconfiguring
        if self._transition_state(connection, ConnectionEvent.RECONFIG_REQUESTED):
            connection.last_reconfig_time = datetime.utcnow()
            connection.reconfig_count += 1
            
            details = {
                "reconfig_started_at": datetime.utcnow().isoformat(),
                "reconfig_reason": reason.value,
                "reconfig_count": connection.reconfig_count
            }
            
            return self.update_connection_status(connection_id, connection.status, details)
        
        return False
    
    def complete_reconfiguration(self, connection_id: str) -> bool:
        """
        Mark reconfiguration as completed.
        
        Args:
            connection_id: Connection ID
            
        Returns:
            True if successful
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return False
        
        if self._transition_state(connection, ConnectionEvent.RECONFIG_COMPLETED):
            details = {
                "reconfig_completed_at": datetime.utcnow().isoformat(),
                "total_reconfig_count": connection.reconfig_count
            }
            return self.update_connection_status(connection_id, connection.status, details)
        
        return False
    
    def start_teardown(self, connection_id: str) -> bool:
        """
        Start connection teardown process.
        
        Args:
            connection_id: Connection ID
            
        Returns:
            True if state transition successful
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return False
        
        if self._transition_state(connection, ConnectionEvent.TEARDOWN_REQUESTED):
            details = {"teardown_started_at": datetime.utcnow().isoformat()}
            return self.update_connection_status(connection_id, connection.status, details)
        
        return False
    
    def complete_teardown(self, connection_id: str) -> bool:
        """
        Complete connection teardown and cleanup.
        
        Args:
            connection_id: Connection ID
            
        Returns:
            True if successful
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return False
        
        # Release spectrum slots
        for segment in connection.path:
            self.linkdb.release_spectrum_slots(segment.link_id, connection_id)
        
        # Release interfaces
        if connection.source_interface:
            # Find and release source interface
            source_routers = self.path_computer.topology[connection.source_pop].router_ids
            for router_id in source_routers:
                if self.linkdb.release_interface(connection.source_pop, router_id, 
                                               connection.source_interface):
                    break
        
        if connection.destination_interface:
            # Find and release destination interface
            dest_routers = self.path_computer.topology[connection.destination_pop].router_ids
            for router_id in dest_routers:
                if self.linkdb.release_interface(connection.destination_pop, router_id,
                                               connection.destination_interface):
                    break
        
        # Update state
        if self._transition_state(connection, ConnectionEvent.TEARDOWN_COMPLETED):
            # Delete from memory
            if connection_id in self.connections:
                del self.connections[connection_id]
            
            # Delete from Link DB
            self.linkdb.delete_connection_record(connection_id)
            
            logger.info(f"Completed teardown for connection {connection_id}")
            return True
        
        return False
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """
        Get connection statistics.
        
        Returns:
            Dictionary with statistics
        """
        stats = {
            "total_connections": len(self.connections),
            "by_status": {},
            "bandwidth_total_gbps": 0,
            "reconfig_count_total": 0,
            "active_since": {}
        }
        
        for conn in self.connections.values():
            # Count by status
            status = conn.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            
            # Total bandwidth
            if conn.status in [ConnectionStatus.ACTIVE, ConnectionStatus.DEGRADED, 
                             ConnectionStatus.RECONFIGURING]:
                stats["bandwidth_total_gbps"] += conn.bandwidth_gbps
            
            # Total reconfigurations
            stats["reconfig_count_total"] += conn.reconfig_count
            
            # Active duration for active connections
            if conn.status == ConnectionStatus.ACTIVE:
                duration = (datetime.utcnow() - conn.setup_time).total_seconds()
                stats["active_since"][conn.connection_id] = {
                    "duration_seconds": duration,
                    "setup_time": conn.setup_time.isoformat()
                }
        
        return stats
    
    def get_connection_qot_history(self, connection_id: str, 
                                  limit: int = 50) -> List[QoTelemetry]:
        """
        Get QoT history for a connection.
        
        Args:
            connection_id: Connection ID
            limit: Maximum number of records to return
            
        Returns:
            List of QoT telemetry records
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return []
        
        return connection.qot_history[-limit:]
    
    def health_check(self) -> Dict[str, Any]:
        """
        Health check for connection manager.
        
        Returns:
            Health status dictionary
        """
        return {
            "status": "healthy",
            "connections_count": len(self.connections),
            "linkdb_connected": self.linkdb.health_check(),
            "timestamp": datetime.utcnow().isoformat()
        }
