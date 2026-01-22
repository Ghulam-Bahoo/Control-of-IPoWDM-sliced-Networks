# link_database/config.py
# Configuration settings for Link Database

import os
from typing import Optional

class Config:
    """Application configuration"""
    
    # Redis configuration
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB = int(os.getenv("REDIS_DB", "0"))
    
    # Kafka configuration
    KAFKA_BROKER = os.getenv("KAFKA_BROKER", "10.30.7.52:9092")
    KAFKA_USERNAME = os.getenv("KAFKA_USERNAME", "")
    KAFKA_PASSWORD = os.getenv("KAFKA_PASSWORD", "")
    
    # Application settings
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    API_HOST = os.getenv("API_HOST", "0.0.0.0")
    API_PORT = int(os.getenv("API_PORT", "8000"))
    
    # Network settings
    START_FREQUENCY = int(os.getenv("START_FREQUENCY", "191300"))  # MHz
    END_FREQUENCY = int(os.getenv("END_FREQUENCY", "196100"))      # MHz
    CHANNEL_SPACING = int(os.getenv("CHANNEL_SPACING", "50"))      # MHz
    TOTAL_CHANNELS = int(os.getenv("TOTAL_CHANNELS", "96"))
    
    # Security (for future use)
    ENABLE_AUTH = os.getenv("ENABLE_AUTH", "false").lower() == "true"
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    
    @classmethod
    def validate(cls):
        """Validate configuration"""
        errors = []
        
        if not cls.KAFKA_BROKER:
            errors.append("KAFKA_BROKER is required")
        
        if cls.START_FREQUENCY >= cls.END_FREQUENCY:
            errors.append("START_FREQUENCY must be less than END_FREQUENCY")
        
        if cls.CHANNEL_SPACING <= 0:
            errors.append("CHANNEL_SPACING must be positive")
        
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")
        
        return True
    
    @classmethod
    def get_kafka_config(cls) -> dict:
        """Get Kafka configuration dictionary"""
        config = {
            "bootstrap_servers": cls.KAFKA_BROKER,
            "api_version": (2, 6, 0)
        }
        
        if cls.KAFKA_USERNAME and cls.KAFKA_PASSWORD:
            config.update({
                "security_protocol": "SASL_PLAINTEXT",
                "sasl_mechanism": "PLAIN",
                "sasl_plain_username": cls.KAFKA_USERNAME,
                "sasl_plain_password": cls.KAFKA_PASSWORD
            })
        
        return config

# Validate configuration on import
try:
    Config.validate()
except ValueError as e:
    print(f"Configuration error: {e}")
    raise
