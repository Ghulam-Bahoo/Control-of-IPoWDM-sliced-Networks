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
# Health messages
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class AgentHealth(BaseModel):
    """Health summary for the agent."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field("agentHealth", description="Message type discriminator")
    agent_id: str
    status: HealthStatus
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=lambda: time.time())


# ---------------------------------------------------------------------------
# Commands from controller -> agent
# ---------------------------------------------------------------------------

class AgentCommandType(str, Enum):
    """Supported command types from IP-SDN controller."""
    HEALTH_CHECK = "healthCheck"
    START_TELEMETRY = "startTelemetry"
    STOP_TELEMETRY = "stopTelemetry"
    APPLY_CONFIG = "applyConfig"
    SHUTDOWN = "shutdown"


class AgentCommand(BaseModel):
    """
    Command issued by the controller to the agent, over `config_<vOp>` topic.

    Example payload:
    {
        "command_id": "cmd-123",
        "type": "startTelemetry",
        "parameters": {
            "connection_id": "conn-1",
            "interface": "Ethernet192"
        },
        "timestamp": 1700000000.0
    }
    """
    model_config = ConfigDict(extra="ignore")

    command_id: str = Field(..., description="Unique command identifier")
    type: AgentCommandType = Field(..., description="Command type")
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Command-specific parameters"
    )
    timestamp: float = Field(default_factory=lambda: time.time())
