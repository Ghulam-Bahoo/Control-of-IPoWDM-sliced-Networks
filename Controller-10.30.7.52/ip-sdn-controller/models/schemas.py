"""
Data Models for IP SDN Controller - Pydantic V2 compatible
"""

from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from enum import Enum
from datetime import datetime


# ============ Enumerations ============
class ConnectionStatus(str, Enum):
    PENDING = "PENDING"
    SETUP_IN_PROGRESS = "SETUP_IN_PROGRESS"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RECONFIGURING = "RECONFIGURING"
    TEARDOWN_IN_PROGRESS = "TEARDOWN_IN_PROGRESS"
    FAILED = "FAILED"
    TERMINATED = "TERMINATED"


class InterfaceStatus(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    ADMIN_DOWN = "ADMIN_DOWN"
    UNKNOWN = "UNKNOWN"


class ModulationFormat(str, Enum):
    DP_16QAM = "DP-16QAM"  # 400G ZR
    DP_QPSK = "DP-QPSK"
    DP_8QAM = "DP-8QAM"


class ReconfigReason(str, Enum):
    QOT_DEGRADATION = "QOT_DEGRADATION"
    MAINTENANCE = "MAINTENANCE"
    OPTIMIZATION = "OPTIMIZATION"
    FAILURE = "FAILURE"


# ============ Topology Models ============
class PopNode(BaseModel):
    pop_id: str
    name: str
    location: Optional[str] = None
    router_ids: List[str] = Field(default_factory=list)
    interfaces: List[str] = Field(default_factory=list)


class NetworkLink(BaseModel):
    link_id: str
    source_pop: str
    destination_pop: str
    length_km: float
    available_slots: List[int] = Field(default_factory=list)
    total_slots: int = 320  # Paper: C-band 4THz / 12.5GHz = 320 slots
    occupied_slots: Dict[str, List[int]] = Field(default_factory=dict)


# ============ Connection Models ============
class ConnectionRequest(BaseModel):
    """Request to setup end-to-end connection (Case 2 from paper)"""
    connection_id: Optional[str] = Field(default=None, description="Optional custom ID")
    source_pop: str = Field(..., description="Source POP ID")
    destination_pop: str = Field(..., description="Destination POP ID")
    source_interface: Optional[str] = Field(default=None, description="Source interface")
    destination_interface: Optional[str] = Field(default=None, description="Destination interface")
    bandwidth_gbps: float = Field(default=400.0, description="Required bandwidth in Gbps")
    modulation: ModulationFormat = Field(default=ModulationFormat.DP_16QAM)
    qos_requirements: Optional[Dict[str, Any]] = Field(default=None, description="QoS requirements")
    
    @field_validator('bandwidth_gbps')
    @classmethod
    def validate_bandwidth(cls, v):
        if v <= 0:
            raise ValueError("Bandwidth must be positive")
        if v > 400:
            raise ValueError("Bandwidth cannot exceed 400 Gbps")
        return v
    
    @field_validator('vop_id', check_fields=False)
    @classmethod
    def validate_vop_id(cls, v):
        if v:
            try:
                vop_num = int(v[3:])
                if vop_num <= 0:
                    raise ValueError("vOp number must be positive")
            except (ValueError, IndexError):
                raise ValueError("vOp ID must be in format 'vOpX' where X is a number")
        return v


class PathSegment(BaseModel):
    link_id: str
    source_pop: str
    destination_pop: str
    allocated_slots: List[int]
    slot_width_ghz: float = 12.5


class ConnectionResponse(BaseModel):
    """Response for connection setup"""
    connection_id: str
    status: ConnectionStatus
    source_pop: str
    destination_pop: str
    source_interface: Optional[str]
    destination_interface: Optional[str]
    path: List[PathSegment]
    bandwidth_gbps: float
    modulation: ModulationFormat
    setup_time: datetime
    estimated_osnr: Optional[float] = None
    message: Optional[str] = None


# ============ Agent Command Models ============
class AgentCommand(BaseModel):
    """Base model for agent commands"""
    action: str
    command_id: str
    target_pop: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SetupConnectionCommand(AgentCommand):
    """Command for setting up connection"""
    action: str = "setupConnection"
    parameters: Dict[str, Any] = Field(..., description="Connection parameters")


class ReconfigConnectionCommand(AgentCommand):
    """Command for reconfiguring connection (Case 3 from paper)"""
    action: str = "reconfigConnection"
    parameters: Dict[str, Any] = Field(..., description="Reconfiguration parameters")


class InterfaceControlCommand(AgentCommand):
    """Command for interface control"""
    action: str = "interfaceControl"
    parameters: Dict[str, Any] = Field(..., description="Interface control parameters")


# ============ Telemetry Models ============
class QoTelemetry(BaseModel):
    """QoT metrics from SONiC agents"""
    connection_id: str
    agent_id: str
    pop_id: str
    router_id: str
    interface: str
    timestamp: datetime
    osnr: Optional[float] = Field(default=None, description="OSNR in dB")
    pre_fec_ber: Optional[float] = Field(default=None, description="Pre-FEC BER")
    post_fec_ber: Optional[float] = Field(default=None, description="Post-FEC BER")
    tx_power: Optional[float] = Field(default=None, description="Tx power in dBm")
    rx_power: Optional[float] = Field(default=None, description="Rx power in dBm")
    temperature: Optional[float] = Field(default=None, description="Transceiver temperature")
    frequency: Optional[float] = Field(default=None, description="Frequency in GHz")
    
    @field_validator('osnr', 'tx_power', 'rx_power', 'temperature', 'frequency')
    @classmethod
    def validate_non_negative(cls, v, info):
        if v is not None and v < 0:
            raise ValueError(f"{info.field_name} cannot be negative")
        return v
    
    @field_validator('pre_fec_ber', 'post_fec_ber')
    @classmethod
    def validate_ber(cls, v, info):
        if v is not None and (v < 0 or v > 1):
            raise ValueError(f"{info.field_name} must be between 0 and 1")
        return v


# ============ API Response Models ============
class HealthCheckResponse(BaseModel):
    status: str
    timestamp: datetime
    controller_id: str
    virtual_operator: str
    kafka_connected: bool
    linkdb_connected: bool
    active_connections: int
    version: str


class TopologyResponse(BaseModel):
    pops: Dict[str, PopNode]
    links: Dict[str, NetworkLink]
    total_slots: int
    used_slots: int
    available_slots: int


class InterfaceStatusResponse(BaseModel):
    interface: str
    pop_id: str
    router_id: str
    status: InterfaceStatus
    assigned_vop: Optional[str]
    current_connection: Optional[str]
    last_updated: datetime


# ============ Reconfiguration Models ============
class ReconfigRequest(BaseModel):
    """Request for manual reconfiguration"""
    connection_id: str
    reason: ReconfigReason
    parameters: Optional[Dict[str, Any]] = None


class EndpointConfig(BaseModel):
    """Configuration for connection endpoint"""
    pop_id: str
    node_id: str
    port_id: str
    tx_power_level: float
    frequency_ghz: Optional[float] = None
    modulation: Optional[ModulationFormat] = None


# ============ Interface Assignment Models ============
class InterfaceAssignment(BaseModel):
    """Interface assignment for a specific POP."""
    pop_id: str = Field(..., description="POP identifier")
    router_id: str = Field(..., description="Router identifier")
    interfaces: List[str] = Field(..., description="List of interface names")
    
    @field_validator('interfaces')
    @classmethod
    def validate_interfaces(cls, v):
        if not v:
            raise ValueError("At least one interface must be specified")
        return v


class VOpActivationRequest(BaseModel):
    """Request to activate a new virtual operator."""
    vop_id: str = Field(
        ...,
        pattern=r'^vOp\d+$',
        description="Virtual operator ID (e.g., 'vOp2')"
    )
    tenant_name: str = Field(..., min_length=1, description="Tenant/operator name")
    description: Optional[str] = Field(default=None, description="Optional description")
    
    interface_assignments: List[InterfaceAssignment] = Field(
        ...,
        description="List of interface assignments per POP"
    )
    
    qos_requirements: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional QoS requirements"
    )


class VOpStatusResponse(BaseModel):
    """Response with virtual operator status."""
    vop_id: str
    tenant_name: str
    config_topic: str
    monitoring_topic: str
    controller_endpoint: Optional[str] = None
    assigned_interfaces: List[Dict[str, Any]]
    activation_time: datetime
    message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None


class TopicInfo(BaseModel):
    """Kafka topic information."""
    name: str
    partitions: int
    replication_factor: int
    configs: Dict[str, str] = Field(default_factory=dict)