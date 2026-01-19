"""
REST API routers for IP SDN Controller
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Path
from datetime import datetime

from config.settings import settings
from models.schemas import (
    ConnectionRequest, ConnectionResponse, ConnectionStatus,
    HealthCheckResponse, TopologyResponse, InterfaceStatusResponse,
    ReconfigRequest, VOpStatusResponse, ModulationFormat
)
from api.dependencies import (
    get_connection_manager, get_path_computer, get_linkdb,
    get_kafka_manager, get_agent_dispatcher, get_qot_monitor,
    check_controller_health
)
from core.connection_manager import ConnectionManager
from core.path_computer import PathComputer
from core.linkdb_client import LinkDBClient
from core.kafka_manager import KafkaManager
from core.agent_dispatcher import AgentDispatcher
from core.qot_monitor import QoTMonitor


router = APIRouter()


# ============ Health & Status Endpoints ============
@router.get("/health", response_model=HealthCheckResponse)
async def health_check(
    health_ok: bool = Depends(check_controller_health),
    linkdb: LinkDBClient = Depends(get_linkdb),
    kafka: KafkaManager = Depends(get_kafka_manager),
    conn_manager: ConnectionManager = Depends(get_connection_manager)
) -> HealthCheckResponse:
    """
    Health check endpoint.
    Returns controller health status and component connectivity.
    """
    try:
        # Get connection stats
        conn_stats = conn_manager.get_connection_stats()
        
        return HealthCheckResponse(
            status="healthy",
            timestamp=datetime.utcnow(),
            controller_id=settings.CONTROLLER_ID,
            virtual_operator=settings.VIRTUAL_OPERATOR,
            kafka_connected=kafka.health_check()['status'] != 'unhealthy',
            linkdb_connected=linkdb.health_check(),
            active_connections=conn_stats['total_connections'],
            version=settings.API_VERSION
        )
        
    except Exception as e:
        return HealthCheckResponse(
            status="unhealthy",
            timestamp=datetime.utcnow(),
            controller_id=settings.CONTROLLER_ID,
            virtual_operator=settings.VIRTUAL_OPERATOR,
            kafka_connected=False,
            linkdb_connected=False,
            active_connections=0,
            version=settings.API_VERSION,
            message=f"Error: {str(e)}"
        )


@router.get("/status")
async def get_status(
    conn_manager: ConnectionManager = Depends(get_connection_manager),
    kafka: KafkaManager = Depends(get_kafka_manager),
    agent_dispatcher: AgentDispatcher = Depends(get_agent_dispatcher),
    qot_monitor: QoTMonitor = Depends(get_qot_monitor)
) -> Dict[str, Any]:
    """
    Get detailed controller status.
    Includes component health, statistics, and operational metrics.
    """
    # Connection statistics
    conn_stats = conn_manager.get_connection_stats()
    
    # Kafka health
    kafka_health = kafka.health_check()
    
    # Agent status
    agent_status = agent_dispatcher.get_agent_status()
    
    # QoT status
    qot_status = qot_monitor.get_all_qot_status()
    
    return {
        "controller": {
            "id": settings.CONTROLLER_ID,
            "virtual_operator": settings.VIRTUAL_OPERATOR,
            "version": settings.API_VERSION,
            "api_port": settings.API_PORT,
            "timestamp": datetime.utcnow().isoformat()
        },
        "connections": conn_stats,
        "kafka": kafka_health,
        "agents": agent_status,
        "qot_monitoring": qot_status,
        "topology": {
            "pops_count": len(conn_manager.path_computer.topology),
            "links_count": len(conn_manager.path_computer.links)
        }
    }


# ============ Topology Endpoints ============
@router.get("/topology", response_model=TopologyResponse)
async def get_topology(
    linkdb: LinkDBClient = Depends(get_linkdb)
) -> TopologyResponse:
    """
    Get network topology.
    Returns all POPs and links with current status.
    """
    pops, links = linkdb.get_topology()
    
    # Calculate slot usage
    total_slots = sum(link.total_slots for link in links.values())
    used_slots = sum(
        len(slots) 
        for link in links.values() 
        for slots in link.occupied_slots.values()
    )
    
    return TopologyResponse(
        pops=pops,
        links=links,
        total_slots=total_slots,
        used_slots=used_slots,
        available_slots=total_slots - used_slots
    )


@router.get("/topology/pops")
async def get_pops(
    linkdb: LinkDBClient = Depends(get_linkdb)
) -> Dict[str, Any]:
    """Get all POPs in the network."""
    pops, _ = linkdb.get_topology()
    return {"pops": pops}


@router.get("/topology/links")
async def get_links(
    linkdb: LinkDBClient = Depends(get_linkdb)
) -> Dict[str, Any]:
    """Get all links in the network."""
    _, links = linkdb.get_topology()
    return {"links": links}


@router.get("/topology/path/{source_pop}/{destination_pop}")
async def compute_path(
    source_pop: str = Path(..., description="Source POP ID"),
    destination_pop: str = Path(..., description="Destination POP ID"),
    bandwidth_gbps: float = Query(400.0, description="Required bandwidth in Gbps"),
    modulation: ModulationFormat = Query(ModulationFormat.DP_16QAM, description="Modulation format"),
    path_computer: PathComputer = Depends(get_path_computer)
) -> Dict[str, Any]:
    """
    Compute path between two POPs.
    Returns path details including spectrum slot allocation.
    """
    # Validate POPs exist
    if source_pop not in path_computer.topology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source POP {source_pop} not found"
        )
    
    if destination_pop not in path_computer.topology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Destination POP {destination_pop} not found"
        )
    
    # Compute path
    path_segments, error = path_computer.compute_complete_path(
        source_pop, destination_pop, bandwidth_gbps, modulation.value
    )
    
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path computation failed: {error}"
        )
    
    # Estimate OSNR
    estimated_osnr = path_computer.estimate_path_osnr(
        [seg.link_id for seg in path_segments]
    )
    
    return {
        "source_pop": source_pop,
        "destination_pop": destination_pop,
        "bandwidth_gbps": bandwidth_gbps,
        "modulation": modulation.value,
        "path_segments": path_segments,
        "estimated_osnr": estimated_osnr,
        "total_hops": len(path_segments),
        "total_length_km": sum(
            path_computer.links[seg.link_id].length_km 
            for seg in path_segments 
            if seg.link_id in path_computer.links
        )
    }


# ============ Connection Management Endpoints ============
@router.post("/connections", response_model=ConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_connection(
    request: ConnectionRequest,
    conn_manager: ConnectionManager = Depends(get_connection_manager)
) -> ConnectionResponse:
    """
    Create a new end-to-end connection (Case 2 from paper).
    
    Establishes a connection between source and destination POPs
    with specified bandwidth and modulation format.
    """
    # Validate request
    if request.source_pop == request.destination_pop:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and destination POPs cannot be the same"
        )
    
    # Create connection
    response = conn_manager.create_connection(request)
    
    if not response:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create connection. Check available resources."
        )
    
    return response


@router.get("/connections", response_model=List[ConnectionResponse])
async def list_connections(
    status: Optional[ConnectionStatus] = Query(None, description="Filter by connection status"),
    conn_manager: ConnectionManager = Depends(get_connection_manager)
) -> List[ConnectionResponse]:
    """
    List all connections.
    Optionally filter by connection status.
    """
    return conn_manager.list_connections(status_filter=status)


@router.get("/connections/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: str = Path(..., description="Connection ID"),
    conn_manager: ConnectionManager = Depends(get_connection_manager)
) -> ConnectionResponse:
    """
    Get connection details by ID.
    """
    response = conn_manager.get_connection_response(connection_id)
    
    if not response:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection {connection_id} not found"
        )
    
    return response


@router.post("/connections/{connection_id}/setup")
async def setup_connection(
    connection_id: str = Path(..., description="Connection ID"),
    conn_manager: ConnectionManager = Depends(get_connection_manager),
    agent_dispatcher: AgentDispatcher = Depends(get_agent_dispatcher)
) -> Dict[str, Any]:
    """
    Complete connection setup by sending commands to agents.
    
    This endpoint would:
    1. Send setup commands to source and destination agents
    2. Update connection status to ACTIVE
    3. Return command execution results
    """
    # Get connection
    connection = conn_manager.get_connection(connection_id)
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection {connection_id} not found"
        )
    
    # Check if connection is in setup state
    if connection.status != ConnectionStatus.SETUP_IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connection is in {connection.status} state, not SETUP_IN_PROGRESS"
        )
    
    # TODO: Implement actual agent command dispatch
    # For now, just mark as active
    if conn_manager.complete_setup(connection_id):
        return {
            "connection_id": connection_id,
            "status": "setup_completed",
            "message": "Connection setup marked as completed",
            "next_step": "Send actual commands to agents"
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete connection setup"
        )


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: str = Path(..., description="Connection ID"),
    conn_manager: ConnectionManager = Depends(get_connection_manager)
) -> Dict[str, Any]:
    """
    Delete a connection (initiate teardown).
    
    Starts the connection teardown process, which includes:
    1. Releasing spectrum slots
    2. Releasing interfaces
    3. Removing connection record
    """
    # Check if connection exists
    connection = conn_manager.get_connection(connection_id)
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection {connection_id} not found"
        )
    
    # Start teardown
    if conn_manager.start_teardown(connection_id):
        # In production, you would wait for agent confirmation
        # For now, complete immediately
        conn_manager.complete_teardown(connection_id)
        
        return {
            "connection_id": connection_id,
            "status": "deleted",
            "message": "Connection teardown completed"
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start connection teardown"
        )


# ============ QoT Monitoring Endpoints ============
@router.post("/connections/{connection_id}/reconfigure")
async def reconfigure_connection(
    connection_id: str = Path(..., description="Connection ID"),
    request: ReconfigRequest = None,
    conn_manager: ConnectionManager = Depends(get_connection_manager),
    qot_monitor: QoTMonitor = Depends(get_qot_monitor)
) -> Dict[str, Any]:
    """
    Manually trigger connection reconfiguration (Case 3 from paper).
    
    Can be used for:
    - Manual QoT degradation response
    - Maintenance reconfiguration
    - Optimization adjustments
    """
    # Get connection
    connection = conn_manager.get_connection(connection_id)
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection {connection_id} not found"
        )
    
    # Determine reconfiguration reason
    reason = request.reason if request else ReconfigReason.MAINTENANCE
    
    # Start reconfiguration
    if conn_manager.start_reconfiguration(connection_id, reason):
        return {
            "connection_id": connection_id,
            "status": "reconfiguration_started",
            "reason": reason.value,
            "message": "Reconfiguration process started"
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start reconfiguration. Connection is in {connection.status} state."
        )


@router.get("/connections/{connection_id}/qot")
async def get_connection_qot(
    connection_id: str = Path(..., description="Connection ID"),
    qot_monitor: QoTMonitor = Depends(get_qot_monitor)
) -> Dict[str, Any]:
    """
    Get QoT metrics and monitoring status for a connection.
    
    Returns:
    - Recent QoT samples
    - Degradation level
    - Reconfiguration history
    - Current monitoring status
    """
    qot_status = qot_monitor.get_connection_qot_status(connection_id)
    
    if not qot_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No QoT data for connection {connection_id}"
        )
    
    return qot_status


@router.get("/monitoring/qot")
async def get_all_qot_status(
    qot_monitor: QoTMonitor = Depends(get_qot_monitor)
) -> Dict[str, Any]:
    """Get QoT monitoring status for all connections."""
    return qot_monitor.get_all_qot_status()


# ============ Agent Management Endpoints ============
@router.get("/agents")
async def get_agents(
    agent_dispatcher: AgentDispatcher = Depends(get_agent_dispatcher)
) -> Dict[str, Any]:
    """Get status of all SONiC agents."""
    return agent_dispatcher.get_agent_status()


@router.post("/agents/discover")
async def discover_agents(
    agent_dispatcher: AgentDispatcher = Depends(get_agent_dispatcher)
) -> Dict[str, Any]:
    """
    Broadcast discovery message to find all agents.
    
    Sends a discovery command to all Kafka topics to identify
    available SONiC agents and their capabilities.
    """
    success = agent_dispatcher.broadcast_discovery()
    
    return {
        "status": "success" if success else "failed",
        "message": "Discovery broadcast sent" if success else "Failed to send discovery",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/agents/{pop_id}/{router_id}")
async def get_agent(
    pop_id: str = Path(..., description="POP ID"),
    router_id: str = Path(..., description="Router ID"),
    agent_dispatcher: AgentDispatcher = Depends(get_agent_dispatcher)
) -> Dict[str, Any]:
    """
    Get agent information for specific POP and router.
    """
    agent = agent_dispatcher.get_agent(pop_id, router_id)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No agent found for {pop_id}/{router_id}"
        )
    
    return {
        "agent_id": agent.agent_id,
        "pop_id": agent.pop_id,
        "router_id": agent.router_id,
        "status": agent.status.value,
        "is_online": agent.is_online,
        "last_heartbeat": agent.last_heartbeat.isoformat() if agent.last_heartbeat else None,
        "capabilities": agent.capabilities,
        "interfaces": agent.interfaces
    }


# ============ Interface Management Endpoints ============
@router.get("/interfaces/{pop_id}/{router_id}")
async def get_interfaces(
    pop_id: str = Path(..., description="POP ID"),
    router_id: str = Path(..., description="Router ID"),
    linkdb: LinkDBClient = Depends(get_linkdb)
) -> List[InterfaceStatusResponse]:
    """
    Get interface status for a specific POP and router.
    
    Returns:
    - List of interfaces with their current status
    - Assigned virtual operator
    - Current connection assignment
    """
    # TODO: Implement actual interface status retrieval
    # For now, return empty list
    return []


@router.post("/interfaces/{pop_id}/{router_id}/{interface}/control")
async def control_interface(
    pop_id: str = Path(..., description="POP ID"),
    router_id: str = Path(..., description="Router ID"),
    interface: str = Path(..., description="Interface name"),
    action: str = Query(..., description="Action: up, down, admin_down"),
    agent_dispatcher: AgentDispatcher = Depends(get_agent_dispatcher)
) -> Dict[str, Any]:
    """
    Control interface state (up/down/admin_down).
    
    Sends interface control command to the agent managing
    the specified POP, router, and interface.
    """
    success = agent_dispatcher.dispatch_interface_command(
        action=action,
        pop_id=pop_id,
        router_id=router_id,
        interface=interface
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send interface control command for {pop_id}/{router_id}/{interface}"
        )
    
    return {
        "pop_id": pop_id,
        "router_id": router_id,
        "interface": interface,
        "action": action,
        "status": "command_sent",
        "message": f"Interface {action} command sent to agent",
        "timestamp": datetime.utcnow().isoformat()
    }


# ============ Virtual Operator Endpoints ============
@router.get("/vop/status", response_model=VOpStatusResponse)
async def get_vop_status() -> VOpStatusResponse:
    """
    Get virtual operator status.
    
    Returns configuration and status for this vOp2 controller.
    """
    return VOpStatusResponse(
        vop_id=settings.VIRTUAL_OPERATOR,
        tenant_name=f"Tenant_{settings.VIRTUAL_OPERATOR}",
        config_topic=settings.CONFIG_TOPIC,
        monitoring_topic=settings.MONITORING_TOPIC,
        controller_endpoint=f"http://10.30.7.52:{settings.API_PORT}",
        assigned_interfaces=[],  # Would come from Slice Manager
        activation_time=datetime.utcnow(),  # Would be actual activation time
        message="vOp2 controller operational"
    )