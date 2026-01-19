#!/usr/bin/env python3
import os
import json
import time
import logging
import sys
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

from kafka import KafkaConsumer, KafkaProducer

# ----------------------------
# ENV
# ----------------------------
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "10.30.7.52:9092")

VIRTUAL_OPERATOR = os.getenv("VIRTUAL_OPERATOR", "vOp1")
CONFIG_TOPIC = os.getenv("CONFIG_TOPIC", f"config_{VIRTUAL_OPERATOR}")
MONITORING_TOPIC = os.getenv("MONITORING_TOPIC", f"monitoring_{VIRTUAL_OPERATOR}")

# Trigger rules (tune later)
OSNR_THRESHOLD = float(os.getenv("OSNR_THRESHOLD", "18.0"))          # dB
BER_THRESHOLD = float(os.getenv("BER_THRESHOLD", "1e-3"))            # pre-FEC BER
PERSISTENCY_SAMPLES = int(os.getenv("PERSISTENCY_SAMPLES", "3"))     # paper-like
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "20"))                 # avoid oscillations
TX_STEP_DB = float(os.getenv("TX_STEP_DB", "1.0"))                  # step size

# Safety caps (controller-side policy)
TX_MIN_DBM = float(os.getenv("TX_MIN_DBM", "-15.0"))
TX_MAX_DBM = float(os.getenv("TX_MAX_DBM", "0.0"))

# Which endpoints to adjust on degradation: "both" or "one"
ADJUST_MODE = os.getenv("ADJUST_MODE", "both").lower()

CONSUMER_GROUP_ID = os.getenv(
    "CONSUMER_GROUP_ID", f"controller-{VIRTUAL_OPERATOR}-{int(time.time())}"
)

# ----------------------------
# LOGGING
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("sdn-controller")


@dataclass
class ConnState:
    bad_count: int = 0
    last_action_ts: float = 0.0
    last_seen_ts: float = 0.0
    last_fields_by_agent: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # store last known commanded tx power per endpoint interface
    last_tx_by_endpoint: Dict[str, float] = field(default_factory=dict)


