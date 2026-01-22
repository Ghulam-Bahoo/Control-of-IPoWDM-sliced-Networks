#!/usr/bin/env python3
"""
Pydantic models for SONiC agent <-> controller messages.

These schemas are used both for:
- Messages produced by the agent (telemetry, QoT events, health)
- Commands consumed by the agent from the controller (AgentCommand)
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Shared / identity models
# ---------------------------------------------------------------------------

class AgentIdentity(BaseModel):
    """Identity of the agent in the IPoWDM architecture."""
    model_config = ConfigDict(extra="ignore")

    pop_id: str = Field(..., description="POP identifier")
    router_id: str = Field(..., description="Router identifier within POP")
    virtual_operator: str = Field(..., description="Virtual operator (vOp) ID")
    agent_id: str = Field(..., description="Unique agent ID (e.g., pop1-router1)")


# ---------------------------------------------------------------------------
# Telemetry models
# ---------------------------------------------------------------------------

class TelemetryFields(BaseModel):
    """Optical telemetry fields from CMIS transceiver."""
    model_config = ConfigDict(extra="ignore")

    rx_power: Optional[float] = Field(
        None, description="Received optical power in dBm"
    )
    tx_power: Optional[float] = Field(
        None, description="Transmit optical power in dBm"
    )
    osnr: Optional[float] = Field(
        None, description="Optical Signal-to-Noise Ratio in dB"
    )
    ber: Optional[float] = Field(
        None, description="Pre-FEC Bit Error Rate"
    )
    temperature: Optional[float] = Field(
        None, description="Module temperature in °C"
    )
    module_state: Optional[str] = Field(
        None, description="Module state string (e.g., ready/warning/alarm)"
    )


class TelemetrySample(BaseModel):
    """Single telemetry sample sent by the agent every interval (3 s)."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field("telemetrySample", description="Message type discriminator")
    connection_id: str = Field(..., description="Logical connection id")
    agent_id: str = Field(..., description="Agent ID (pop-router)")
    interface: str = Field(..., description="Interface name, e.g. Ethernet192")
    timestamp: float = Field(default_factory=lambda: time.time())
    fields: TelemetryFields


# ---------------------------------------------------------------------------
# QoT events
# ---------------------------------------------------------------------------

class QoTEventType(str, Enum):
    """Types of QoT-related events that the agent can emit."""
    TX_POWER_ADJUSTED = "tx_power_adjusted"
    QOT_DEGRADED = "qot_degraded"
    QOT_RECOVERED = "qot_recovered"


class QoTEvent(BaseModel):
    """QoT-related event emitted on monitoring topic."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field("qotEvent", description="Message type discriminator")
    connection_id: str = Field(..., description="Logical connection id")
    agent_id: str = Field(..., description="Agent ID (pop-router)")
    event: QoTEventType
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=lambda: time.time())


# ---------------------------------------------------------------------------
# Health messages (used by AgentOrchestrator)
# ---------------------------------------------------------------------------

class HealthStatus(BaseModel):
    """Health summary for the agent, as sent by AgentOrchestrator."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field("agentHealth", description="Message type discriminator")
    agent_id: str
    status: str  # "healthy", "degraded", "stopped", "error", etc.
    pop_id: str
    router_id: str
    uptime: float = 0.0
    interfaces: List[Dict[str, Any]] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=lambda: time.time())


# ---------------------------------------------------------------------------
# Commands from controller -> agent (generic)
# ---------------------------------------------------------------------------

class AgentCommandType(str, Enum):
    """Supported generic command types."""
    HEALTH_CHECK = "healthCheck"
    START_TELEMETRY = "startTelemetry"
    STOP_TELEMETRY = "stopTelemetry"
    APPLY_CONFIG = "applyConfig"
    SHUTDOWN = "shutdown"


class AgentCommand(BaseModel):
    """
    Generic command issued by the controller to the agent, over `config_<vOp>` topic.
    """
    model_config = ConfigDict(extra="ignore")

    command_id: str = Field(..., description="Unique command identifier")
    type: AgentCommandType = Field(..., description="Command type")
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Command-specific parameters"
    )
    timestamp: float = Field(default_factory=lambda: time.time())


# ---------------------------------------------------------------------------
# Capabilities and connection setup (used by AgentOrchestrator)
# ---------------------------------------------------------------------------

class AgentCapabilities(BaseModel):
    """Capabilities of the agent and its interfaces (Fig. 2a style)."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field("agentCapabilities", description="Message type discriminator")
    agent_id: str
    pop_id: str
    node_id: str
    interfaces: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: float = Field(default_factory=lambda: time.time())


class SetupConnectionCommand(BaseModel):
    """Command for setting up a connection (Case 2 from the OFC paper)."""
    model_config = ConfigDict(extra="ignore")

    command_id: Optional[str] = Field(default=None, description="Command identifier")
    type: Optional[str] = Field(default=None, description="Message type, e.g. 'command'")
    action: str = Field(..., description="Expected to be 'setupConnection'")
    connection_id: str = Field(..., description="Logical connection ID")
    endpoint_config: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Per-endpoint configuration dicts (pop_id, node_id, port_id, app, tx_power_level, ...)"
    )
    frequency: float = Field(..., description="Optical frequency for the connection")
    timestamp: float = Field(default_factory=lambda: time.time())


class ErrorReport(BaseModel):
    """Error report sent when message / command processing fails."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field("error", description="Message type discriminator")
    agent_id: str
    error_type: str
    error_message: str
    command_id: Optional[str] = None
    timestamp: float = Field(default_factory=lambda: time.time())

