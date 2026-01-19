"""
Configuration Management for IP SDN Controller
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from enum import Enum


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # API Configuration
    API_HOST: str = Field(default="0.0.0.0", env="API_HOST")
    API_PORT: int = Field(default=8083, env="API_PORT")  # Default for vOp2 controller
    API_TITLE: str = "IP SDN Controller"
    API_VERSION: str = "1.0.0"
    
    # Virtual Operator Configuration
    VIRTUAL_OPERATOR: str = Field(default="vOp2", env="VIRTUAL_OPERATOR")
    CONTROLLER_ID: str = Field(default="controller-vOp2", env="CONTROLLER_ID")
    
    # Kafka Configuration
    KAFKA_BROKER: str = Field(default="10.30.7.52:9092", env="KAFKA_BROKER")
    CONFIG_TOPIC: Optional[str] = Field(default=None, env="CONFIG_TOPIC")
    MONITORING_TOPIC: Optional[str] = Field(default=None, env="MONITORING_TOPIC")
    
    # Link Database Configuration
    LINKDB_HOST: str = Field(default="link-db-redis", env="LINKDB_HOST")
    LINKDB_PORT: int = Field(default=6379, env="LINKDB_PORT")
    LINKDB_PASSWORD: Optional[str] = Field(default=None, env="LINKDB_PASSWORD")
    
    # QoT Monitoring Configuration (from your existing controller)
    OSNR_THRESHOLD: float = Field(default=18.0, env="OSNR_THRESHOLD")
    BER_THRESHOLD: float = Field(default=1e-3, env="BER_THRESHOLD")
    PERSISTENCY_SAMPLES: int = Field(default=3, env="PERSISTENCY_SAMPLES")
    COOLDOWN_SEC: int = Field(default=20, env="COOLDOWN_SEC")
    TX_STEP_DB: float = Field(default=1.0, env="TX_STEP_DB")
    TX_MIN_DBM: float = Field(default=-15.0, env="TX_MIN_DBM")
    TX_MAX_DBM: float = Field(default=0.0, env="TX_MAX_DBM")
    ADJUST_MODE: str = Field(default="both", env="ADJUST_MODE")
    
    # Path Computation Configuration
    DEFAULT_SPECTRUM_SLOTS: int = Field(default=4, env="DEFAULT_SPECTRUM_SLOTS")
    SLOT_WIDTH_GHZ: float = Field(default=12.5, env="SLOT_WIDTH_GHZ")  # Paper: 12.5 GHz
    
    # Logging
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO, env="LOG_LEVEL")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Set default topics based on virtual operator if not provided
        if not self.CONFIG_TOPIC:
            self.CONFIG_TOPIC = f"config_{self.VIRTUAL_OPERATOR}"
        if not self.MONITORING_TOPIC:
            self.MONITORING_TOPIC = f"monitoring_{self.VIRTUAL_OPERATOR}"


# Global settings instance
settings = Settings()