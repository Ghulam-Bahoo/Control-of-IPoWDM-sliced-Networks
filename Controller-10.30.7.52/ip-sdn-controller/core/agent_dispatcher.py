"""
Agent Dispatcher for IP SDN Controller
Formats and dispatches commands to specific SONiC agents
"""

import json
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
        
        # Consider agent offline if no heartbeat in last 60 seconds
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
        
        # Register callbacks
        self.kafka.register_heartbeat_callback(self._handle_heartbeat)
        self.kafka.register_ack_callback(self._handle_acknowledgement)
        
        # Start periodic cleanup
        self._cleanup_thread = threading.Thread(
            target=self._periodic_cleanup,
            name="AgentCleanupThread",
            daemon=True
        )
        self._cleanup_thread.start()
        
        logger.info("Agent dispatcher initialized")
    
    def _handle_heartbeat(self, agent_id: str, status: str, message_data: Dict[str, Any]) -> None:
        """Handle heartbeat messages to update agent status."""
        with self._lock:
            if agent_id not in self.agents:
                # Extract agent info from heartbeat
                pop_id = message_data.get('payload', {}).get('pop_id', 'unknown')
                router_id = message_data.get('payload', {}).get('router_id', 'unknown')
                
                self.agents[agent_id] = AgentInfo(
                    agent_id=agent_id,
                    pop_id=pop_id,
                    router_id=router_id,
                    status=AgentStatus.ONLINE if status == 'HEALTHY' else AgentStatus.DEGRADED,
                    last_heartbeat=datetime.utcnow(),
                    capabilities=message_data.get('payload', {}).get('capabilities', []),
                    interfaces=message_data.get('payload', {}).get('interfaces', [])
                )
                logger.info(f"Discovered new agent: {agent_id} at {pop_id}/{router_id}")
            else:
                # Update existing agent
                agent = self.agents[agent_id]
                agent.last_heartbeat = datetime.utcnow()
                agent.status = AgentStatus.ONLINE if status == 'HEALTHY' else AgentStatus.DEGRADED
                logger.debug(f"Heartbeat from agent {agent_id}")
    
    def _handle_acknowledgement(self, command_id: str, status: str, 
                               agent_id: str, message_data: Dict[str, Any]) -> None:
        """Handle command acknowledgements."""
        logger.info(f"Command {command_id} acknowledged by {agent_id}: {status}")
        
        # Could track command completion here
        # In production, you'd update command state in database
    
    def _periodic_cleanup(self) -> None:
        """Periodically clean up stale agents."""
        while True:
            time.sleep(300)  # Run every 5 minutes
            
            try:
                with self._lock:
                    now = datetime.utcnow()
                    stale_agents = []
                    
                    for agent_id, agent in self.agents.items():
                        if agent.last_heartbeat and (now - agent.last_heartbeat) > timedelta(minutes=5):
                            stale_agents.append(agent_id)
                    
                    for agent_id in stale_agents:
                        del self.agents[agent_id]
                        logger.warning(f"Removed stale agent: {agent_id}")
                        
            except Exception as e:
                logger.error(f"Error in agent cleanup: {e}")
    
    def get_agent(self, pop_id: str, router_id: str) -> Optional[AgentInfo]:
        """
        Get agent info for a specific POP and router.
        
        Args:
            pop_id: POP identifier
            router_id: Router identifier
            
        Returns:
            AgentInfo if found, None otherwise
        """
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
    
    def dispatch_setup_command(self, connection_id: str, 
                              source_config: EndpointConfig,
                              destination_config: EndpointConfig,
                              path_info: Dict[str, Any]) -> Dict[str, bool]:
        """
        Dispatch setup commands to source and destination agents.
        
        Args:
            connection_id: Connection identifier
            source_config: Source endpoint configuration
            destination_config: Destination endpoint configuration
            path_info: Path information including spectrum slots
            
        Returns:
            Dictionary mapping agent_id -> success status
        """
        results = {}
        
        # Dispatch to source agent
        source_agent = self.get_agent(source_config.pop_id, source_config.node_id)
        if source_agent and source_agent.is_online:
            params = {
                'connection_id': connection_id,
                'pop_id': source_config.pop_id,
                'router_id': source_config.node_id,
                'interface': source_config.port_id,
                'direction': 'source',
                'tx_power': source_config.tx_power_level,
                'frequency': source_config.frequency_ghz,
                'modulation': source_config.modulation.value if source_config.modulation else None,
                'path_info': path_info
            }
            
            success = self.kafka.send_setup_command(
                connection_id=connection_id,
                params=params,
                target_agent=source_agent.agent_id
            )
            results[source_agent.agent_id] = success
            
            if success:
                logger.info(f"Setup command sent to source agent {source_agent.agent_id}")
            else:
                logger.error(f"Failed to send setup command to source agent {source_agent.agent_id}")
        else:
            logger.error(f"Source agent not found or offline for {source_config.pop_id}/{source_config.node_id}")
            results['source'] = False
        
        # Dispatch to destination agent
        dest_agent = self.get_agent(destination_config.pop_id, destination_config.node_id)
        if dest_agent and dest_agent.is_online:
            params = {
                'connection_id': connection_id,
                'pop_id': destination_config.pop_id,
                'router_id': destination_config.node_id,
                'interface': destination_config.port_id,
                'direction': 'destination',
                'tx_power': destination_config.tx_power_level,
                'frequency': destination_config.frequency_ghz,
                'modulation': destination_config.modulation.value if destination_config.modulation else None,
                'path_info': path_info
            }
            
            success = self.kafka.send_setup_command(
                connection_id=connection_id,
                params=params,
                target_agent=dest_agent.agent_id
            )
            results[dest_agent.agent_id] = success
            
            if success:
                logger.info(f"Setup command sent to destination agent {dest_agent.agent_id}")
            else:
                logger.error(f"Failed to send setup command to destination agent {dest_agent.agent_id}")
        else:
            logger.error(f"Destination agent not found or offline for {destination_config.pop_id}/{destination_config.node_id}")
            results['destination'] = False
        
        return results
    
    def dispatch_reconfig_command(self, connection_id: str, reason: str,
                                 agent_configs: List[EndpointConfig]) -> Dict[str, bool]:
        """
        Dispatch reconfiguration commands to agents.
        
        Args:
            connection_id: Connection identifier
            reason: Reason for reconfiguration
            agent_configs: List of endpoint configurations to update
            
        Returns:
            Dictionary mapping agent_id -> success status
        """
        results = {}
        
        for config in agent_configs:
            agent = self.get_agent(config.pop_id, config.node_id)
            if agent and agent.is_online:
                params = {
                    'connection_id': connection_id,
                    'pop_id': config.pop_id,
                    'router_id': config.node_id,
                    'interface': config.port_id,
                    'tx_power': config.tx_power_level,
                    'frequency': config.frequency_ghz,
                    'modulation': config.modulation.value if config.modulation else None,
                    'reason': reason
                }
                
                success = self.kafka.send_reconfig_command(
                    connection_id=connection_id,
                    reason=reason,
                    params=params,
                    target_agent=agent.agent_id
                )
                results[agent.agent_id] = success
                
                if success:
                    logger.info(f"Reconfig command sent to agent {agent.agent_id} for {connection_id}")
                else:
                    logger.error(f"Failed to send reconfig command to agent {agent.agent_id}")
            else:
                logger.error(f"Agent not found or offline for {config.pop_id}/{config.node_id}")
                results[f"{config.pop_id}_{config.node_id}"] = False
        
        return results
    
    def dispatch_interface_command(self, action: str, pop_id: str, 
                                  router_id: str, interface: str,
                                  parameters: Optional[Dict] = None) -> bool:
        """
        Dispatch interface control command to agent.
        
        Args:
            action: Interface action (up, down, admin_down, etc.)
            pop_id: POP identifier
            router_id: Router identifier
            interface: Interface name
            parameters: Additional parameters
            
        Returns:
            True if command sent successfully
        """
        agent = self.get_agent(pop_id, router_id)
        if not agent or not agent.is_online:
            logger.error(f"Agent not found or offline for {pop_id}/{router_id}")
            return False
        
        params = {
            'pop_id': pop_id,
            'router_id': router_id,
            'interface': interface,
            'action': action
        }
        
        if parameters:
            params.update(parameters)
        
        success = self.kafka.send_interface_command(
            action=action,
            interface_params=params,
            target_agent=agent.agent_id
        )
        
        if success:
            logger.info(f"Interface command '{action}' sent to agent {agent.agent_id}")
        else:
            logger.error(f"Failed to send interface command to agent {agent.agent_id}")
        
        return success
    
    def broadcast_discovery(self) -> bool:
        """
        Broadcast discovery message to find all agents.
        
        Returns:
            True if broadcast sent successfully
        """
        command = AgentCommand(
            command_id=str(uuid.uuid4()),
            action="discover",
            parameters={
                'controller_id': settings.CONTROLLER_ID,
                'timestamp': datetime.utcnow().isoformat()
            }
        )
        
        # Send to all agents (no specific target)
        success = self.kafka.send_command(command, target_agent=None)
        
        if success:
            logger.info("Discovery broadcast sent to all agents")
        else:
            logger.error("Failed to send discovery broadcast")
        
        return success
    
    def get_agent_status(self) -> Dict[str, Any]:
        """Get status of all agents."""
        with self._lock:
            online_count = sum(1 for agent in self.agents.values() if agent.is_online)
            offline_count = len(self.agents) - online_count
            
            return {
                'total_agents': len(self.agents),
                'online_agents': online_count,
                'offline_agents': offline_count,
                'agents_by_pop': self._get_agents_by_pop(),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _get_agents_by_pop(self) -> Dict[str, List[str]]:
        """Group agent IDs by POP."""
        result = {}
        for agent in self.agents.values():
            if agent.pop_id not in result:
                result[agent.pop_id] = []
            result[agent.pop_id].append(agent.agent_id)
        return result
    
    def health_check(self) -> Dict[str, Any]:
        """Check agent dispatcher health."""
        agent_status = self.get_agent_status()
        
        return {
            'status': 'healthy',
            'agent_status': agent_status,
            'kafka_health': self.kafka.health_check(),
            'timestamp': datetime.utcnow().isoformat()
        }