"""
Data schemas for SONiC Agent
Using Pydantic for data validation and serialization.
"""

from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, validator


class AgentAction(str, Enum):
    """Agent action enumeration."""
    INTERFACE_CONTROL = "interfaceControl"
    GET_INTERFACE_STATUS = "getInterfaceStatus"
    GET_PLUGGABLE_INFO = "getPluggableInfo"
    SETUP_CONNECTION = "setupConnection"
    RECONFIG_CONNECTION = "reconfigConnection"
    START_TELEMETRY = "startTelemetry"
    STOP_TELEMETRY = "stopTelemetry"
    HEALTH_CHECK = "healthCheck"


class AgentStatus(str, Enum):
    """Agent status enumeration."""
    SUCCESS = "success"
    ERROR = "error"
    PENDING = "pending"
    DEGRADED = "degraded"


class ModuleState(str, Enum):
    """Module state enumeration."""
    RESET = "reset"
    INITIALIZED = "initialized"
    LOW_POWER = "low_power"
    HIGH_POWER_UP = "high_power_up"
    HIGH_POWER = "high_power"
    FAULT = "fault"


class TelemetryType(str, Enum):
    """Telemetry type enumeration."""
    OPTICAL_POWER = "optical_power"
    OSNR = "osnr"
    BER = "ber"
    TEMPERATURE = "temperature"
    VOLTAGE = "voltage"
    CURRENT = "current"
    STATUS = "status"


class AgentCommand(BaseModel):
    """Command sent to agent."""
    action: AgentAction
    command_id: str = Field(..., min_length=1, max_length=100)
    target_pop: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[float] = None
    priority: int = Field(default=1, ge=1, le=10)
    
    @validator('timestamp', pre=True, always=True)
    def set_timestamp(cls, v):
        """Set timestamp if not provided."""
        return v or datetime.utcnow().timestamp()
    
    class Config:
        schema_extra = {
            "example": {
                "action": "setupConnection",
                "command_id": "cmd-12345",
                "target_pop": "pop1",
                "parameters": {
                    "connection_id": "conn-1",
                    "frequency": 193100,
                    "endpoint_config": [
                        {
                            "pop_id": "pop1",
                            "port_id": "Ethernet192",
                            "app": 1,
                            "tx_power_level": -10.0
                        }
                    ]
                },
                "timestamp": 1672531200.0,
                "priority": 5
            }
        }


class CommandResponse(BaseModel):
    """Response from agent after command execution."""
    status: AgentStatus
    command_id: str
    agent_id: str
    timestamp: float
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    execution_time_ms: Optional[float] = None
    
    class Config:
        schema_extra = {
            "example": {
                "status": "success",
                "command_id": "cmd-12345",
                "agent_id": "pop1-router1",
                "timestamp": 1672531200.5,
                "data": {
                    "connection_id": "conn-1",
                    "frequency": 193100,
                    "endpoints": [
                        {
                            "interface": "Ethernet192",
                            "success": True,
                            "frequency_mhz": 193100,
                            "app_code": 1,
                            "tx_power_dbm": -10.0
                        }
                    ]
                },
                "execution_time_ms": 125.5
            }
        }


class TelemetryData(BaseModel):
    """Telemetry data from agent."""
    session_id: str
    connection_id: str
    interface: str
    timestamp: float
    readings: Dict[str, Any]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        schema_extra = {
            "example": {
                "session_id": "session-conn-1-1672531200",
                "connection_id": "conn-1",
                "interface": "Ethernet192",
                "timestamp": 1672531200.5,
                "readings": {
                    "tx_power_dbm": -9.8,
                    "rx_power_dbm": -12.3,
                    "osnr_db": 25.7,
                    "pre_fec_ber": 1.2e-5,
                    "temperature_c": 45.2,
                    "module_state": "high_power",
                    "is_healthy": True
                },
                "metadata": {
                    "frequency_mhz": 193100,
                    "app_code": 1
                }
            }
        }


class ConnectionConfig(BaseModel):
    """Connection configuration."""
    connection_id: str
    frequency: int = Field(..., ge=191300, le=196100)
    endpoint_config: List[Dict[str, Any]]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @validator('endpoint_config')
    def validate_endpoints(cls, v):
        """Validate endpoint configuration."""
        if not v:
            raise ValueError("At least one endpoint required")
        
        for endpoint in v:
            if 'pop_id' not in endpoint:
                raise ValueError("Endpoint missing pop_id")
            if 'port_id' not in endpoint:
                raise ValueError("Endpoint missing port_id")
        
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "connection_id": "conn-1",
                "frequency": 193100,
                "endpoint_config": [
                    {
                        "pop_id": "pop1",
                        "port_id": "Ethernet192",
                        "app": 1,
                        "tx_power_level": -10.0
                    },
                    {
                        "pop_id": "pop2",
                        "port_id": "Ethernet192",
                        "app": 1,
                        "tx_power_level": -10.0
                    }
                ],
                "metadata": {
                    "qos_class": "gold",
                    "owner": "operator-b"
                }
            }
        }


class InterfaceStatus(BaseModel):
    """Interface status information."""
    interface: str
    present: bool
    operational: bool
    admin_state: str
    speed: str
    vendor: Optional[str] = None
    part_number: Optional[str] = None
    serial: Optional[str] = None
    module_state: Optional[ModuleState] = None
    frequency_mhz: Optional[int] = None
    app_code: Optional[int] = None
    tx_power_dbm: Optional[float] = None
    errors: List[str] = Field(default_factory=list)
    timestamp: float
    
    class Config:
        schema_extra = {
            "example": {
                "interface": "Ethernet192",
                "present": True,
                "operational": True,
                "admin_state": "up",
                "speed": "400G",
                "vendor": "NeoPhotonics",
                "part_number": "QDDMA400700C2000",
                "serial": "EVC2327067",
                "module_state": "high_power",
                "frequency_mhz": 193100,
                "app_code": 1,
                "tx_power_dbm": -10.2,
                "errors": [],
                "timestamp": 1672531200.0
            }
        }


