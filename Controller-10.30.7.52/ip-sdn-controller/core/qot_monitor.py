"""
QoT Monitor for IP SDN Controller
Implements QoT degradation detection and reconfiguration logic (Case 3 from paper)
"""

import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import threading
from collections import deque

from config.settings import settings
from models.schemas import QoTelemetry, ConnectionStatus, ReconfigReason
from core.connection_manager import ConnectionManager
from core.kafka_manager import KafkaManager
from core.agent_dispatcher import AgentDispatcher


logger = logging.getLogger(__name__)


class DegradationLevel(str, Enum):
    """Degradation levels based on paper thresholds."""
    NORMAL = "NORMAL"
    WARNING = "WARNING"      # OSNR < 20 dB or BER > 1e-4
    DEGRADED = "DEGRADED"    # OSNR < 18 dB or BER > 1e-3 (paper thresholds)
    CRITICAL = "CRITICAL"    # OSNR < 15 dB or BER > 1e-2


@dataclass
class QoTSample:
    """Single QoT measurement sample."""
    timestamp: datetime
    osnr: Optional[float] = None
    pre_fec_ber: Optional[float] = None
    post_fec_ber: Optional[float] = None
    tx_power: Optional[float] = None
    rx_power: Optional[float] = None
    
    @property
    def is_valid(self) -> bool:
        """Check if sample has valid metrics."""
        return self.osnr is not None or self.pre_fec_ber is not None


@dataclass 
class ConnectionQoTState:
    """QoT state for a single connection."""
    connection_id: str
    samples: deque = field(default_factory=lambda: deque(maxlen=100))
    degradation_level: DegradationLevel = DegradationLevel.NORMAL
    last_degradation_time: Optional[datetime] = None
    reconfig_count: int = 0
    last_reconfig_time: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    
    def add_sample(self, telemetry: QoTelemetry) -> None:
        """Add a new QoT sample."""
        sample = QoTSample(
            timestamp=telemetry.timestamp,
            osnr=telemetry.osnr,
            pre_fec_ber=telemetry.pre_fec_ber,
            post_fec_ber=telemetry.post_fec_ber,
            tx_power=telemetry.tx_power,
            rx_power=telemetry.rx_power
        )
        self.samples.append(sample)
    
    def get_recent_samples(self, count: int = 10) -> List[QoTSample]:
        """Get most recent samples."""
        return list(self.samples)[-count:]
    
    @property
    def is_in_cooldown(self) -> bool:
        """Check if connection is in reconfiguration cooldown."""
        if not self.cooldown_until:
            return False
        return datetime.utcnow() < self.cooldown_until


