"""
Kafka Manager for SONiC Agent
Handles all Kafka communication with reliability and retry logic.
"""

import json
import time
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import NoBrokersAvailable, KafkaError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class MessageType(str, Enum):
    """Message type enumeration."""
    COMMAND = "command"
    TELEMETRY = "telemetry"
    HEALTH = "health"
    ERROR = "error"
    ACKNOWLEDGMENT = "acknowledgment"


@dataclass
class KafkaMessage:
    """Kafka message wrapper."""
    topic: str
    key: Optional[str]
    value: Dict[str, Any]
    timestamp: float
    partition: Optional[int] = None
    offset: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "topic": self.topic,
            "key": self.key,
            "value": self.value,
            "timestamp": self.timestamp,
            "partition": self.partition,
            "offset": self.offset
        }


class KafkaManager:
    """Manages Kafka producer and consumer with reliability features."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Kafka manager."""
        self.config = config
        self.logger = logging.getLogger("kafka-manager")
        
        # Producer and consumer instances
        self.producer: Optional[KafkaProducer] = None
        self.consumers: Dict[str, KafkaConsumer] = {}
        
        # Message queues
        self.send_queue: List[KafkaMessage] = []
        self.receive_handlers: Dict[str, List[Callable]] = {}
        
        # Statistics
        self.messages_sent = 0
        self.messages_received = 0
        self.send_errors = 0
        self.receive_errors = 0
        
        # Connection state
        self.connected = False
        self.last_connection_check = 0
        self.connection_check_interval = 30  # seconds
        
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
                producer_config = self._get_producer_config()
                self.producer = KafkaProducer(**producer_config)
                
                # Test connection
                self.producer.flush(timeout=10)
                
                self.connected = True
                self.logger.info(f"Connected to Kafka broker: {self.config.get('bootstrap_servers')}")
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
    
    def _get_producer_config(self) -> Dict[str, Any]:
        """Get producer configuration."""
        config = {
            'bootstrap_servers': self.config.get('bootstrap_servers'),
            'value_serializer': lambda v: json.dumps(v).encode('utf-8'),
            'key_serializer': lambda k: k.encode('utf-8') if k else None,
            'acks': 'all',
            'retries': 3,
            'max_in_flight_requests_per_connection': 1,
            'compression_type': 'gzip',
            'request_timeout_ms': 30000,
            'metadata_max_age_ms': 300000,
        }
        
        # Add security configuration if provided
        if self.config.get('security_protocol'):
            config.update({
                'security_protocol': self.config.get('security_protocol'),
                'sasl_mechanism': self.config.get('sasl_mechanism', 'PLAIN'),
                'sasl_plain_username': self.config.get('sasl_plain_username'),
                'sasl_plain_password': self.config.get('sasl_plain_password'),
            })
        
        return config
    
    def _get_consumer_config(self, group_id: str) -> Dict[str, Any]:
        """Get consumer configuration."""
        config = {
            'bootstrap_servers': self.config.get('bootstrap_servers'),
            'group_id': group_id,
            'value_deserializer': lambda v: json.loads(v.decode('utf-8')),
            'key_deserializer': lambda k: k.decode('utf-8') if k else None,
            'auto_offset_reset': 'latest',
            'enable_auto_commit': False,
            'session_timeout_ms': 30000,
            'heartbeat_interval_ms': 3000,
            'max_poll_records': self.config.get('max_poll_records', 10),
            'max_poll_interval_ms': 300000,
        }
        
        # Add security configuration if provided
        if self.config.get('security_protocol'):
            config.update({
                'security_protocol': self.config.get('security_protocol'),
                'sasl_mechanism': self.config.get('sasl_mechanism', 'PLAIN'),
                'sasl_plain_username': self.config.get('sasl_plain_username'),
                'sasl_plain_password': self.config.get('sasl_plain_password'),
            })
        
        return config
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((KafkaError, ConnectionError))
    )
    def send_message(
        self,
        topic: str,
        value: Dict[str, Any],
        key: Optional[str] = None,
        sync: bool = True
    ) -> bool:
        """Send a message to Kafka topic."""
        if not self.connected:
            self.logger.warning("Not connected to Kafka, attempting reconnection...")
            if not self._reconnect():
                return False
        
        try:
            # Create message
            message = KafkaMessage(
                topic=topic,
                key=key,
                value=value,
                timestamp=time.time()
            )
            
            # Send message
            future = self.producer.send(
                topic=topic,
                key=key,
                value=value
            )
            
            if sync:
                # Wait for acknowledgment
                record_metadata = future.get(timeout=10)
                message.partition = record_metadata.partition
                message.offset = record_metadata.offset
                
                self.logger.debug(
                    f"Message sent to {topic}[{message.partition}:{message.offset}] "
                    f"(key: {key})"
                )
            
            self.messages_sent += 1
            return True
            
        except Exception as e:
            self.send_errors += 1
            self.logger.error(f"Failed to send message to {topic}: {e}")
            
            # Queue message for retry
            if sync:
                self.send_queue.append(message)
                self.logger.info(f"Queued message for retry: {len(self.send_queue)} in queue")
            
            return False
    
    def send_batch(self, messages: List[Dict[str, Any]]) -> List[bool]:
        """Send a batch of messages."""
        results = []
        
        for msg in messages:
            result = self.send_message(
                topic=msg.get('topic'),
                value=msg.get('value'),
                key=msg.get('key'),
                sync=False
            )
            results.append(result)
        
        # Flush producer
        try:
            self.producer.flush(timeout=30)
        except Exception as e:
            self.logger.error(f"Failed to flush producer: {e}")
            return [False] * len(results)
        
        return results
    
    def create_consumer(
        self,
        topic: str,
        group_id: str,
        auto_offset_reset: str = 'latest'
    ) -> Optional[KafkaConsumer]:
        """Create a Kafka consumer for a topic."""
        if topic in self.consumers:
            return self.consumers[topic]
        
        try:
            consumer_config = self._get_consumer_config(group_id)
            consumer_config['auto_offset_reset'] = auto_offset_reset
            
            consumer = KafkaConsumer(
                topic,
                **consumer_config
            )
            
            self.consumers[topic] = consumer
            self.logger.info(f"Created consumer for topic {topic} (group: {group_id})")
            
            return consumer
            
        except Exception as e:
            self.logger.error(f"Failed to create consumer for {topic}: {e}")
            return None
    
    def poll_messages(
        self,
        topic: str,
        group_id: str,
        timeout_ms: int = 1000,
        max_messages: int = 10
    ) -> List[KafkaMessage]:
        """Poll messages from a topic."""
        messages = []
        
        try:
            # Get or create consumer
            consumer = self.consumers.get(topic)
            if not consumer:
                consumer = self.create_consumer(topic, group_id)
                if not consumer:
                    return []
            
            # Poll for messages
            batch = consumer.poll(timeout_ms=timeout_ms)
            
            for _, records in batch.items():
                for record in records:
                    message = KafkaMessage(
                        topic=topic,
                        key=record.key,
                        value=record.value,
                        timestamp=record.timestamp / 1000.0 if record.timestamp else time.time(),
                        partition=record.partition,
                        offset=record.offset
                    )
                    
                    messages.append(message)
                    
                    if len(messages) >= max_messages:
                        break
                
                if len(messages) >= max_messages:
                    break
            
            # Commit offsets if we processed messages
            if messages:
                consumer.commit()
                self.messages_received += len(messages)
                
                self.logger.debug(
                    f"Polled {len(messages)} messages from {topic} "
                    f"(total: {self.messages_received})"
                )
            
        except Exception as e:
            self.receive_errors += 1
            self.logger.error(f"Failed to poll messages from {topic}: {e}")
        
        return messages
    
    def subscribe(
        self,
        topic: str,
        group_id: str,
        handler: Callable[[KafkaMessage], None]
    ) -> bool:
        """Subscribe to a topic with a message handler."""
        try:
            # Create consumer
            consumer = self.create_consumer(topic, group_id)
            if not consumer:
                return False
            
            # Register handler
            if topic not in self.receive_handlers:
                self.receive_handlers[topic] = []
            
            self.receive_handlers[topic].append(handler)
            
            self.logger.info(f"Subscribed to {topic} with handler")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to subscribe to {topic}: {e}")
            return False
    
    def process_subscriptions(self, timeout_ms: int = 100):
        """Process all subscriptions."""
        for topic, handlers in self.receive_handlers.items():
            if not handlers:
                continue
            
            # Poll messages
            messages = self.poll_messages(
                topic=topic,
                group_id=f"handler-{id(handlers[0])}",
                timeout_ms=timeout_ms
            )
            
            # Process messages with handlers
            for message in messages:
                for handler in handlers:
                    try:
                        handler(message)
                    except Exception as e:
                        self.logger.error(f"Handler error for {topic}: {e}")
    
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
        current_time = time.time()
        
        # Only check periodically
        if current_time - self.last_connection_check < self.connection_check_interval:
            return self.connected
        
        self.last_connection_check = current_time
        
        try:
            if self.producer:
                # Test connection by getting metadata
                self.producer.partitions_for(topic='test-connection')
                self.connected = True
            else:
                self.connected = False
                
        except Exception as e:
            self.logger.warning(f"Kafka connection check failed: {e}")
            self.connected = False
        
        return self.connected
    
    def get_stats(self) -> Dict[str, Any]:
        """Get Kafka manager statistics."""
        return {
            "connected": self.connected,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "send_errors": self.send_errors,
            "receive_errors": self.receive_errors,
            "queued_messages": len(self.send_queue),
            "active_consumers": len(self.consumers),
            "active_subscriptions": len(self.receive_handlers),
            "last_connection_check": self.last_connection_check,
        }
    
    def close(self):
        """Close all Kafka connections."""
        self.logger.info("Closing Kafka connections...")
        
        # Close producer
        if self.producer:
            try:
                self.producer.flush(timeout=10)
                self.producer.close(timeout=10)
                self.logger.info("Producer closed")
            except Exception as e:
                self.logger.error(f"Error closing producer: {e}")
        
        # Close consumers
        for topic, consumer in self.consumers.items():
            try:
                consumer.close()
                self.logger.debug(f"Consumer for {topic} closed")
            except Exception as e:
                self.logger.error(f"Error closing consumer for {topic}: {e}")
        
        self.consumers.clear()
        self.receive_handlers.clear()
        self.connected = False
        
        self.logger.info("All Kafka connections closed")
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        self.close()