class PluggableInfo(BaseModel):
    """Pluggable transceiver information."""
    interface: str
    present: bool
    vendor: str
    part_number: str
    serial: str
    revision: str
    type: str
    capabilities: Dict[str, Any]
    module_state: Optional[ModuleState] = None
    timestamp: float
    
    class Config:
        schema_extra = {
            "example": {
                "interface": "Ethernet192",
                "present": True,
                "vendor": "NeoPhotonics",
                "part_number": "QDDMA400700C2000",
                "serial": "EVC2327067",
                "revision": "03",
                "type": "QSFP-DD 400G ZR",
                "capabilities": {
                    "frequency_range": {
                        "min_mhz": 191300,
                        "max_mhz": 196100,
                        "step_mhz": 100
                    },
                    "tx_power_range": {
                        "min_dbm": -15.0,
                        "max_dbm": -8.0,
                        "step_db": 0.1
                    },
                    "supported_applications": [
                        {"code": 1, "name": "400ZR_DWDM"},
                        {"code": 2, "name": "400ZR_OFEC_16QAM"}
                    ],
                    "supported_modulations": ["16QAM", "8QAM", "QPSK"],
                    "max_baud_rate_gbd": 64.0,
                    "power_class": 8
                },
                "module_state": "high_power",
                "timestamp": 1672531200.0
            }
        }


class HealthStatus(BaseModel):
    """Agent health status."""
    agent_id: str
    pop_id: str
    router_id: str
    virtual_operator: str
    status: AgentStatus
    uptime: float
    messages_received: int = 0
    messages_processed: int = 0
    success_rate: float = 0.0
    telemetry_sessions: int = 0
    issues: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    timestamp: float
    
    @validator('success_rate')
    def calculate_success_rate(cls, v, values):
        """Calculate success rate if not provided."""
        if v == 0.0 and values.get('messages_processed', 0) > 0:
            successful = values['messages_processed'] - len(values.get('issues', []))
            return (successful / values['messages_processed']) * 100
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "agent_id": "pop1-router1",
                "pop_id": "pop1",
                "router_id": "router1",
                "virtual_operator": "vOp2",
                "status": "healthy",
                "uptime": 86400.5,
                "messages_received": 1250,
                "messages_processed": 1248,
                "success_rate": 99.8,
                "telemetry_sessions": 3,
                "issues": [],
                "timestamp": 1672531200.0
            }
        }


class AgentMetrics(BaseModel):
    """Agent performance metrics."""
    agent_id: str
    cpu_percent: float = Field(..., ge=0.0, le=100.0)
    memory_mb: float = Field(..., ge=0.0)
    disk_usage_percent: float = Field(..., ge=0.0, le=100.0)
    network_rx_mbps: float = Field(..., ge=0.0)
    network_tx_mbps: float = Field(..., ge=0.0)
    process_count: int = Field(..., ge=0)
    timestamp: float
    
    class Config:
        schema_extra = {
            "example": {
                "agent_id": "pop1-router1",
                "cpu_percent": 12.5,
                "memory_mb": 256.7,
                "disk_usage_percent": 45.2,
                "network_rx_mbps": 125.3,
                "network_tx_mbps": 87.6,
                "process_count": 42,
                "timestamp": 1672531200.0
            }
        }


class ErrorReport(BaseModel):
    """Error report from agent."""
    agent_id: str
    error_type: str
    error_message: str
    command_id: Optional[str] = None
    interface: Optional[str] = None
    stack_trace: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    severity: str = Field(default="error", regex="^(info|warning|error|critical)$")
    timestamp: float
    
    class Config:
        schema_extra = {
            "example": {
                "agent_id": "pop1-router1",
                "error_type": "CMISWriteError",
                "error_message": "Failed to write frequency register",
                "command_id": "cmd-12345",
                "interface": "Ethernet192",
                "stack_trace": "Traceback...",
                "context": {
                    "frequency_mhz": 193100,
                    "register_address": "0x14",
                    "attempts": 3
                },
                "severity": "error",
                "timestamp": 1672531200.0
            }
        }


class ConfigurationUpdate(BaseModel):
    """Configuration update for agent."""
    agent_id: str
    config_type: str
    config_data: Dict[str, Any]
    version: str
    timestamp: float
    requires_restart: bool = False
    
    class Config:
        schema_extra = {
            "example": {
                "agent_id": "pop1-router1",
                "config_type": "telemetry",
                "config_data": {
                    "interval_sec": 5.0,
                    "max_sessions": 100,
                    "batch_size": 200
                },
                "version": "1.2.0",
                "timestamp": 1672531200.0,
                "requires_restart": False
            }
        }


class AuditLog(BaseModel):
    """Audit log entry."""
    agent_id: str
    event_type: str
    event_data: Dict[str, Any]
    user: Optional[str] = None
    source_ip: Optional[str] = None
    timestamp: float
    
    class Config:
        schema_extra = {
            "example": {
                "agent_id": "pop1-router1",
                "event_type": "command_executed",
                "event_data": {
                    "action": "setupConnection",
                    "command_id": "cmd-12345",
                    "success": True,
                    "execution_time_ms": 125.5
                },
                "user": "admin",
                "source_ip": "10.30.7.52",
                "timestamp": 1672531200.0
            }
        }