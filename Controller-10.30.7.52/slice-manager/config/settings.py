import os
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict  # CHANGED
from pydantic import Field, validator  # Keep Field from pydantic
from enum import Enum


class LogLevel(str, Enum):
    """Log level enumeration."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # API Settings
    API_HOST: str = Field(default="0.0.0.0", env="API_HOST")
    API_PORT: int = Field(default=8080, env="API_PORT")
    API_TITLE: str = "SONiC Slice Manager"
    API_VERSION: str = "1.0.0"
    
    # Kafka Settings
    KAFKA_BROKER: str = Field(default="10.30.7.52:9092", env="KAFKA_BROKER")
    KAFKA_SECURITY_PROTOCOL: Optional[str] = Field(default=None, env="KAFKA_SECURITY_PROTOCOL")
    KAFKA_SASL_MECHANISM: Optional[str] = Field(default=None, env="KAFKA_SASL_MECHANISM")
    KAFKA_SASL_USERNAME: Optional[str] = Field(default=None, env="KAFKA_SASL_USERNAME")
    KAFKA_SASL_PASSWORD: Optional[str] = Field(default=None, env="KAFKA_SASL_PASSWORD")
    
    # Link Database Settings
    LINKDB_HOST: str = Field(default="localhost", env="LINKDB_HOST")
    LINKDB_PORT: int = Field(default=6379, env="LINKDB_PORT")  # Assuming Redis
    LINKDB_PASSWORD: Optional[str] = Field(default=None, env="LINKDB_PASSWORD")
    
    # Logging
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO, env="LOG_LEVEL")
    
    # Deployment Settings
    CONTROLLER_DEPLOY_SCRIPT: str = Field(
        default="/opt/slice-manager/deploy-controller.sh",
        env="CONTROLLER_DEPLOY_SCRIPT"
    )
    
    # Default Topic Configurations
    DEFAULT_TOPIC_PARTITIONS: int = Field(default=3, env="DEFAULT_TOPIC_PARTITIONS")
    DEFAULT_TOPIC_REPLICATION: int = Field(default=1, env="DEFAULT_TOPIC_REPLICATION")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()