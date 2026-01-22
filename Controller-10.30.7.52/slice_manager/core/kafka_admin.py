"""
Module: Kafka Administration Client
Description: Kafka topic management for slice provisioning
Author: AI Developer
Date: 2024
"""

import logging
from typing import Dict, List, Optional, TYPE_CHECKING  # ADDED TYPE_CHECKING
from kafka import KafkaAdminClient
from kafka.admin import NewTopic, ConfigResource, ConfigResourceType
from kafka.errors import KafkaError, TopicAlreadyExistsError

from config.settings import settings

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from models.schemas import TopicInfo

logger = logging.getLogger(__name__)


class KafkaAdminManager:
    """Professional Kafka topic management for slice provisioning."""
    
    def __init__(self):
        """Initialize Kafka admin client with security configuration."""
        self._admin_client = None
        self._initialize_client()
    
    def _initialize_client(self) -> None:
        """Initialize Kafka admin client with optional security."""
        try:
            # Prepare client configuration
            client_config = {
                'bootstrap_servers': settings.KAFKA_BROKER,
                'client_id': 'slice-manager-admin'
            }
            
            # Add security configuration if provided
            if settings.KAFKA_SECURITY_PROTOCOL:
                client_config['security_protocol'] = settings.KAFKA_SECURITY_PROTOCOL
                
                if settings.KAFKA_SASL_MECHANISM:
                    client_config['sasl_mechanism'] = settings.KAFKA_SASL_MECHANISM
                    
                if settings.KAFKA_SASL_USERNAME and settings.KAFKA_SASL_PASSWORD:
                    client_config['sasl_plain_username'] = settings.KAFKA_SASL_USERNAME
                    client_config['sasl_plain_password'] = settings.KAFKA_SASL_PASSWORD
            
            self._admin_client = KafkaAdminClient(**client_config)
            logger.info(f"Kafka admin client initialized for broker: {settings.KAFKA_BROKER}")
            
        except Exception as e:
            logger.error(f"Failed to initialize Kafka admin client: {e}")
            raise
    
    def create_vop_topics(self, vop_id: str) -> Dict[str, 'TopicInfo']:  # CHANGED: Added quotes
        """
        Create Kafka topics for a new virtual operator.
        
        Args:
            vop_id: Virtual operator ID (e.g., 'vOp2')
            
        Returns:
            Dictionary with topic information
            
        Raises:
            KafkaError: If topic creation fails
        """
        if not self._admin_client:
            raise KafkaError("Kafka admin client not initialized")
        
        try:
            # Import here to avoid circular import
            from models.schemas import TopicInfo  # MOVED IMPORT HERE
            
            # Topic names based on paper convention
            config_topic = f"config_{vop_id}"
            monitoring_topic = f"monitoring_{vop_id}"
            
            topics = [
                NewTopic(
                    name=config_topic,
                    num_partitions=settings.DEFAULT_TOPIC_PARTITIONS,
                    replication_factor=settings.DEFAULT_TOPIC_REPLICATION,
                    topic_configs={
                        'retention.ms': '604800000',  # 7 days
                        'cleanup.policy': 'delete'
                    }
                ),
                NewTopic(
                    name=monitoring_topic,
                    num_partitions=settings.DEFAULT_TOPIC_PARTITIONS,
                    replication_factor=settings.DEFAULT_TOPIC_REPLICATION,
                    topic_configs={
                        'retention.ms': '86400000',  # 1 day
                        'cleanup.policy': 'delete'
                    }
                )
            ]
            
            # Create topics
            self._admin_client.create_topics(new_topics=topics, validate_only=False)
            logger.info(f"Created topics for {vop_id}: {config_topic}, {monitoring_topic}")
            
            # Verify topic creation
            self._verify_topics_exist([config_topic, monitoring_topic])
            
            return {
                'config_topic': TopicInfo(
                    name=config_topic,
                    partitions=settings.DEFAULT_TOPIC_PARTITIONS,
                    replication_factor=settings.DEFAULT_TOPIC_REPLICATION
                ),
                'monitoring_topic': TopicInfo(
                    name=monitoring_topic,
                    partitions=settings.DEFAULT_TOPIC_PARTITIONS,
                    replication_factor=settings.DEFAULT_TOPIC_REPLICATION
                )
            }
            
        except TopicAlreadyExistsError:
            logger.warning(f"Topics for {vop_id} already exist")
            raise
        except Exception as e:
            logger.error(f"Failed to create topics for {vop_id}: {e}")
            raise KafkaError(f"Topic creation failed: {e}")
    
    def _verify_topics_exist(self, topic_names: List[str]) -> None:
        """Verify that topics were created successfully."""
        try:
            existing_topics = self._admin_client.list_topics()
            for topic in topic_names:
                if topic not in existing_topics:
                    raise KafkaError(f"Topic {topic} was not created")
        except Exception as e:
            logger.error(f"Topic verification failed: {e}")
            raise
    
    def delete_vop_topics(self, vop_id: str) -> None:
        """
        Delete Kafka topics for a virtual operator.
        
        Args:
            vop_id: Virtual operator ID
            
        Raises:
            KafkaError: If topic deletion fails
        """
        try:
            topics = [f"config_{vop_id}", f"monitoring_{vop_id}"]
            self._admin_client.delete_topics(topics)
            logger.info(f"Deleted topics for {vop_id}: {topics}")
        except Exception as e:
            logger.error(f"Failed to delete topics for {vop_id}: {e}")
            raise KafkaError(f"Topic deletion failed: {e}")
    
    def get_topic_config(self, topic_name: str) -> Dict[str, str]:
        """Get configuration for a specific topic."""
        try:
            config_resource = ConfigResource(ConfigResourceType.TOPIC, topic_name)
            config_entries = self._admin_client.describe_configs([config_resource])
            
            config_dict = {}
            for config_entry in config_entries[0].resources:
                config_dict[config_entry.name] = config_entry.value
            
            return config_dict
        except Exception as e:
            logger.error(f"Failed to get config for topic {topic_name}: {e}")
            return {}
    
    def close(self) -> None:
        """Close the admin client connection."""
        if self._admin_client:
            self._admin_client.close()
            logger.info("Kafka admin client closed")
    
    def __del__(self):
        """Destructor to ensure proper cleanup."""
        self.close()


# Singleton instance
kafka_admin = KafkaAdminManager()
