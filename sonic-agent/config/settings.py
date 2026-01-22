"""
Configuration management for SONiC Agent
"""

import os
import json
from typing import List, Dict, Any, Optional
from enum import Enum

try:
    # Pydantic v2 with v1-compat shim
    from pydantic.v1 import BaseSettings, Field, validator
except ImportError:
    # Fallback for environments still on pure v1
    from pydantic import BaseSettings, Field, validator


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # === Agent Identity ===
    POP_ID: str = Field(default="pop1", env="POP_ID")
    ROUTER_ID: str = Field(default="router1", env="ROUTER_ID")
    VIRTUAL_OPERATOR: str = Field(default="vOp2", env="VIRTUAL_OPERATOR")
    AGENT_ID: str = Field(default="", env="AGENT_ID")

    @validator("AGENT_ID", pre=True, always=True)
    def set_agent_id(cls, v, values):
        """Derive AGENT_ID if not explicitly set."""
        if not v:
            return f"{values.get('POP_ID', 'pop')}-{values.get('ROUTER_ID', 'router')}"
        return v

    # === Kafka Configuration ===
    KAFKA_BROKER: str = Field(default="10.30.7.52:9092", env="KAFKA_BROKER")
    CONFIG_TOPIC: str = Field(default="", env="CONFIG_TOPIC")
    MONITORING_TOPIC: str = Field(default="", env="MONITORING_TOPIC")
    HEALTH_TOPIC: str = Field(default="", env="HEALTH_TOPIC")

    @validator("CONFIG_TOPIC", "MONITORING_TOPIC", "HEALTH_TOPIC", pre=True, always=True)
    def set_topic_names(cls, v, field, values):
        """Fill in default topic names based on VIRTUAL_OPERATOR if not provided."""
        if not v:
            vop = values.get("VIRTUAL_OPERATOR", "vOp2")
            if field.name == "CONFIG_TOPIC":
                return f"config_{vop}"
            elif field.name == "MONITORING_TOPIC":
                return f"monitoring_{vop}"
            elif field.name == "HEALTH_TOPIC":
                return f"health_{vop}"
        return v

    # === Hardware Configuration ===
    # Env: ASSIGNED_TRANSCEIVERS=["Ethernet0","Ethernet192",...]
    ASSIGNED_TRANSCEIVERS: List[str] = Field(
        default_factory=list,
        env="ASSIGNED_TRANSCEIVERS",
        description="List of interface names to monitor (e.g. Ethernet192)",
    )

    # Backwards-compatible alias (if older code used `assigned_transceivers`)
    @property
    def assigned_transceivers(self) -> List[str]:
        return self.ASSIGNED_TRANSCEIVERS

    IFNAME_TO_PORTNUM_JSON: str = Field(
        default='{"Ethernet192": 192}',
        env="IFNAME_TO_PORTNUM_JSON",
        description="JSON mapping from interface name to port number",
    )

    @property
    def interface_mappings(self) -> Dict[str, int]:
        """Get interface to port number mappings parsed from JSON."""
        try:
            data = json.loads(self.IFNAME_TO_PORTNUM_JSON)
            # Ensure keys are strings and values are ints
            return {str(k): int(v) for k, v in data.items()}
        except Exception:
            # Safe fallback
            return {"Ethernet192": 192}

    def validate_interface_mappings(self) -> None:
        """
        Log any interfaces that are missing from IFNAME_TO_PORTNUM_JSON.

        This is used early in run_agent.py to catch misalignment between
        ASSIGNED_TRANSCEIVERS and IFNAME_TO_PORTNUM_JSON.
        """
        import logging

        logger = logging.getLogger(__name__)
        mappings = self.interface_mappings
        missing = [iface for iface in self.ASSIGNED_TRANSCEIVERS if iface not in mappings]

        if missing:
            logger.warning(
                "The following ASSIGNED_TRANSCEIVERS have no port mapping in "
                "IFNAME_TO_PORTNUM_JSON: %s",
                missing,
            )

    # === Operational Settings ===
    TELEMETRY_INTERVAL_SEC: float = Field(
        default=3.0,
        env="TELEMETRY_INTERVAL_SEC",
        gt=0.1,
        description="Telemetry sampling interval in seconds",
    )
    COMMAND_TIMEOUT_SEC: int = Field(
        default=30,
        env="COMMAND_TIMEOUT_SEC",
        ge=5,
        description="Timeout for commands from controller",
    )
    MAX_TELEMETRY_SESSIONS: int = Field(
        default=10,
        env="MAX_TELEMETRY_SESSIONS",
        ge=1,
        description="Maximum concurrent telemetry sessions",
    )

    # === QoT Monitoring ===
    ENABLE_QOT_MONITORING: bool = Field(
        default=True,
        env="ENABLE_QOT_MONITORING",
        description="Enable QoT-based monitoring and events",
    )
    QOT_SAMPLES: int = Field(
        default=3,
        env="QOT_SAMPLES",
        ge=1,
        description="Number of samples for QoT decision",
    )
    QOT_COOLDOWN_SEC: int = Field(
        default=20,
        env="QOT_COOLDOWN_SEC",
        ge=1,
        description="Cooldown between QoT actions in seconds",
    )
    OSNR_THRESHOLD_DB: float = Field(
        default=18.0,
        env="OSNR_THRESHOLD_DB",
        description="OSNR threshold (dB) for QoT degradation",
    )
    BER_THRESHOLD: float = Field(
        default=0.001,
        env="BER_THRESHOLD",
        description="BER threshold for QoT degradation",
    )

    # === Logging Configuration ===
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO, env="LOG_LEVEL")
    LOG_FILE: str = Field(
        default="/var/log/sonic-agent/agent.log",
        env="LOG_FILE",
    )
    LOG_MAX_SIZE_MB: int = Field(
        default=10,
        env="LOG_MAX_SIZE_MB",
        ge=1,
        description="Max size of log file before rotation (MB)",
    )
    LOG_BACKUP_COUNT: int = Field(
        default=5,
        env="LOG_BACKUP_COUNT",
        ge=1,
        description="Number of rotated log files to keep",
    )

    # === Debug Settings ===
    DEBUG_MODE: bool = Field(default=False, env="DEBUG_MODE")
    MOCK_HARDWARE: bool = Field(
        default=False,
        env="MOCK_HARDWARE",
        description="If true, CMIS/SONiC access may be mocked",
    )

    class Config:
        # Keep behaviour similar to your original file
        env_file = "None"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()

