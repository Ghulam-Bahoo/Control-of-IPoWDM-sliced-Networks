"""
Kafka Manager for IP SDN Controller
Handles communication with SONiC agents via Kafka topics
"""

import json
import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
import threading
import time
import uuid

from kafka import KafkaProducer, KafkaConsumer, KafkaAdminClient
from kafka.admin import NewTopic
from kafka.errors import KafkaError, NoBrokersAvailable

from config.settings import settings
from models.schemas import (
    AgentCommand, SetupConnectionCommand, ReconfigConnectionCommand,
    InterfaceControlCommand, QoTelemetry
)


logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class KafkaMessageType(str, Enum):
    """Types of Kafka messages."""
    COMMAND = "COMMAND"
    TELEMETRY = "TELEMETRY"
    HEARTBEAT = "HEARTBEAT"
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
    ERROR = "ERROR"


@dataclass
class KafkaMessage:
    """Standard Kafka message format."""
    message_id: str
    message_type: KafkaMessageType
    sender_id: str
    recipient_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class KafkaManager:
    """
    Manages Kafka communication with SONiC agents.
    Handles both command publishing and telemetry consumption.
    """
    
    def __init__(self):
        """Initialize Kafka manager with configuration from settings."""
        self.broker = settings.KAFKA_BROKER
        self.config_topic = settings.CONFIG_TOPIC
        self.monitoring_topic = settings.MONITORING_TOPIC
        self.controller_id = settings.CONTROLLER_ID
        self.virtual_operator = settings.VIRTUAL_OPERATOR
        
        self.producer = None
        self.consumer = None
        self.admin_client = None
        
        self._producer_lock = threading.Lock()
        self._consumer_lock = threading.Lock()
        
        self.telemetry_callbacks = []
        self.heartbeat_callbacks = []
        self.ack_callbacks = []
        
        self._running = False
        self._consumer_thread = None
        
        self._initialize_kafka()
    
    def _initialize_kafka(self) -> None:
        """Initialize Kafka connections and ensure topics exist."""
        try:
            # Initialize admin client for topic management
            self.admin_client = KafkaAdminClient(
                bootstrap_servers=self.broker,
                client_id=f"{self.controller_id}_admin"
            )
            
            # Ensure topics exist
            self._ensure_topics()
            
            # Initialize producer
            self.producer = KafkaProducer(
                bootstrap_servers=self.broker,
                client_id=self.controller_id,
                value_serializer=lambda v: json.dumps(v, cls=DateTimeEncoder).encode('utf-8'),
                key_serializer=lambda k: k.encode('utf-8') if k else None,
                acks='all',  # Wait for all replicas to acknowledge
                retries=3,
                max_in_flight_requests_per_connection=1,
                request_timeout_ms=30000
            )
            
            # Initialize consumer for telemetry
            self.consumer = KafkaConsumer(
                self.monitoring_topic,
                bootstrap_servers=self.broker,
                client_id=f"{self.controller_id}_consumer",
                group_id=f"{self.virtual_operator}_controller",
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                key_deserializer=lambda k: k.decode('utf-8') if k else None,
                auto_offset_reset='latest',
                enable_auto_commit=True,
                auto_commit_interval_ms=5000,
                session_timeout_ms=30000,
                heartbeat_interval_ms=10000
            )
            
            logger.info(f"Kafka manager initialized for {self.virtual_operator}")
            logger.info(f"Config topic: {self.config_topic}")
            logger.info(f"Monitoring topic: {self.monitoring_topic}")
            
        except NoBrokersAvailable as e:
            logger.error(f"No Kafka brokers available at {self.broker}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize Kafka manager: {e}")
            raise
    
    def _ensure_topics(self) -> None:
        """Ensure required Kafka topics exist, create if missing."""
        try:
            existing_topics = self.admin_client.list_topics()
            topics_to_create = []
            
            if self.config_topic not in existing_topics:
                topics_to_create.append(
                    NewTopic(
                        name=self.config_topic,
                        num_partitions=3,
                        replication_factor=1
                    )
                )
                logger.info(f"Will create config topic: {self.config_topic}")
            
            if self.monitoring_topic not in existing_topics:
                topics_to_create.append(
                    NewTopic(
                        name=self.monitoring_topic,
                        num_partitions=3,
                        replication_factor=1
                    )
                )
                logger.info(f"Will create monitoring topic: {self.monitoring_topic}")
            
            if topics_to_create:
                self.admin_client.create_topics(new_topics=topics_to_create, validate_only=False)
                logger.info(f"Created topics: {[t.name for t in topics_to_create]}")
                # Wait a bit for topics to be fully created
                time.sleep(2)
            
        except Exception as e:
            logger.error(f"Failed to ensure topics exist: {e}")
            # Don't raise - we can continue if topics already exist
    
    def start_consuming(self) -> None:
        """Start consuming messages from monitoring topic."""
        if self._running:
            logger.warning("Consumer is already running")
            return
        
        self._running = True
        self._consumer_thread = threading.Thread(
            target=self._consume_messages,
            name="KafkaConsumerThread",
            daemon=True
        )
        self._consumer_thread.start()
        logger.info("Started Kafka consumer thread")
    
    def stop_consuming(self) -> None:
        """Stop consuming messages."""
        self._running = False
        if self._consumer_thread:
            self._consumer_thread.join(timeout=10)
            logger.info("Stopped Kafka consumer thread")
    
    def _consume_messages(self) -> None:
        """Main consumer loop for processing incoming messages."""
        logger.info(f"Starting to consume from {self.monitoring_topic}")
        
        while self._running:
            try:
                # Poll for messages with timeout
                message_batch = self.consumer.poll(timeout_ms=1000)
                
                for topic_partition, messages in message_batch.items():
                    for message in messages:
                        self._process_message(message)
                
            except Exception as e:
                logger.error(f"Error in consumer loop: {e}")
                if self._running:
                    time.sleep(1)  # Avoid tight loop on error
    
    def _process_message(self, message) -> None:
        """Process a single Kafka message."""
        try:
            if not message.value:
                logger.warning("Received empty message")
                return
            
            message_data = message.value
            
            # Extract message type
            msg_type = message_data.get('message_type')
            sender_id = message_data.get('sender_id')
            
            logger.debug(f"Received {msg_type} message from {sender_id}")
            
            # Route based on message type
            if msg_type == KafkaMessageType.TELEMETRY.value:
                self._handle_telemetry(message_data)
            elif msg_type == KafkaMessageType.HEARTBEAT.value:
                self._handle_heartbeat(message_data)
            elif msg_type == KafkaMessageType.ACKNOWLEDGEMENT.value:
                self._handle_acknowledgement(message_data)
            elif msg_type == KafkaMessageType.ERROR.value:
                self._handle_error(message_data)
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except Exception as e:
            logger.error(f"Failed to process message: {e}")
    
    def _handle_telemetry(self, message_data: Dict[str, Any]) -> None:
        """Handle telemetry messages from agents."""
        try:
            # Convert to QoTelemetry model
            telemetry = QoTelemetry(**message_data.get('payload', {}))
            
            # Notify registered callbacks
            for callback in self.telemetry_callbacks:
                try:
                    callback(telemetry)
                except Exception as e:
                    logger.error(f"Telemetry callback error: {e}")
            
            logger.debug(f"Processed telemetry for connection {telemetry.connection_id}")
            
        except Exception as e:
            logger.error(f"Failed to handle telemetry: {e}")
    
    def _handle_heartbeat(self, message_data: Dict[str, Any]) -> None:
        """Handle heartbeat messages from agents."""
        try:
            agent_id = message_data.get('sender_id')
            status = message_data.get('payload', {}).get('status', 'UNKNOWN')
            
            # Notify registered callbacks
            for callback in self.heartbeat_callbacks:
                try:
                    callback(agent_id, status, message_data)
                except Exception as e:
                    logger.error(f"Heartbeat callback error: {e}")
            
            logger.debug(f"Heartbeat from {agent_id}: {status}")
            
        except Exception as e:
            logger.error(f"Failed to handle heartbeat: {e}")
    
    def _handle_acknowledgement(self, message_data: Dict[str, Any]) -> None:
        """Handle acknowledgement messages from agents."""
        try:
            command_id = message_data.get('payload', {}).get('command_id')
            status = message_data.get('payload', {}).get('status', 'UNKNOWN')
            agent_id = message_data.get('sender_id')
            
            # Notify registered callbacks
            for callback in self.ack_callbacks:
                try:
                    callback(command_id, status, agent_id, message_data)
                except Exception as e:
                    logger.error(f"ACK callback error: {e}")
            
            logger.info(f"ACK from {agent_id} for command {command_id}: {status}")
            
        except Exception as e:
            logger.error(f"Failed to handle acknowledgement: {e}")
    
    def _handle_error(self, message_data: Dict[str, Any]) -> None:
        """Handle error messages from agents."""
        try:
            agent_id = message_data.get('sender_id')
            error_details = message_data.get('payload', {})
            
            logger.error(f"Error from agent {agent_id}: {error_details}")
            
            # Could trigger alerting or recovery actions here
            
        except Exception as e:
            logger.error(f"Failed to handle error message: {e}")
    
    def send_command(self, command: AgentCommand, target_agent: Optional[str] = None) -> bool:
        """
        Send a command to agents via Kafka.
        
        Args:
            command: The agent command to send
            target_agent: Specific agent ID (None for broadcast)
            
        Returns:
            True if sent successfully
        """
        try:
            # Create Kafka message
            kafka_message = KafkaMessage(
                message_id=str(uuid.uuid4()),
                message_type=KafkaMessageType.COMMAND,
                sender_id=self.controller_id,
                recipient_id=target_agent,
                payload=command.dict()
            )
            
            # Convert to dict
            message_dict = {
                'message_id': kafka_message.message_id,
                'message_type': kafka_message.message_type.value,
                'sender_id': kafka_message.sender_id,
                'recipient_id': kafka_message.recipient_id,
                'timestamp': kafka_message.timestamp.isoformat(),
                'payload': kafka_message.payload,
                'metadata': kafka_message.metadata
            }
            
            # Send to config topic
            future = self.producer.send(
                topic=self.config_topic,
                value=message_dict,
                key=target_agent  # Partition by agent ID
            )
            
            # Wait for send confirmation
            record_metadata = future.get(timeout=10)
            
            logger.info(
                f"Command {command.action} sent to {target_agent or 'all agents'}. "
                f"Topic: {record_metadata.topic}, "
                f"Partition: {record_metadata.partition}, "
                f"Offset: {record_metadata.offset}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send command {command.action}: {e}")
            return False
    
    def send_setup_command(self, connection_id: str, params: Dict[str, Any],
                          target_agent: Optional[str] = None) -> bool:
        """Send connection setup command."""
        command = SetupConnectionCommand(
            command_id=str(uuid.uuid4()),
            target_pop=params.get('pop_id'),
            parameters={
                'connection_id': connection_id,
                'params': params,
                'timestamp': datetime.utcnow().isoformat()
            }
        )
        return self.send_command(command, target_agent)
    
    def send_reconfig_command(self, connection_id: str, reason: str,
                             params: Dict[str, Any], target_agent: Optional[str] = None) -> bool:
        """Send reconfiguration command."""
        command = ReconfigConnectionCommand(
            command_id=str(uuid.uuid4()),
            target_pop=params.get('pop_id'),
            parameters={
                'connection_id': connection_id,
                'reason': reason,
                'params': params,
                'timestamp': datetime.utcnow().isoformat()
            }
        )
        return self.send_command(command, target_agent)
    
    def send_interface_command(self, action: str, interface_params: Dict[str, Any],
                              target_agent: Optional[str] = None) -> bool:
        """Send interface control command."""
        command = InterfaceControlCommand(
            command_id=str(uuid.uuid4()),
            target_pop=interface_params.get('pop_id'),
            parameters={
                'action': action,
                'params': interface_params,
                'timestamp': datetime.utcnow().isoformat()
            }
        )
        return self.send_command(command, target_agent)
    
    def register_telemetry_callback(self, callback: Callable[[QoTelemetry], None]) -> None:
        """Register callback for telemetry messages."""
        self.telemetry_callbacks.append(callback)
        logger.debug(f"Registered telemetry callback: {callback.__name__}")
    
    def register_heartbeat_callback(self, callback: Callable[[str, str, Dict], None]) -> None:
        """Register callback for heartbeat messages."""
        self.heartbeat_callbacks.append(callback)
        logger.debug(f"Registered heartbeat callback: {callback.__name__}")
    
    def register_ack_callback(self, callback: Callable[[str, str, str, Dict], None]) -> None:
        """Register callback for acknowledgement messages."""
        self.ack_callbacks.append(callback)
        logger.debug(f"Registered ACK callback: {callback.__name__}")
    
    def health_check(self) -> Dict[str, Any]:
        """Check Kafka connection health."""
        try:
            # Check producer
            producer_ok = self.producer is not None
            
            # Check consumer
            consumer_ok = self.consumer is not None and self._running
            
            # Try to list topics as connectivity test
            topics_ok = False
            if self.admin_client:
                topics = self.admin_client.list_topics()
                topics_ok = self.config_topic in topics and self.monitoring_topic in topics
            
            return {
                'status': 'healthy' if all([producer_ok, consumer_ok, topics_ok]) else 'degraded',
                'producer_connected': producer_ok,
                'consumer_running': consumer_ok,
                'topics_available': topics_ok,
                'config_topic': self.config_topic,
                'monitoring_topic': self.monitoring_topic,
                'broker': self.broker,
                'timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Kafka health check failed: {e}")
            return {
                'status': 'unhealthy',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def close(self) -> None:
        """Clean up Kafka connections."""
        self.stop_consuming()
        
        if self.producer:
            self.producer.flush(timeout=10)
            self.producer.close()
            logger.info("Kafka producer closed")
        
        if self.consumer:
            self.consumer.close()
            logger.info("Kafka consumer closed")
        
        if self.admin_client:
            self.admin_client.close()
            logger.info("Kafka admin client closed")
        
        logger.info("Kafka manager shutdown complete")
    
    def __del__(self):
        """Destructor for cleanup."""
        self.close()
