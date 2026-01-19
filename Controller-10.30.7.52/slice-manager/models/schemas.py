from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, validator
from enum import Enum
from datetime import datetime
import re  # ADDED IMPORT


class VOpStatus(str, Enum):
    """Virtual Operator status enumeration."""
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    DEACTIVATING = "DEACTIVATING"
    INACTIVE = "INACTIVE" 


class InterfaceAssignment(BaseModel):
    """Interface assignment for a specific POP."""
    pop_id: str = Field(..., description="POP identifier (e.g., 'pop1')")
    router_id: str = Field(..., description="Router identifier (e.g., 'router1')")
    interfaces: List[str] = Field(
        ...,
        description="List of interface names (e.g., ['Ethernet48', 'Ethernet56'])"
    )
    
    @validator('interfaces')
    def validate_interfaces(cls, v):
        if not v:
            raise ValueError("At least one interface must be specified")
        return v


class VOpActivationRequest(BaseModel):
    """Request to activate a new virtual operator."""
    vop_id: str = Field(
        ...,
        pattern=r'^vOp\d+$',  # CHANGED: regex -> pattern
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
        description="Optional QoS requirements for future use"
    )
    
    @validator('vop_id')
    def validate_vop_id(cls, v):
        # Ensure vOp number is positive integer
        try:
            vop_num = int(v[3:])  # Extract number from 'vOpX'
            if vop_num <= 0:
                raise ValueError("vOp number must be positive")
        except (ValueError, IndexError):
            raise ValueError("vOp ID must be in format 'vOpX' where X is a number")
        return v


class VOpStatusResponse(BaseModel):
    """Response with virtual operator status."""
    vop_id: str
    tenant_name: str
    status: VOpStatus
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


class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str
    timestamp: datetime
    kafka_connected: bool
    linkdb_connected: bool
    version: str