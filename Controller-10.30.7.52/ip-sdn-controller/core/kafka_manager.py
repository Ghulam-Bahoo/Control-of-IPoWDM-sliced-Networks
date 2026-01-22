# core/kafka_manager.py
"""
Kafka Manager for IP SDN Controller

Controller responsibilities:
- Produce commands to config_<vOp> (targeted to specific agent_id via key)
- Consume monitoring_<vOp> to learn:
    - agent health/heartbeats
    - telemetry updates
    - command acknowledgements
- Provide callbacks for higher layers:
    - register_heartbeat_callback(fn)
    - register_telemetry_callback(fn)
    - register_ack_callback(fn)
"""

import json
import time
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError, NoBrokersAvailable

from config.settings import settings

logger = logging.getLogger(__name__)

HeartbeatCallback = Callable[[str, str, Dict[str, Any]], None]   # (agent_id, status, message)
TelemetryCallback = Callable[[str, Dict[str, Any]], None]        # (agent_id, telemetry_message)
AckCallback = Callable[[str, str, str, Dict[str, Any]], None]    # (command_id, status, agent_id, message)


def uuid4_str() -> str:
    import uuid
    return str(uuid.uuid4())


class KafkaManager:
    def __init__(self):
        self.vop = settings.VIRTUAL_OPERATOR
        self.broker = settings.KAFKA_BROKER

        self.config_topic = settings.CONFIG_TOPIC          # e.g., config_vOp2
        self.monitoring_topic = settings.MONITORING_TOPIC  # e.g., monitoring_vOp2

        self._producer: Optional[KafkaProducer] = None
        self._consumer: Optional[KafkaConsumer] = None

        self._consume_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._heartbeat_callbacks: List[HeartbeatCallback] = []
        self._telemetry_callbacks: List[TelemetryCallback] = []
        self._ack_callbacks: List[AckCallback] = []

        self._init_clients()

        logger.info("Kafka manager initialized for %s", self.vop)
        logger.info("Config topic: %s", self.config_topic)
        logger.info("Monitoring topic: %s", self.monitoring_topic)

    # ---------------------------
    # Public callback registration
    # ---------------------------
    def register_heartbeat_callback(self, cb: HeartbeatCallback) -> None:
        self._heartbeat_callbacks.append(cb)

    def register_telemetry_callback(self, cb: TelemetryCallback) -> None:
        self._telemetry_callbacks.append(cb)

    def register_ack_callback(self, cb: AckCallback) -> None:
        self._ack_callbacks.append(cb)

    # ---------------------------
    # Init / lifecycle
    # ---------------------------
    def _init_clients(self) -> None:
        try:
            self._producer = KafkaProducer(
                bootstrap_servers=self.broker,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else None,
                acks="all",
                retries=3,
                linger_ms=10,
                request_timeout_ms=30000,
                max_in_flight_requests_per_connection=1,
            )

            # Controller consumes monitoring topic to track agents and telemetry
            self._consumer = KafkaConsumer(
                self.monitoring_topic,
                bootstrap_servers=self.broker,
                value_deserializer=lambda v: json.loads(v.decode("utf-8", errors="ignore")),
                key_deserializer=lambda k: k.decode("utf-8", errors="ignore") if k else None,
                group_id=f"controller-{self.vop}",
                auto_offset_reset="latest",
                enable_auto_commit=True,
                session_timeout_ms=30000,
                heartbeat_interval_ms=3000,
                max_poll_records=100,
                max_poll_interval_ms=300000,
            )

        except NoBrokersAvailable as e:
            logger.error("Kafka broker not available: %s", e)
            raise
        except Exception as e:
            logger.error("Kafka init failed: %s", e)
            raise

    def start_consuming(self) -> None:
        if self._consume_thread and self._consume_thread.is_alive():
            return

        self._stop_event.clear()
        self._consume_thread = threading.Thread(
            target=self._consume_loop,
            name="KafkaConsumeThread",
            daemon=True,
        )
        self._consume_thread.start()

    def stop_consuming(self) -> None:
        self._stop_event.set()
        if self._consume_thread and self._consume_thread.is_alive():
            self._consume_thread.join(timeout=5)

    def close(self) -> None:
        self.stop_consuming()

        try:
            if self._producer:
                self._producer.flush(timeout=5)
                self._producer.close(timeout=5)
                logger.info("Kafka producer closed")
        except Exception:
            pass

        try:
            if self._consumer:
                self._consumer.close()
                logger.info("Kafka consumer closed")
        except Exception:
            pass

        logger.info("Kafka manager shutdown complete")

    # ---------------------------
    # Consume loop
    # ---------------------------
    def _consume_loop(self) -> None:
        if not self._consumer:
            return

        while not self._stop_event.is_set():
            try:
                records = self._consumer.poll(timeout_ms=1000)
                for _tp, recs in (records or {}).items():
                    for r in recs:
                        msg = getattr(r, "value", None)
                        if isinstance(msg, dict):
                            self._route_monitoring_message(msg)
            except Exception as e:
                logger.warning("Kafka consume loop error: %s", e)
                time.sleep(1)

    def _route_monitoring_message(self, msg: Dict[str, Any]) -> None:
        """
        Accepts:
        - Health:
            {"type":"agentHealth","agent_id":"pop1-router1","status":"healthy",...}
          or older:
            {"type":"heartbeat","payload":{...}}
        - Telemetry:
            {"type":"telemetry","agent_id":"...","data":{...},...}
        - ACK:
            {"type":"commandAck","agent_id":"...","command_id":"...","status":"ok",...}
        """
        mtype = msg.get("type") or msg.get("message_type")
        if not mtype:
            logger.warning("Unknown message type: %s", msg.get("type"))
            return

        agent_id = msg.get("agent_id") or msg.get("payload", {}).get("agent_id") or "unknown"

        # ---- Health / Heartbeat ----
        if mtype in ("agentHealth", "agentHeartbeat", "health", "heartbeat"):
            status_raw = msg.get("status") or msg.get("payload", {}).get("status") or "unknown"
            norm = "HEALTHY"
            if isinstance(status_raw, str) and status_raw.lower() not in ("healthy", "ok", "up"):
                norm = "DEGRADED"

            envelope = {"payload": msg}
            for cb in self._heartbeat_callbacks:
                try:
                    cb(agent_id, norm, envelope)
                except Exception as e:
                    logger.debug("Heartbeat callback failed: %s", e)
            return

        # ---- Telemetry ----
        if mtype in ("telemetry", "agentTelemetry", "monitoring", "qotTelemetry"):
            # Pass the full message upstream (QoT monitor can interpret fields)
            for cb in self._telemetry_callbacks:
                try:
                    cb(agent_id, msg)
                except Exception as e:
                    logger.debug("Telemetry callback failed: %s", e)
            return

        # ---- ACK ----
        if mtype in ("commandAck", "ack", "command_ack"):
            command_id = msg.get("command_id") or msg.get("payload", {}).get("command_id") or "unknown"
            ack_status = msg.get("ack_status") or msg.get("status") or msg.get("payload", {}).get("status") or "unknown"

            for cb in self._ack_callbacks:
                try:
                    cb(str(command_id), str(ack_status), str(agent_id), msg)
                except Exception as e:
                    logger.debug("Ack callback failed: %s", e)
            return

        # Ignore other monitoring events safely
        return

    # ---------------------------
    # Produce commands (controller -> agent)
    # ---------------------------
    def _send(self, topic: str, key: Optional[str], value: Dict[str, Any]) -> bool:
        if not self._producer:
            return False
        try:
            fut = self._producer.send(topic=topic, key=key, value=value)
            fut.get(timeout=10)
            return True
        except KafkaError as e:
            logger.error("Kafka send failed: %s", e)
            return False
        except Exception as e:
            logger.error("Kafka send failed: %s", e)
            return False

    def send_command(self, command: Any, target_agent: Optional[str]) -> bool:
        if isinstance(command, dict):
            payload = command
        elif hasattr(command, "dict"):
            payload = command.dict()
        elif hasattr(command, "__dict__"):
            payload = dict(command.__dict__)
        else:
            payload = {"command": str(command)}

        return self._send(self.config_topic, target_agent, payload)

    def send_interface_command(self, action: str, interface_params: Dict[str, Any], target_agent: str) -> bool:
        msg = {
            "type": "interfaceControl",
            "command_id": uuid4_str(),
            "timestamp": time.time(),
            "target_agent": target_agent,
            "action": action,
            "parameters": interface_params,
        }
        return self._send(self.config_topic, target_agent, msg)

    def send_setup_command(self, connection_id: str, params: Dict[str, Any], target_agent: str) -> bool:
        msg = {
            "type": "setupConnection",
            "command_id": uuid4_str(),
            "timestamp": time.time(),
            "target_agent": target_agent,
            "connection_id": connection_id,
            "parameters": params,
        }
        return self._send(self.config_topic, target_agent, msg)

    def send_reconfig_command(self, connection_id: str, reason: str, params: Dict[str, Any], target_agent: str) -> bool:
        msg = {
            "type": "reconfigConnection",
            "command_id": uuid4_str(),
            "timestamp": time.time(),
            "target_agent": target_agent,
            "connection_id": connection_id,
            "reason": reason,
            "parameters": params,
        }
        return self._send(self.config_topic, target_agent, msg)

    # ---------------------------
    # Health
    # ---------------------------
    def health_check(self) -> Dict[str, Any]:
        ok = True
        err = None
        try:
            if self._producer:
                _ = self._producer.partitions_for(self.config_topic)
            if self._consumer:
                _ = self._consumer.subscription()
        except Exception as e:
            ok = False
            err = str(e)

        return {
            "status": "healthy" if ok else "unhealthy",
            "broker": self.broker,
            "config_topic": self.config_topic,
            "monitoring_topic": self.monitoring_topic,
            "error": err,
            "timestamp": time.time(),
        }

