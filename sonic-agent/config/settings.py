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
    
    @validator('AGENT_ID', pre=True, always=True)
    def set_agent_id(cls, v, values):
        if not v:
            return f"{values.get('POP_ID', 'pop')}-{values.get('ROUTER_ID', 'router')}"
        return v
    
    # === Kafka Configuration ===
    KAFKA_BROKER: str = Field(default="10.30.7.52:9092", env="KAFKA_BROKER")
    CONFIG_TOPIC: str = Field(default="", env="CONFIG_TOPIC")
    MONITORING_TOPIC: str = Field(default="", env="MONITORING_TOPIC")
    HEALTH_TOPIC: str = Field(default="", env="HEALTH_TOPIC")
    
    @validator('CONFIG_TOPIC', 'MONITORING_TOPIC', 'HEALTH_TOPIC', pre=True, always=True)
    def set_topic_names(cls, v, field, values):
        if not v:
            if field.name == 'CONFIG_TOPIC':
                return f"config_{values.get('VIRTUAL_OPERATOR', 'vOp2')}"
            elif field.name == 'MONITORING_TOPIC':
                return f"monitoring_{values.get('VIRTUAL_OPERATOR', 'vOp2')}"
            elif field.name == 'HEALTH_TOPIC':
                return f"health_{values.get('VIRTUAL_OPERATOR', 'vOp2')}"
        return v
    
    # === Hardware Configuration ===
    assigned_transceivers:List[str] = Field(default_factory=list, env="ASSIGNED_TRANSCEIVERS")

    IFNAME_TO_PORTNUM_JSON:str = Field(
        default='{"Ethernet192": 192}',
        env="IFNAME_TO_PORTNUM_JSON"
    )
    
    @property
    def interface_mappings(self) -> Dict[str, int]:
        """Get interface to port number mappings."""
        try:
            return json.loads(self.IFNAME_TO_PORTNUM_JSON)
        except json.JSONDecodeError:
            return {"Ethernet192": 192}
    
    # === Operational Settings ===
    TELEMETRY_INTERVAL_SEC: float = Field(default=3.0, env="TELEMETRY_INTERVAL_SEC", gt=0.1)
    COMMAND_TIMEOUT_SEC: int = Field(default=30, env="COMMAND_TIMEOUT_SEC", ge=5)
    MAX_TELEMETRY_SESSIONS: int = Field(default=10, env="MAX_TELEMETRY_SESSIONS", ge=1)
    
    # === QoT Monitoring ===
    ENABLE_QOT_MONITORING: bool = Field(default=True, env="ENABLE_QOT_MONITORING")
    QOT_SAMPLES: int = Field(default=3, env="QOT_SAMPLES", ge=1)
    QOT_COOLDOWN_SEC: int = Field(default=20, env="QOT_COOLDOWN_SEC", ge=1)
    OSNR_THRESHOLD_DB: float = Field(default=18.0, env="OSNR_THRESHOLD_DB")
    BER_THRESHOLD: float = Field(default=0.001, env="BER_THRESHOLD")
    
    # === Logging Configuration ===
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO, env="LOG_LEVEL")
    LOG_FILE: str = Field(default="/var/log/sonic-agent/agent.log", env="LOG_FILE")
    LOG_MAX_SIZE_MB: int = Field(default=10, env="LOG_MAX_SIZE_MB", ge=1)
    LOG_BACKUP_COUNT: int = Field(default=5, env="LOG_BACKUP_COUNT", ge=1)
    
    # === Debug Settings ===
    DEBUG_MODE: bool = Field(default=False, env="DEBUG_MODE")
    MOCK_HARDWARE: bool = Field(default=False, env="MOCK_HARDWARE")
    
    class Config:
        env_file = "None"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()