class SDNController:
    def __init__(self):
        log.info(f"Controller starting for {VIRTUAL_OPERATOR}")
        log.info(f"KAFKA_BROKER={KAFKA_BROKER}")
        log.info(f"MONITORING_TOPIC={MONITORING_TOPIC}")
        log.info(f"CONFIG_TOPIC={CONFIG_TOPIC}")
        log.info(f"Rules: OSNR<{OSNR_THRESHOLD} OR BER>{BER_THRESHOLD}, "
                 f"persistency={PERSISTENCY_SAMPLES}, cooldown={COOLDOWN_SEC}s, tx_step={TX_STEP_DB}dB, mode={ADJUST_MODE}")

        self.state: Dict[str, ConnState] = {}

        self.producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda m: json.dumps(m).encode("utf-8"),
            retries=3,
            request_timeout_ms=30000,
            max_block_ms=60000,
            api_version=(2, 6, 0),
        )

        self.consumer = KafkaConsumer(
            MONITORING_TOPIC,
            bootstrap_servers=KAFKA_BROKER,
            group_id=CONSUMER_GROUP_ID,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
            auto_commit_interval_ms=5000,
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
            max_poll_interval_ms=300000,
            max_poll_records=50,
            api_version=(2, 6, 0),
        )

    # ----------------------------
    # Helpers
    # ----------------------------
    def _get_conn(self, connection_id: str) -> ConnState:
        if connection_id not in self.state:
            self.state[connection_id] = ConnState()
        return self.state[connection_id]

    def _is_degraded(self, fields: Dict[str, Any]) -> bool:
        """Return True if fields indicate QoT degradation."""
        osnr = fields.get("osnr")
        ber = fields.get("pre_fec_ber")

        bad_osnr = (isinstance(osnr, (int, float)) and osnr < OSNR_THRESHOLD)
        bad_ber = (isinstance(ber, (int, float)) and ber > BER_THRESHOLD)

        return bad_osnr or bad_ber

    def _clamp(self, v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    def _choose_endpoints(self, conn: ConnState) -> Dict[str, Dict[str, str]]:
        """
        Identify endpoints from last_seen telemetry.
        Returns mapping:
          endpoint_key -> {"pop_id":..., "router_id":..., "port_id":..., "agent_id":...}
        """
        endpoints = {}
        for agent_id, rec in conn.last_fields_by_agent.items():
            # telemetry payload structure coming from agent:
            # msg["data"] contains "telemetry": {connection_id, agent_id, interface, fields...}
            interface = rec.get("_interface")
            pop_id = rec.get("_pop_id")
            router_id = rec.get("_router_id")
            if interface and pop_id and router_id:
                endpoint_key = f"{pop_id}/{router_id}/{interface}"
                endpoints[endpoint_key] = {
                    "pop_id": pop_id,
                    "node_id": router_id,
                    "port_id": interface,
                    "agent_id": agent_id,
                }
        return endpoints

    # ----------------------------
    # Reconfiguration action
    # ----------------------------
    def _publish_reconfig(self, connection_id: str, endpoints: Dict[str, Dict[str, str]], reason: str):
        """
        Publish reconfig as a controller command on config topic.
        We'll use action='reconfigConnection' with endpoint_config list.
        Agents can later implement CMIS writes for tx_power_level.
        """
        # Decide which endpoints to adjust
        selected = list(endpoints.values())
        if ADJUST_MODE == "one" and selected:
            selected = [selected[0]]

        endpoint_config = []
        for ep in selected:
            # Track last commanded tx per endpoint; if unknown, start at -2 dBm
            key = f"{ep['pop_id']}/{ep['node_id']}/{ep['port_id']}"
            prev = self.state[connection_id].last_tx_by_endpoint.get(key, -2.0)
            new_tx = self._clamp(prev + TX_STEP_DB, TX_MIN_DBM, TX_MAX_DBM)
            self.state[connection_id].last_tx_by_endpoint[key] = new_tx

            endpoint_config.append({
                "pop_id": ep["pop_id"],
                "node_id": ep["node_id"],
                "port_id": ep["port_id"],
                "tx_power_level": new_tx
            })

        cmd = {
            "action": "reconfigConnection",
            "command_id": f"reconfig-{connection_id}-{int(time.time())}",
            "target_pop": "all",  # each agent filters by pop_id if you add that logic
            "parameters": {
                "connection_id": connection_id,
                "reason": reason,
                "endpoint_config": endpoint_config
            }
        }

        log.warning(f"[RECONFIG] conn={connection_id} reason={reason} endpoints={endpoint_config}")
        self.producer.send(CONFIG_TOPIC, value=cmd)
        self.producer.flush(5)

    # ----------------------------
    # Main loop
    # ----------------------------
    def run(self):
        log.info("Controller running. Listening for telemetry...")
        while True:
            batch = self.consumer.poll(timeout_ms=2000, max_records=50)
            now = time.time()

            for _, messages in batch.items():
                for m in messages:
                    msg = m.value

                    # We only care about telemetry type
                    if msg.get("type") != "telemetry":
                        continue

                    data = msg.get("data", {})
                    connection_id = data.get("connection_id")
                    fields = (data.get("fields") or {})
                    agent_id = msg.get("agent_id")
                    pop_id = msg.get("pop_id")
                    router_id = msg.get("router_id")
                    interface = data.get("interface")

                    if not connection_id or not agent_id or not interface:
                        continue

                    conn = self._get_conn(connection_id)
                    conn.last_seen_ts = now

                    # Store last fields per agent with metadata
                    stored = dict(fields)
                    stored["_interface"] = interface
                    stored["_pop_id"] = pop_id
                    stored["_router_id"] = router_id
                    conn.last_fields_by_agent[agent_id] = stored

                    degraded = self._is_degraded(fields)

                    if degraded:
                        conn.bad_count += 1
                    else:
                        # reset bad count on healthy sample
                        conn.bad_count = 0

                    # Persistency check
                    if conn.bad_count >= PERSISTENCY_SAMPLES:
                        # Cooldown check
                        if (now - conn.last_action_ts) < COOLDOWN_SEC:
                            continue

                        endpoints = self._choose_endpoints(conn)
                        if not endpoints:
                            continue

                        reason = {
                            "bad_count": conn.bad_count,
                            "osnr": fields.get("osnr"),
                            "pre_fec_ber": fields.get("pre_fec_ber"),
                            "interface": interface,
                            "agent_id": agent_id,
                        }
                        self._publish_reconfig(connection_id, endpoints, reason=str(reason))
                        conn.last_action_ts = now
                        conn.bad_count = 0  # reset after action


if __name__ == "__main__":
    SDNController().run()

