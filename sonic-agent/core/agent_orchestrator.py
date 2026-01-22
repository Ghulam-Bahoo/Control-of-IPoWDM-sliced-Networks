# /app/core/agent_orchestrator.py

import time
import logging
from typing import Dict, Any, Optional, Union

# Your project imports (keep as in your repo)
from config.settings import settings


class AgentOrchestrator:
    """
    Orchestrates agent behavior:
    - Polls config/command messages from Kafka
    - Dispatches to handlers
    - Emits health/capabilities/acks
    """

    def __init__(self, kafka_manager, cmis_driver, telemetry_manager=None):
        self.kafka_manager = kafka_manager
        self.cmis_driver = cmis_driver
        self.telemetry_manager = telemetry_manager

        self.logger = logging.getLogger("agent-orchestrator")

        self.running = False

        # Stats
        self.commands_processed = 0
        self.commands_failed = 0

        # If you track connections
        self.active_connections = {}

    def start(self):
        self.running = True
        self.logger.info("Starting command processing loop...")
        self._command_loop()

    def stop(self):
        self.running = False
        self.logger.info("Stopping agent orchestrator...")

    def _command_loop(self):
        last_health_check = 0.0

        while self.running:
            try:
                messages = self.kafka_manager.poll_messages(timeout_ms=1000)

                for msg in messages:
                    payload = self._extract_payload(msg)
                    if not payload:
                        continue
                    self._process_message(payload)

                # periodic health
                now = time.time()
                if now - last_health_check >= 30:
                    self._send_health_check()
                    last_health_check = now

                # stats each minute (best-effort)
                if int(now) % 60 == 0:
                    self.logger.info(
                        f"Agent stats: commands={self.commands_processed}, "
                        f"failed={self.commands_failed}, "
                        f"connections={len(self.active_connections)}"
                    )

            except Exception as e:
                self.logger.error(f"Error in command loop: {e}", exc_info=True)
                time.sleep(1)

        self.logger.info("Command processing loop stopped")

    @staticmethod
    def _extract_payload(msg: Any) -> Optional[Dict[str, Any]]:
        """
        Support both:
        - KafkaMessage(topic, key, value, timestamp)  -> msg.value
        - dict payloads                              -> msg
        - kafka-python ConsumerRecord                -> msg.value (bytes/dict)
        """
        if msg is None:
            return None

        # Your current bug: msg is dict already
        if isinstance(msg, dict):
            return msg

        # If it is a wrapper object with `.value`
        if hasattr(msg, "value"):
            val = getattr(msg, "value")
            # val might already be dict depending on deserializer
            if isinstance(val, dict):
                return val
            # could be bytes/str; try JSON decode
            try:
                import json

                if isinstance(val, (bytes, bytearray)):
                    val = val.decode("utf-8", errors="ignore")
                if isinstance(val, str):
                    return json.loads(val)
            except Exception:
                return None

        return None

    def _process_message(self, message: Dict[str, Any]):
        try:
            message_type = message.get("type")
            action = message.get("action")

            # Some producers may send only "type" (no action)
            self.logger.info(f"Processing message: type={message_type}, action={action}")

            if action == "setupConnection":
                self._handle_setup_connection(message)

            elif action == "teardownConnection":
                self._handle_teardown_connection(message)

            elif action == "reconfigConnection":
                self._handle_reconfig_connection(message)

            elif action == "interfaceControl" or message_type == "interfaceControl":
                self._handle_interface_control(message)

            elif message_type == "healthCheck":
                self._handle_health_check(message)

            elif message_type == "getCapabilities":
                self._send_capabilities()

            else:
                self.logger.warning(f"Unknown message type/action: type={message_type}, action={action}")

            self.commands_processed += 1

        except Exception as e:
            self.commands_failed += 1
            self.logger.error(f"Failed to process message: {e}", exc_info=True)

    # ---------------------------
    # Handlers (stubs or existing)
    # ---------------------------

    def _handle_setup_connection(self, msg: Dict[str, Any]):
        # Keep your existing implementation here
        self.logger.info("setupConnection handler invoked")
        # ...

    def _handle_teardown_connection(self, msg: Dict[str, Any]):
        self.logger.info("teardownConnection handler invoked")
        # ...

    def _handle_reconfig_connection(self, msg: Dict[str, Any]):
        self.logger.info("reconfigConnection handler invoked")
        # ...

    def _handle_health_check(self, msg: Dict[str, Any]):
        self.logger.info("healthCheck received; replying with agent health")
        self._send_health_check()

    def _handle_interface_control(self, msg: Dict[str, Any]):
        """
        Expected controller message payload should include:
          - command_id (optional but recommended)
          - parameters: {pop_id, router_id, interface, action}
        """
        command_id = msg.get("command_id") or msg.get("id") or msg.get("commandId")
        params = msg.get("parameters") or msg.get("params") or msg

        interface = params.get("interface")
        action = params.get("action")

        if not interface or not action:
            self.logger.error(f"interfaceControl missing interface/action: {msg}")
            self._send_command_ack(command_id, "failed", "interfaceControl", {"error": "missing_interface_or_action"})
            return

        # Apply to SONiC: delegate to cmis_driver (preferred) or implement here
        try:
            result = self.cmis_driver.control_interface(interface=interface, action=action)
            status = "success" if result.get("success") else "failed"
            self._send_command_ack(command_id, status, "interfaceControl", {"result": result})
            self.logger.info(f"interfaceControl {interface} action={action} -> {status}")

        except Exception as e:
            self.logger.error(f"interfaceControl failed: {e}", exc_info=True)
            self._send_command_ack(command_id, "failed", "interfaceControl", {"error": str(e)})

    # ---------------------------
    # Outbound messages
    # ---------------------------

    def _send_capabilities(self):
        # Keep your existing implementation if present
        payload = {
            "type": "capabilities",
            "agent_id": settings.AGENT_ID,
            "pop_id": settings.POP_ID,
            "router_id": settings.ROUTER_ID,
            "virtual_operator": settings.VIRTUAL_OPERATOR,
            "timestamp": time.time(),
            "interfaces": settings.ASSIGNED_TRANSCEIVERS,
        }
        try:
            self.kafka_manager.send_monitoring_message(payload)
        except Exception:
            self.logger.debug("send_monitoring_message not available; skipping capabilities publish")

    def _send_health_check(self):
        # If you already build a richer health payload elsewhere, keep it.
        payload = {
            "type": "agentHealth",
            "agent_id": settings.AGENT_ID,
            "pop_id": settings.POP_ID,
            "router_id": settings.ROUTER_ID,
            "virtual_operator": settings.VIRTUAL_OPERATOR,
            "status": "healthy",
            "timestamp": time.time(),
        }
        try:
            self.kafka_manager.send_health_message(payload)
        except Exception:
            # fallback
            try:
                self.kafka_manager.send_monitoring_message(payload)
            except Exception:
                self.logger.debug("No health/monitoring send method available")

    def _send_command_ack(self, command_id: Optional[str], status: str, action: str, details: Dict[str, Any]):
        payload = {
            "type": "commandAck",
            "agent_id": settings.AGENT_ID,
            "pop_id": settings.POP_ID,
            "router_id": settings.ROUTER_ID,
            "virtual_operator": settings.VIRTUAL_OPERATOR,
            "command_id": command_id,
            "action": action,
            "status": status,
            "details": details,
            "timestamp": time.time(),
        }
        try:
            self.kafka_manager.send_monitoring_message(payload)
        except Exception:
            self.logger.debug("send_monitoring_message not available; ACK not published")

