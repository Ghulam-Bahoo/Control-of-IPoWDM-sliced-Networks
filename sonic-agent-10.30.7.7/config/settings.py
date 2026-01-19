"""
Configuration Management for SONiC Agent
"""

import os
import json
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass
from pydantic import BaseSettings, Field, validator


class LogLevel(str, Enum):
    """Log level enumeration."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class KafkaSecurityProtocol(str, Enum):
    """Kafka security protocol options."""
    PLAINTEXT = "PLAINTEXT"
    SASL_PLAINTEXT = "SASL_PLAINTEXT"
    SASL_SSL = "SASL_SSL"
    SSL = "SSL"


@dataclass
class HardwareConfig:
    """Hardware configuration."""
    interface_mappings: Dict[str, int]
    assigned_transceivers: List[str]
    default_polling_interval: int = 1000
    max_retries: int = 3
    retry_delay: float = 0.5


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # === Kafka Configuration ===
    kafka_broker: str = Field(
        default="10.30.7.52:9092",
        env="KAFKA_BROKER",
        description="Kafka broker address"
    )
    
    kafka_security_protocol: KafkaSecurityProtocol = Field(
        default=KafkaSecurityProtocol.PLAINTEXT,
        env="KAFKA_SECURITY_PROTOCOL"
    )
    
    kafka_sasl_mechanism: Optional[str] = Field(
        default=None,
        env="KAFKA_SASL_MECHANISM"
    )
    
    kafka_sasl_username: Optional[str] = Field(
        default=None,
        env="KAFKA_SASL_USERNAME"
    )
    
    kafka_sasl_password: Optional[str] = Field(
        default=None,
        env="KAFKA_SASL_PASSWORD"
    )
    
    kafka_max_poll_records: int = Field(
        default=10,
        env="KAFKA_MAX_POLL_RECORDS",
        ge=1,
        le=100
    )
    
    kafka_session_timeout_ms: int = Field(
        default=30000,
        env="KAFKA_SESSION_TIMEOUT_MS",
        ge=10000,
        le=120000
    )
    
    # === Agent Identity ===
    pop_id: str = Field(
        default="pop1",
        env="POP_ID",
        min_length=1,
        regex=r'^[a-zA-Z0-9_-]+$'
    )
    
    router_id: str = Field(
        default="router1",
        env="ROUTER_ID",
        min_length=1,
        regex=r'^[a-zA-Z0-9_-]+$'
    )
    
    virtual_operator: str = Field(
        default="vOp2",
        env="VIRTUAL_OPERATOR",
        min_length=1,
        regex=r'^[a-zA-Z0-9_-]+$'
    )
    
    agent_id: str = Field(
        default="",
        env="AGENT_ID"
    )
    
    @validator('agent_id', pre=True, always=True)
    def set_agent_id(cls, v, values):
        """Generate agent ID if not provided."""
        if not v:
            return f"{values.get('pop_id', 'pop')}-{values.get('router_id', 'router')}"
        return v
    
    # === Topic Configuration ===
    config_topic: str = Field(
        default="",
        env="CONFIG_TOPIC"
    )
    
    monitoring_topic: str = Field(
        default="",
        env="MONITORING_TOPIC"
    )
    
    health_topic: str = Field(
        default="",
        env="HEALTH_TOPIC"
    )
    
    @validator('config_topic', 'monitoring_topic', 'health_topic', pre=True, always=True)
    def set_topic_names(cls, v, field, values):
        """Generate topic names if not provided."""
        if not v:
            if field.name == 'config_topic':
                return f"config_{values.get('virtual_operator', 'vOp2')}"
            elif field.name == 'monitoring_topic':
                return f"monitoring_{values.get('virtual_operator', 'vOp2')}"
            elif field.name == 'health_topic':
                return f"health_{values.get('virtual_operator', 'vOp2')}"
        return v
    
    # === Operational Settings ===
    interval_sec: float = Field(
        default=3.0,
        env="INTERVAL_SEC",
        gt=0.1,
        le=60.0
    )
    
    max_telemetry_sessions: int = Field(
        default=50,
        env="MAX_TELEMETRY_SESSIONS",
        ge=1,
        le=1000
    )
    
    session_timeout_sec: int = Field(
        default=1800,
        env="SESSION_TIMEOUT_SEC",
        ge=60,
        le=86400
    )
    
    command_timeout_sec: int = Field(
        default=30,
        env="COMMAND_TIMEOUT_SEC",
        ge=5,
        le=300
    )
    
    # === Hardware Configuration ===
    assigned_transceivers: List[str] = Field(
        default_factory=lambda: ["Ethernet192"],
        env="ASSIGNED_TRANSCEIVERS"
    )
    
    ifname_to_portnum_json: str = Field(
        default='{"Ethernet192": 192}',
        env="IFNAME_TO_PORTNUM_JSON"
    )
    
    default_polling_interval: int = Field(
        default=1000,
        env="DEFAULT_POLLING_INTERVAL",
        ge=100,
        le=10000
    )
    
    # === Module Configuration ===
    default_frequency_mhz: int = Field(
        default=193100,
        env="DEFAULT_FREQUENCY_MHZ",
        ge=191300,
        le=196100
    )
    
    default_app_code: int = Field(
        default=1,
        env="DEFAULT_APP_CODE",
        ge=1,
        le=5
    )
    
    default_tx_power_dbm: float = Field(
        default=-10.0,
        env="DEFAULT_TX_POWER_DBM",
        ge=-15.0,
        le=0.0
    )
    
    max_tx_power_dbm: float = Field(
        default=0.0,
        env="MAX_TX_POWER_DBM",
        ge=-20.0,
        le=5.0
    )
    
    min_tx_power_dbm: float = Field(
        default=-15.0,
        env="MIN_TX_POWER_DBM",
        ge=-20.0,
        le=0.0
    )
    
    # === Logging Configuration ===
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        env="LOG_LEVEL"
    )
    
    log_file: str = Field(
        default="/var/log/sonic-agent/agent.log",
        env="LOG_FILE"
    )
    
    log_max_size_mb: int = Field(
        default=10,
        env="LOG_MAX_SIZE_MB",
        ge=1,
        le=100
    )
    
    log_backup_count: int = Field(
        default=5,
        env="LOG_BACKUP_COUNT",
        ge=1,
        le=20
    )
    
    enable_debug: bool = Field(
        default=False,
        env="ENABLE_DEBUG"
    )
    
    # === Performance Settings ===
    telemetry_batch_size: int = Field(
        default=100,
        env="TELEMETRY_BATCH_SIZE",
        ge=1,
        le=1000
    )
    
    # === Computed Properties ===
    @property
    def hardware_config(self) -> HardwareConfig:
        """Get hardware configuration."""
        try:
            ifname_to_portnum = json.loads(self.ifname_to_portnum_json)
            if not isinstance(ifname_to_portnum, dict):
                raise ValueError("IFNAME_TO_PORTNUM_JSON must be a JSON object")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid IFNAME_TO_PORTNUM_JSON: {e}")
        
        return HardwareConfig(
            interface_mappings=ifname_to_portnum,
            assigned_transceivers=self.assigned_transceivers,
            default_polling_interval=self.default_polling_interval
        )
    
    @property
    def kafka_config(self) -> Dict[str, Any]:
        """Get Kafka configuration."""
        config = {
            'bootstrap_servers': self.kafka_broker,
            'security_protocol': self.kafka_security_protocol.value,
            'api_version': (2, 6, 0),
        }
        
        if self.kafka_sasl_username and self.kafka_sasl_password:
            config.update({
                'sasl_mechanism': self.kafka_sasl_mechanism or 'PLAIN',
                'sasl_plain_username': self.kafka_sasl_username,
                'sasl_plain_password': self.kafka_sasl_password,
            })
        
        return config
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()