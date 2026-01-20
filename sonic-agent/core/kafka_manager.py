"""
Kafka Manager for SONiC Agent
Production-ready with retry logic and proper error handling
"""

import json
import time
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum

from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import NoBrokersAvailable, KafkaError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@dataclass
class KafkaMessage:
    """Kafka message wrapper."""
    topic: str
    key: Optional[str]
    value: Dict[str, Any]
    timestamp: float


class KafkaManager:
    """Manages Kafka communication for SONiC Agent."""
    
    def __init__(self, broker: str, config_topic: str, monitoring_topic: str):
        """Initialize Kafka manager."""
        self.broker = broker
        self.config_topic = config_topic
        self.monitoring_topic = monitoring_topic
        
        self.logger = logging.getLogger("kafka-manager")
        
        # Kafka clients
        self.producer: Optional[KafkaProducer] = None
        self.consumer: Optional[KafkaConsumer] = None
        
        # Connection state
        self.connected = False
        self.consumer_group = f"sonic-agent-{int(time.time())}"
        
        # Statistics
        self.messages_sent = 0
        self.messages_received = 0
        self.send_errors = 0
        self.receive_errors = 0
        
        # Initialize connections
        self._initialize_connections()
    
    def _initialize_connections(self):
        """Initialize Kafka connections with retry logic."""
        max_retries = 5
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                self.logger.info(f"Connecting to Kafka (attempt {attempt + 1}/{max_retries})...")
                
                # Initialize producer
                self.producer = KafkaProducer(
                    bootstrap_servers=self.broker,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    key_serializer=lambda k: k.encode('utf-8') if k else None,
                    acks='all',
                    retries=3,
                    max_in_flight_requests_per_connection=1,
                    request_timeout_ms=30000
                )
                
                # Initialize consumer
                self.consumer = KafkaConsumer(
                    self.config_topic,
                    bootstrap_servers=self.broker,
                    value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                    key_deserializer=lambda k: k.decode('utf-8') if k else None,
                    group_id=self.consumer_group,
                    auto_offset_reset='latest',
                    enable_auto_commit=False,
                    session_timeout_ms=30000,
                    heartbeat_interval_ms=3000,
                    max_poll_records=10,
                    max_poll_interval_ms=300000
                )
                
                # Test connection
                self.producer.flush(timeout=10)
                
                self.connected = True
                self.logger.info(f"Connected to Kafka broker: {self.broker}")
                self.logger.info(f"Subscribed to config topic: {self.config_topic}")
                self.logger.info(f"Will publish to monitoring topic: {self.monitoring_topic}")
                return
                
            except NoBrokersAvailable as e:
                self.logger.error(f"Kafka broker not available: {e}")
            except Exception as e:
                self.logger.error(f"Kafka connection failed: {e}")
            
            if attempt < max_retries - 1:
                self.logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
        
        self.logger.error("Failed to connect to Kafka after all retries")
        self.connected = False
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((KafkaError, ConnectionError))
    )
    def send_message(self, topic: str, value: Dict[str, Any], key: Optional[str] = None) -> bool:
        """Send a message to Kafka topic."""
        if not self.connected:
            self.logger.warning("Not connected to Kafka, attempting reconnection...")
            self._reconnect()
        
        try:
            future = self.producer.send(
                topic=topic,
                key=key,
                value=value
            )
            
            # Wait for acknowledgment
            record_metadata = future.get(timeout=10)
            
            self.messages_sent += 1
            self.logger.debug(
                f"Message sent to {topic}[{record_metadata.partition}:{record_metadata.offset}] "
                f"(key: {key})"
            )
            return True
            
        except Exception as e:
            self.send_errors += 1
            self.logger.error(f"Failed to send message to {topic}: {e}")
            return False
    
    def send_monitoring_message(self, message: Dict[str, Any]) -> bool:
        """Send message to monitoring topic."""
        return self.send_message(self.monitoring_topic, message)
    
    def send_health_message(self, message: Dict[str, Any]) -> bool:
        """Send health message."""
        return self.send_message(f"health_{self.monitoring_topic.split('_')[1]}", message)
    
    def poll_messages(self, timeout_ms: int = 1000) -> List[KafkaMessage]:
        """Poll messages from config topic."""
        messages = []
        
        if not self.connected or not self.consumer:
            return messages
        
        try:
            batch = self.consumer.poll(timeout_ms=timeout_ms)
            
            for _, records in batch.items():
                for record in records:
                    message = KafkaMessage(
                        topic=record.topic,
                        key=record.key,
                        value=record.value,
                        timestamp=record.timestamp / 1000.0 if record.timestamp else time.time()
                    )
                    messages.append(message)
            
            # Commit offsets if we processed messages
            if messages:
                self.consumer.commit()
                self.messages_received += len(messages)
                self.logger.debug(f"Polled {len(messages)} messages from {self.config_topic}")
            
        except Exception as e:
            self.receive_errors += 1
            self.logger.error(f"Failed to poll messages: {e}")
        
        return messages
    
    def _reconnect(self) -> bool:
        """Attempt to reconnect to Kafka."""
        self.logger.info("Attempting to reconnect to Kafka...")
        
        # Close existing connections
        self.close()
        
        # Reinitialize
        self._initialize_connections()
        
        if self.connected:
            self.logger.info("Reconnected to Kafka successfully")
        else:
            self.logger.error("Failed to reconnect to Kafka")
        
        return self.connected
    
    def check_connection(self) -> bool:
        """Check Kafka connection status."""
        if not self.connected:
            return False
        
        try:
            # Test connection by getting metadata
            self.producer.partitions_for(topic=self.config_topic)
            return True
        except Exception as e:
            self.logger.warning(f"Kafka connection check failed: {e}")
            self.connected = False
            return False
    
    def close(self):
        """Close Kafka connections."""
        self.logger.info("Closing Kafka connections...")
        
        if self.producer:
            try:
                self.producer.flush(timeout=10)
                self.producer.close(timeout=10)
            except Exception:
                pass
        
        if self.consumer:
            try:
                self.consumer.close()
            except Exception:
                pass
        
        self.connected = False
        self.logger.info("Kafka connections closed")
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        self.close()
