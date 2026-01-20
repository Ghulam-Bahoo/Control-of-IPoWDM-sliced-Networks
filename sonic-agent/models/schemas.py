"""
Data schemas for SONiC Agent (following OFC paper specification)
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, validator


class MessageType(str, Enum):
    """Message types as per OFC paper."""
    CAPABILITIES = "agentCapabilities"
    SETUP_CONNECTION = "setupConnection"
    TEARDOWN_CONNECTION = "teardownConnection"
    RECONFIG_CONNECTION = "reconfigConnection"
    TELEMETRY = "telemetrySample"
    HEALTH = "healthCheck"
    ERROR = "error"


class AgentCapabilities(BaseModel):
    """Agent capabilities message (Fig. 2a in paper)."""
    type: str = MessageType.CAPABILITIES
    agent_id: str
    pop_id: str
    node_id: str
    interfaces: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    
    class Config:
        schema_extra = {
            "example": {
                "type": "agentCapabilities",
                "agent_id": "pop1-router1",
                "pop_id": "pop1",
                "node_id": "router1",
                "interfaces": [
                    {
                        "port_id": "Ethernet192",
                        "type": "ZR",
                        "app_code": {
                            "1": {"rate": "400G", "mode": "DWDM-amplified"},
                            "2": {"rate": "400G", "mode": "OFEC-16QAM"}
                        },
                        "frequency_range": [191300, 196100],
                        "tx_power_range": [-15.0, -8.0]
                    }
                ],
                "timestamp": 1672531200.0
            }
        }


class SetupConnectionCommand(BaseModel):
    """Setup connection command (Fig. 2b in paper)."""
    action: str = "setupConnection"
    connection_id: str
    frequency: int = Field(..., ge=191300, le=196100)
    endpoint_config: List[Dict[str, Any]]
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    
    @validator('endpoint_config')
    def validate_endpoints(cls, v):
        if not v or len(v) < 2:
            raise ValueError("At least two endpoints required")
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "action": "setupConnection",
                "connection_id": "POP1-POP2",
                "frequency": 193400,
                "endpoint_config": [
                    {
                        "pop_id": "POP1",
                        "node_id": "R1.1",
                        "port_id": "Ethernet192",
                        "app": "1",
                        "tx_power_level": -2.0
                    },
                    {
                        "pop_id": "POP2",
                        "node_id": "R2.1",
                        "port_id": "Ethernet192",
                        "app": "1",
                        "tx_power_level": -2.0
                    }
                ],
                "timestamp": 1672531200.0
            }
        }


class TelemetrySample(BaseModel):
    """Telemetry sample (Fig. 2c in paper)."""
    type: str = MessageType.TELEMETRY
    connection_id: str
    agent_id: str
    interface: str
    timestamp: float
    fields: Dict[str, Any]
    
    class Config:
        schema_extra = {
            "example": {
                "type": "telemetrySample",
                "connection_id": "POP1-POP2",
                "agent_id": "pop1-router1",
                "interface": "Ethernet192",
                "timestamp": 1672531200.5,
                "fields": {
                    "rx_power": -9.2,
                    "ber": 1.4e-5,
                    "osnr": 23.9,
                    "tx_power": -2.1,
                    "temperature": 45.2
                }
            }
        }


class HealthStatus(BaseModel):
    """Health status message."""
    type: str = MessageType.HEALTH
    agent_id: str
    status: str = Field(..., pattern="^(healthy|degraded|error)$")
    pop_id: str
    router_id: str
    uptime: float
    interfaces: List[Dict[str, Any]] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    
    class Config:
        schema_extra = {
            "example": {
                "type": "healthCheck",
                "agent_id": "pop1-router1",
                "status": "healthy",
                "pop_id": "pop1",
                "router_id": "router1",
                "uptime": 86400.5,
                "interfaces": [
                    {
                        "port_id": "Ethernet192",
                        "present": True,
                        "operational": True,
                        "frequency": 193100
                    }
                ],
                "issues": [],
                "timestamp": 1672531200.0
            }
        }


class ErrorReport(BaseModel):
    """Error report message."""
    type: str = MessageType.ERROR
    agent_id: str
    error_type: str
    error_message: str
    command_id: Optional[str] = None
    interface: Optional[str] = None
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    
    class Config:
        schema_extra = {
            "example": {
                "type": "error",
                "agent_id": "pop1-router1",
                "error_type": "CMISWriteError",
                "error_message": "Failed to write frequency register",
                "command_id": "conn-12345",
                "interface": "Ethernet192",
                "timestamp": 1672531200.0
            }
        }


class QoTEvent(BaseModel):
    """QoT reconfiguration event (Case 3 in paper)."""
    type: str = "qotEvent"
    connection_id: str
    agent_id: str
    event: str = Field(..., pattern="^(degradation_detected|tx_power_adjusted|recovery)$")
    details: Dict[str, Any]
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    
    class Config:
        schema_extra = {
            "example": {
                "type": "qotEvent",
                "connection_id": "POP1-POP2",
                "agent_id": "pop1-router1",
                "event": "tx_power_adjusted",
                "details": {
                    "old_power": -2.0,
                    "new_power": -1.5,
                    "reason": "OSNR degraded to 17.5 dB",
                    "samples": 3
                },
                "timestamp": 1672531200.0
            }
        }