class QoTMonitor:
    """
    Monitors QoT metrics and triggers reconfigurations (Case 3 from paper).
    
    Paper Specifications:
    - OSNR threshold: 18 dB (degradation), 15 dB (critical)
    - BER threshold: 1e-3 (pre-FEC)
    - Persistency: 3 consecutive samples
    - Cooldown: 20 seconds between reconfigurations
    - Tx adjustment: ±1 dB steps, min -15 dBm, max 0 dBm
    - Adjustment mode: 'both' (both ends), 'source', or 'destination'
    """
    
    def __init__(self, connection_manager: ConnectionManager, 
                 kafka_manager: KafkaManager,
                 agent_dispatcher: AgentDispatcher):
        """
        Initialize QoT Monitor.
        
        Args:
            connection_manager: For updating connection states
            kafka_manager: For receiving telemetry
            agent_dispatcher: For sending reconfiguration commands
        """
        self.conn_manager = connection_manager
        self.kafka = kafka_manager
        self.agent_dispatcher = agent_dispatcher
        
        self.connection_states: Dict[str, ConnectionQoTState] = {}
        self._lock = threading.RLock()
        
        # Configuration from paper
        self.osnr_threshold = settings.OSNR_THRESHOLD  # 18 dB
        self.critical_osnr_threshold = 15.0  # Critical level
        self.ber_threshold = settings.BER_THRESHOLD  # 1e-3
        self.persistency_samples = settings.PERSISTENCY_SAMPLES  # 3 samples
        self.cooldown_sec = settings.COOLDOWN_SEC  # 20 seconds
        self.tx_step_db = settings.TX_STEP_DB  # 1 dB
        self.tx_min_dbm = settings.TX_MIN_DBM  # -15 dBm
        self.tx_max_dbm = settings.TX_MAX_DBM  # 0 dBm
        self.adjust_mode = settings.ADJUST_MODE  # 'both', 'source', 'destination'
        
        # Register telemetry callback
        self.kafka.register_telemetry_callback(self._handle_telemetry)
        
        # Start monitoring thread
        self._monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            name="QoTMonitorThread",
            daemon=True
        )
        self._monitoring_thread.start()
        
        logger.info("QoT Monitor initialized with paper specifications")
        logger.info(f"OSNR threshold: {self.osnr_threshold} dB")
        logger.info(f"BER threshold: {self.ber_threshold}")
        logger.info(f"Persistency samples: {self.persistency_samples}")
        logger.info(f"Cooldown: {self.cooldown_sec} seconds")
    
    def _handle_telemetry(self, telemetry: QoTelemetry) -> None:
        """
        Handle incoming telemetry from agents.
        
        Args:
            telemetry: QoT metrics from SONiC agent
        """
        with self._lock:
            # Get or create connection state
            if telemetry.connection_id not in self.connection_states:
                self.connection_states[telemetry.connection_id] = ConnectionQoTState(
                    connection_id=telemetry.connection_id
                )
            
            state = self.connection_states[telemetry.connection_id]
            state.add_sample(telemetry)
            
            # Check for degradation
            self._check_degradation(telemetry.connection_id, state)
    
    def _check_degradation(self, connection_id: str, state: ConnectionQoTState) -> None:
        """
        Check if connection is degraded based on recent samples.
        
        Args:
            connection_id: Connection identifier
            state: Connection QoT state
        """
        if state.is_in_cooldown:
            logger.debug(f"Connection {connection_id} in cooldown, skipping check")
            return
        
        # Get recent samples
        recent_samples = state.get_recent_samples(self.persistency_samples)
        if len(recent_samples) < self.persistency_samples:
            return  # Not enough samples for persistency check
        
        # Check all samples meet degradation criteria
        degraded_samples = 0
        critical_samples = 0
        
        for sample in recent_samples:
            if not sample.is_valid:
                continue
            
            # Check OSNR degradation (paper: OSNR < 18 dB)
            if sample.osnr is not None:
                if sample.osnr < self.critical_osnr_threshold:
                    critical_samples += 1
                elif sample.osnr < self.osnr_threshold:
                    degraded_samples += 1
            
            # Check BER degradation (paper: BER > 1e-3)
            if sample.pre_fec_ber is not None:
                if sample.pre_fec_ber > self.ber_threshold * 10:  # Critical: 10x threshold
                    critical_samples += 1
                elif sample.pre_fec_ber > self.ber_threshold:
                    degraded_samples += 1
        
        # Determine degradation level
        if critical_samples >= self.persistency_samples:
            new_level = DegradationLevel.CRITICAL
        elif degraded_samples >= self.persistency_samples:
            new_level = DegradationLevel.DEGRADED
        else:
            new_level = DegradationLevel.NORMAL
        
        # Check if degradation level changed
        if new_level != state.degradation_level:
            old_level = state.degradation_level
            state.degradation_level = new_level
            
            if new_level in [DegradationLevel.DEGRADED, DegradationLevel.CRITICAL]:
                state.last_degradation_time = datetime.utcnow()
                logger.warning(
                    f"Connection {connection_id} degraded: {old_level} -> {new_level}. "
                    f"OSNR: {recent_samples[-1].osnr} dB, "
                    f"BER: {recent_samples[-1].pre_fec_ber}"
                )
                
                # Mark connection as degraded in connection manager
                connection = self.conn_manager.get_connection(connection_id)
                if connection and connection.status == ConnectionStatus.ACTIVE:
                    # Get latest telemetry for details
                    latest_telemetry = QoTelemetry(
                        connection_id=connection_id,
                        agent_id="qot_monitor",
                        pop_id=connection.source_pop,
                        router_id="monitor",
                        interface="N/A",
                        timestamp=datetime.utcnow(),
                        osnr=recent_samples[-1].osnr,
                        pre_fec_ber=recent_samples[-1].pre_fec_ber
                    )
                    self.conn_manager.mark_degraded(connection_id, latest_telemetry)
                    
                    # Trigger reconfiguration
                    self._trigger_reconfiguration(connection_id, state)
            else:
                logger.info(f"Connection {connection_id} recovered: {old_level} -> {new_level}")
    
    def _trigger_reconfiguration(self, connection_id: str, state: ConnectionQoTState) -> None:
        """
        Trigger reconfiguration for degraded connection (Case 3 from paper).
        
        Args:
            connection_id: Connection identifier
            state: Connection QoT state
        """
        try:
            # Check if we should reconfigure
            if state.reconfig_count >= 3:  # Max 3 reconfig attempts
                logger.warning(f"Max reconfig attempts reached for {connection_id}")
                return
            
            if state.is_in_cooldown:
                logger.debug(f"Connection {connection_id} in cooldown")
                return
            
            # Get connection details
            connection = self.conn_manager.get_connection(connection_id)
            if not connection:
                logger.error(f"Connection {connection_id} not found")
                return
            
            # Start reconfiguration in connection manager
            if self.conn_manager.start_reconfiguration(
                connection_id, 
                ReconfigReason.QOT_DEGRADATION
            ):
                logger.info(f"Starting reconfiguration for {connection_id}")
                
                # Calculate new Tx power (paper: ±1 dB adjustment)
                current_samples = state.get_recent_samples(5)
                if current_samples:
                    latest = current_samples[-1]
                    
                    # Determine adjustment direction based on metrics
                    adjustment = self._calculate_power_adjustment(latest)
                    
                    # Apply reconfiguration
                    success = self._apply_reconfiguration(connection, adjustment)
                    
                    if success:
                        state.reconfig_count += 1
                        state.last_reconfig_time = datetime.utcnow()
                        state.cooldown_until = datetime.utcnow() + timedelta(seconds=self.cooldown_sec)
                        
                        # Mark reconfiguration as completed
                        self.conn_manager.complete_reconfiguration(connection_id)
                        logger.info(f"Reconfiguration completed for {connection_id}")
                    else:
                        logger.error(f"Reconfiguration failed for {connection_id}")
                        self.conn_manager.mark_degraded(connection_id, None)  # Stay degraded
            
        except Exception as e:
            logger.error(f"Error in reconfiguration for {connection_id}: {e}")
    
    def _calculate_power_adjustment(self, sample: QoTSample) -> Dict[str, Any]:
        """
        Calculate Tx power adjustment based on QoT metrics (paper algorithm).
        
        Args:
            sample: Latest QoT sample
            
        Returns:
            Adjustment parameters
        """
        adjustment = {
            'source_tx_adjustment': 0,
            'destination_tx_adjustment': 0,
            'reason': 'unknown'
        }
        
        # Paper logic: Adjust based on OSNR and BER
        if sample.osnr is not None:
            if sample.osnr < self.osnr_threshold:
                # Low OSNR: Increase Tx power
                adjustment['source_tx_adjustment'] = self.tx_step_db
                adjustment['destination_tx_adjustment'] = self.tx_step_db
                adjustment['reason'] = f'low_osnr_{sample.osnr:.1f}dB'
            elif sample.osnr > (self.osnr_threshold + 3):  # Good margin
                # High OSNR: Could reduce power for efficiency
                adjustment['source_tx_adjustment'] = -self.tx_step_db
                adjustment['destination_tx_adjustment'] = -self.tx_step_db
                adjustment['reason'] = f'high_osnr_{sample.osnr:.1f}dB'
        
        elif sample.pre_fec_ber is not None:
            if sample.pre_fec_ber > self.ber_threshold:
                # High BER: Increase Tx power
                adjustment['source_tx_adjustment'] = self.tx_step_db
                adjustment['destination_tx_adjustment'] = self.tx_step_db
                adjustment['reason'] = f'high_ber_{sample.pre_fec_ber:.2e}'
        
        # Apply mode-specific adjustments
        if self.adjust_mode == 'source':
            adjustment['destination_tx_adjustment'] = 0
        elif self.adjust_mode == 'destination':
            adjustment['source_tx_adjustment'] = 0
        
        return adjustment
    
    def _apply_reconfiguration(self, connection, adjustment: Dict[str, Any]) -> bool:
        """
        Apply reconfiguration to connection endpoints.
        
        Args:
            connection: Connection object
            adjustment: Power adjustment parameters
            
        Returns:
            True if reconfiguration applied successfully
        """
        try:
            # In a real implementation, this would:
            # 1. Calculate new Tx power levels
            # 2. Send reconfig commands to agents
            # 3. Update Link Database
            
            logger.info(
                f"Applying reconfiguration to {connection.connection_id}: "
                f"src +{adjustment['source_tx_adjustment']}dB, "
                f"dst +{adjustment['destination_tx_adjustment']}dB, "
                f"reason: {adjustment['reason']}"
            )
            
            # Here you would implement actual agent communication
            # For now, simulate success
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply reconfiguration: {e}")
            return False
    
    def _monitoring_loop(self) -> None:
        """Background monitoring loop."""
        logger.info("QoT monitoring loop started")
        
        while True:
            try:
                self._check_all_connections()
                time.sleep(5)  # Check every 5 seconds
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(1)
    
    def _check_all_connections(self) -> None:
        """Periodic check of all active connections."""
        with self._lock:
            # Get all active connections
            active_connections = self.conn_manager.list_connections(
                status_filter=ConnectionStatus.ACTIVE
            )
            
            for conn in active_connections:
                if conn.connection_id in self.connection_states:
                    state = self.connection_states[conn.connection_id]
                    
                    # Check for recovery
                    if (state.degradation_level in [DegradationLevel.DEGRADED, DegradationLevel.CRITICAL] and
                        not state.is_in_cooldown):
                        
                        # Check if metrics have recovered
                        recent_samples = state.get_recent_samples(self.persistency_samples)
                        if len(recent_samples) >= self.persistency_samples:
                            recovered = all(
                                (s.osnr is None or s.osnr >= self.osnr_threshold) and
                                (s.pre_fec_ber is None or s.pre_fec_ber <= self.ber_threshold)
                                for s in recent_samples
                            )
                            
                            if recovered:
                                state.degradation_level = DegradationLevel.NORMAL
                                logger.info(f"Connection {conn.connection_id} automatically recovered")
    
    def get_connection_qot_status(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """
        Get QoT status for a connection.
        
        Args:
            connection_id: Connection identifier
            
        Returns:
            QoT status dictionary or None if not found
        """
        with self._lock:
            if connection_id not in self.connection_states:
                return None
            
            state = self.connection_states[connection_id]
            recent_samples = state.get_recent_samples(5)
            
            return {
                'connection_id': connection_id,
                'degradation_level': state.degradation_level.value,
                'reconfig_count': state.reconfig_count,
                'is_in_cooldown': state.is_in_cooldown,
                'last_degradation_time': state.last_degradation_time.isoformat() if state.last_degradation_time else None,
                'last_reconfig_time': state.last_reconfig_time.isoformat() if state.last_reconfig_time else None,
                'cooldown_until': state.cooldown_until.isoformat() if state.cooldown_until else None,
                'recent_metrics': [
                    {
                        'timestamp': s.timestamp.isoformat(),
                        'osnr': s.osnr,
                        'pre_fec_ber': s.pre_fec_ber,
                        'tx_power': s.tx_power,
                        'rx_power': s.rx_power
                    }
                    for s in recent_samples
                ]
            }
    
    def get_all_qot_status(self) -> Dict[str, Any]:
        """
        Get QoT status for all connections.
        
        Returns:
            Dictionary with overall QoT status
        """
        with self._lock:
            status = {}
            degraded_count = 0
            critical_count = 0
            
            for conn_id, state in self.connection_states.items():
                status[conn_id] = {
                    'degradation_level': state.degradation_level.value,
                    'reconfig_count': state.reconfig_count,
                    'sample_count': len(state.samples)
                }
                
                if state.degradation_level == DegradationLevel.DEGRADED:
                    degraded_count += 1
                elif state.degradation_level == DegradationLevel.CRITICAL:
                    critical_count += 1
            
            return {
                'total_monitored': len(self.connection_states),
                'degraded_connections': degraded_count,
                'critical_connections': critical_count,
                'connections': status,
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def health_check(self) -> Dict[str, Any]:
        """Check QoT monitor health."""
        return {
            'status': 'healthy',
            'monitored_connections': len(self.connection_states),
            'monitoring_thread_alive': self._monitoring_thread.is_alive(),
            'timestamp': datetime.utcnow().isoformat()
        }
