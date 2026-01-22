# core/agent_dispatcher.py
"""
Agent Dispatcher for IP SDN Controller
Formats and dispatches commands to specific SONiC agents

Update:
- Do not hard-fail interface commands when agent registry is empty/offline.
- Publish commands using derived agent_id = "{pop_id}-{router_id}" as fallback.
"""

import logging
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import threading
import time

from config.settings import settings
from models.schemas import (
    AgentCommand, SetupConnectionCommand, ReconfigConnectionCommand,
    InterfaceControlCommand, EndpointConfig
)
from core.kafka_manager import KafkaManager

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


@dataclass
class AgentInfo:
    """Information about a SONiC agent."""
    agent_id: str
    pop_id: str
    router_id: str
    status: AgentStatus
    last_heartbeat: Optional[datetime] = None
    capabilities: List[str] = field(default_factory=list)
    interfaces: List[str] = field(default_factory=list)

    @property
    def is_online(self) -> bool:
        """Check if agent is online based on recent heartbeat."""
        if not self.last_heartbeat:
            return False
        return datetime.utcnow() - self.last_heartbeat < timedelta(seconds=60)


class AgentDispatcher:
    """
    Dispatches commands to specific SONiC agents.
    Manages agent state and handles command routing.
    """

    def __init__(self, kafka_manager: KafkaManager):
        self.kafka = kafka_manager
        self.agents: Dict[str, AgentInfo] = {}
        self._lock = threading.RLock()

        # Register callbacks (KafkaManager must invoke these from its consumer loop)
        self.kafka.register_heartbeat_callback(self._handle_heartbeat)
        self.kafka.register_ack_callback(self._handle_acknowledgement)

        # Start periodic cleanup
        self._cleanup_thread = threading.Thread(
            target=self._periodic_cleanup,
            name="AgentCleanupThread",
            daemon=True,
        )
        self._cleanup_thread.start()

        logger.info("Agent dispatcher initialized")

    # -------------------------
    # Helpers
    # -------------------------
    @staticmethod
    def derive_agent_id(pop_id: str, router_id: str) -> str:
        """Deterministic agent_id used across controller/agent."""
        return f"{pop_id}-{router_id}"

    def _best_effort_target_agent(self, pop_id: str, router_id: str) -> str:
        """
        Prefer registry ONLINE agent. If unavailable, fall back to derived agent_id.
        This avoids 500 errors when registry is empty.
        """
        agent = self.get_agent(pop_id, router_id)
        if agent and agent.is_online:
            return agent.agent_id
        return self.derive_agent_id(pop_id, router_id)

    # -------------------------
    # Kafka callbacks
    # -------------------------
    def _handle_heartbeat(self, agent_id: str, status: str, message_data: Dict[str, Any]) -> None:
        """Handle heartbeat messages to update agent status."""
        with self._lock:
            payload = message_data.get("payload") or message_data or {}

            pop_id = payload.get("pop_id", "unknown")
            router_id = payload.get("router_id", "unknown")

            if agent_id not in self.agents:
                self.agents[agent_id] = AgentInfo(
                    agent_id=agent_id,
                    pop_id=pop_id,
                    router_id=router_id,
                    status=AgentStatus.ONLINE if status == "HEALTHY" else AgentStatus.DEGRADED,
                    last_heartbeat=datetime.utcnow(),
                    capabilities=payload.get("capabilities", []),
                    interfaces=payload.get("interfaces", []),
                )
                logger.info("Discovered new agent: %s at %s/%s", agent_id, pop_id, router_id)
            else:
                agent = self.agents[agent_id]
                agent.last_heartbeat = datetime.utcnow()
                agent.status = AgentStatus.ONLINE if status == "HEALTHY" else AgentStatus.DEGRADED
                # refresh optional info if provided
                if "capabilities" in payload:
                    agent.capabilities = payload.get("capabilities") or agent.capabilities
                if "interfaces" in payload:
                    agent.interfaces = payload.get("interfaces") or agent.interfaces

    def _handle_acknowledgement(
        self,
        command_id: str,
        status: str,
        agent_id: str,
        message_data: Dict[str, Any],
    ) -> None:
        """Handle command acknowledgements."""
        logger.info("Command %s acknowledged by %s: %s", command_id, agent_id, status)

    def _periodic_cleanup(self) -> None:
        """Periodically clean up stale agents."""
        while True:
            time.sleep(300)
            try:
                with self._lock:
                    now = datetime.utcnow()
                    stale = []
                    for agent_id, agent in self.agents.items():
                        if agent.last_heartbeat and (now - agent.last_heartbeat) > timedelta(minutes=5):
                            stale.append(agent_id)
                    for agent_id in stale:
                        del self.agents[agent_id]
                        logger.warning("Removed stale agent: %s", agent_id)
            except Exception as e:
                logger.error("Error in agent cleanup: %s", e)

    # -------------------------
    # Agent lookups
    # -------------------------
    def get_agent(self, pop_id: str, router_id: str) -> Optional[AgentInfo]:
        """Get agent info for a specific POP and router."""
        with self._lock:
            for agent in self.agents.values():
                if agent.pop_id == pop_id and agent.router_id == router_id:
                    return agent
            return None

    def get_agents_by_pop(self, pop_id: str) -> List[AgentInfo]:
        """Get all agents for a specific POP."""
        with self._lock:
            return [agent for agent in self.agents.values() if agent.pop_id == pop_id]

    def get_online_agents(self) -> List[AgentInfo]:
        """Get all online agents."""
        with self._lock:
            return [agent for agent in self.agents.values() if agent.is_online]

    # -------------------------
    # Command dispatch
    # -------------------------
    def dispatch_setup_command(
        self,
        connection_id: str,
        source_config: EndpointConfig,
        destination_config: EndpointConfig,
        path_info: Dict[str, Any],
    ) -> Dict[str, bool]:
        """Dispatch setup commands to source and destination agents."""
        results: Dict[str, bool] = {}

        # Source agent
        source_agent = self.get_agent(source_config.pop_id, source_config.node_id)
        if source_agent and source_agent.is_online:
            params = {
                "connection_id": connection_id,
                "pop_id": source_config.pop_id,
                "router_id": source_config.node_id,
                "interface": source_config.port_id,
                "direction": "source",
                "tx_power": source_config.tx_power_level,
                "frequency": source_config.frequency_ghz,
                "modulation": source_config.modulation.value if source_config.modulation else None,
                "path_info": path_info,
            }
            success = self.kafka.send_setup_command(
                connection_id=connection_id,
                params=params,
                target_agent=source_agent.agent_id,
            )
            results[source_agent.agent_id] = success
        else:
            logger.error("Source agent not found or offline for %s/%s", source_config.pop_id, source_config.node_id)
            results["source"] = False

        # Destination agent
        dest_agent = self.get_agent(destination_config.pop_id, destination_config.node_id)
        if dest_agent and dest_agent.is_online:
            params = {
                "connection_id": connection_id,
                "pop_id": destination_config.pop_id,
                "router_id": destination_config.node_id,
                "interface": destination_config.port_id,
                "direction": "destination",
                "tx_power": destination_config.tx_power_level,
                "frequency": destination_config.frequency_ghz,
                "modulation": destination_config.modulation.value if destination_config.modulation else None,
                "path_info": path_info,
            }
            success = self.kafka.send_setup_command(
                connection_id=connection_id,
                params=params,
                target_agent=dest_agent.agent_id,
            )
            results[dest_agent.agent_id] = success
        else:
            logger.error("Destination agent not found or offline for %s/%s", destination_config.pop_id, destination_config.node_id)
            results["destination"] = False

        return results

    def dispatch_reconfig_command(
        self,
        connection_id: str,
        reason: str,
        agent_configs: List[EndpointConfig],
    ) -> Dict[str, bool]:
        """Dispatch reconfiguration commands to agents."""
        results: Dict[str, bool] = {}

        for config in agent_configs:
            agent = self.get_agent(config.pop_id, config.node_id)
            if agent and agent.is_online:
                params = {
                    "connection_id": connection_id,
                    "pop_id": config.pop_id,
                    "router_id": config.node_id,
                    "interface": config.port_id,
                    "tx_power": config.tx_power_level,
                    "frequency": config.frequency_ghz,
                    "modulation": config.modulation.value if config.modulation else None,
                    "reason": reason,
                }
                success = self.kafka.send_reconfig_command(
                    connection_id=connection_id,
                    reason=reason,
                    params=params,
                    target_agent=agent.agent_id,
                )
                results[agent.agent_id] = success
            else:
                logger.error("Agent not found or offline for %s/%s", config.pop_id, config.node_id)
                results[f"{config.pop_id}_{config.node_id}"] = False

        return results

    def dispatch_interface_command(
        self,
        action: str,
        pop_id: str,
        router_id: str,
        interface: str,
        parameters: Optional[Dict] = None,
    ) -> bool:
        """
        Dispatch interface control command to agent.

        Update:
        - If agent registry is empty/offline, do NOT fail.
        - Publish to Kafka with derived agent_id so the agent still receives it.
        """
        target_agent = self._best_effort_target_agent(pop_id, router_id)

        params = {
            "pop_id": pop_id,
            "router_id": router_id,
            "interface": interface,
            "action": action,
        }
        if parameters:
            params.update(parameters)

        success = self.kafka.send_interface_command(
            action=action,
            interface_params=params,
            target_agent=target_agent,
        )

        if success:
            logger.info(
                "Interface command '%s' published to agent_id=%s (pop=%s router=%s iface=%s)",
                action,
                target_agent,
                pop_id,
                router_id,
                interface,
            )
        else:
            logger.error(
                "Failed to publish interface command '%s' to agent_id=%s",
                action,
                target_agent,
            )

        return success

    def broadcast_discovery(self) -> bool:
        """Broadcast discovery message to find all agents."""
        command = AgentCommand(
            command_id=str(uuid.uuid4()),
            action="discover",
            parameters={
                "controller_id": settings.CONTROLLER_ID,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        success = self.kafka.send_command(command, target_agent=None)
        if success:
            logger.info("Discovery broadcast sent to all agents")
        else:
            logger.error("Failed to send discovery broadcast")
        return success

    # -------------------------
    # Status / health
    # -------------------------
    def get_agent_status(self) -> Dict[str, Any]:
        """Get status of all agents."""
        with self._lock:
            online_count = sum(1 for agent in self.agents.values() if agent.is_online)
            offline_count = len(self.agents) - online_count
            return {
                "total_agents": len(self.agents),
                "online_agents": online_count,
                "offline_agents": offline_count,
                "agents_by_pop": self._get_agents_by_pop(),
                "timestamp": datetime.utcnow().isoformat(),
            }

    def _get_agents_by_pop(self) -> Dict[str, List[str]]:
        """Group agent IDs by POP."""
        result: Dict[str, List[str]] = {}
        for agent in self.agents.values():
            result.setdefault(agent.pop_id, []).append(agent.agent_id)
        return result

    def health_check(self) -> Dict[str, Any]:
        """Check agent dispatcher health."""
        agent_status = self.get_agent_status()
        return {
            "status": "healthy",
            "agent_status": agent_status,
            "kafka_health": self.kafka.health_check(),
            "timestamp": datetime.utcnow().isoformat(),
        }

