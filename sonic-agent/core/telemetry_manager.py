"""
Telemetry Manager for SONiC Agent
Handles periodic telemetry collection as per OFC paper (3s interval)
"""

import time
import threading
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
from collections import deque


class TelemetryState(str, Enum):
    """Telemetry session state."""
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class TelemetrySession:
    """Telemetry session for a connection."""
    session_id: str
    connection_id: str
    interface: str
    state: TelemetryState = TelemetryState.ACTIVE
    created_at: float = time.time()
    last_collection: float = 0
    collection_count: int = 0


class TelemetryManager:
    """Manages telemetry collection for connections."""
    
    def __init__(self, cmis_driver, kafka_manager, interval_sec: float = 3.0):
        """Initialize telemetry manager."""
        self.cmis_driver = cmis_driver
        self.kafka_manager = kafka_manager
        self.interval_sec = interval_sec
        
        self.logger = logging.getLogger("telemetry-manager")
        
        # Session management
        self.sessions: Dict[str, TelemetrySession] = {}
        self.session_lock = threading.RLock()
        
        # QoT monitoring
        self.qot_samples: Dict[str, deque] = {}  # connection_id -> deque of samples
        self.qot_lock = threading.RLock()
        
        # Thread control
        self.collection_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.running = False
        
        # Statistics
        self.total_collections = 0
        self.failed_collections = 0
        
        self.logger.info(f"Telemetry manager initialized with {interval_sec}s interval")
    
    def start(self):
        """Start telemetry collection thread."""
        if self.running:
            self.logger.warning("Telemetry manager already running")
            return
        
        self.running = True
        self.stop_event.clear()
        
        self.collection_thread = threading.Thread(
            target=self._collection_loop,
            name="telemetry-collection",
            daemon=True
        )
        self.collection_thread.start()
        
        self.logger.info("Telemetry manager started")
    
    def stop(self):
        """Stop telemetry collection."""
        if not self.running:
            return
        
        self.logger.info("Stopping telemetry manager...")
        self.running = False
        self.stop_event.set()
        
        if self.collection_thread and self.collection_thread.is_alive():
            self.collection_thread.join(timeout=5)
        
        # Clear all sessions
        with self.session_lock:
            self.sessions.clear()
        
        self.logger.info("Telemetry manager stopped")
    
    def _collection_loop(self):
        """Main telemetry collection loop (3s interval as per paper)."""
        self.logger.info(f"Telemetry collection loop started ({self.interval_sec}s interval)")
        
        while not self.stop_event.is_set():
            try:
                loop_start = time.time()
                
                # Collect telemetry for all active sessions
                self._collect_all_sessions()
                
                # QoT monitoring
                if self.sessions:
                    self._monitor_qot()
                
                # Calculate sleep time to maintain 3s interval
                loop_duration = time.time() - loop_start
                sleep_time = max(0.1, self.interval_sec - loop_duration)
                
                # Sleep, checking stop event frequently
                for _ in range(int(sleep_time * 10)):
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)
                
            except Exception as e:
                self.logger.error(f"Error in telemetry loop: {e}")
                time.sleep(1)
        
        self.logger.info("Telemetry collection loop stopped")
    
    def _collect_all_sessions(self):
        """Collect telemetry for all active sessions."""
        with self.session_lock:
            # Get active sessions
            active_sessions = [
                session for session in self.sessions.values()
                if session.state == TelemetryState.ACTIVE
            ]
        
        if not active_sessions:
            return
        
        # Collect for each session
        for session in active_sessions:
            try:
                self._collect_session(session)
            except Exception as e:
                self.logger.error(f"Failed to collect session {session.session_id}: {e}")
                self.failed_collections += 1
    
    def _collect_session(self, session: TelemetrySession):
        """Collect telemetry for a single session."""
        try:
            # Read telemetry from hardware
            readings = self.cmis_driver.read_telemetry(session.interface)
            
            # Prepare telemetry message (Fig. 2c format)
            telemetry_msg = {
                "type": "telemetrySample",
                "connection_id": session.connection_id,
                "agent_id": "pop1-router1",  # Should be from settings
                "interface": session.interface,
                "timestamp": time.time(),
                "fields": {
                    "rx_power": readings.rx_power_dbm,
                    "osnr": readings.osnr_db,
                    "ber": readings.pre_fec_ber,
                    "tx_power": readings.tx_power_dbm,
                    "temperature": readings.temperature_c,
                    "module_state": readings.module_state
                }
            }
            
            # Send to monitoring topic
            self.kafka_manager.send_monitoring_message(telemetry_msg)
            
            # Update session stats
            session.last_collection = time.time()
            session.collection_count += 1
            self.total_collections += 1
            
            # Store sample for QoT monitoring
            self._store_qot_sample(session.connection_id, telemetry_msg["fields"])
            
            self.logger.debug(f"Telemetry collected for {session.connection_id} on {session.interface}")
            
        except Exception as e:
            self.logger.error(f"Failed to collect telemetry for session {session.session_id}: {e}")
            self.failed_collections += 1
    
    def _store_qot_sample(self, connection_id: str, fields: Dict[str, Any]):
        """Store telemetry sample for QoT monitoring."""
        with self.qot_lock:
            if connection_id not in self.qot_samples:
                self.qot_samples[connection_id] = deque(maxlen=10)  # Keep last 10 samples
            
            self.qot_samples[connection_id].append({
                "timestamp": time.time(),
                "fields": fields
            })
    
    def _monitor_qot(self):
        """Monitor QoT and trigger reconfiguration if needed."""
        with self.qot_lock:
            for connection_id, samples in self.qot_samples.items():
                if len(samples) < 3:  # Need at least 3 samples (paper spec)
                    continue
                
                # Check for degradation (simple threshold-based as per paper)
                recent_samples = list(samples)[-3:]  # Last 3 samples
                
                # Check OSNR
                osnr_values = [s["fields"].get("osnr") for s in recent_samples if s["fields"].get("osnr")]
                if osnr_values and len(osnr_values) >= 3:
                    avg_osnr = sum(osnr_values) / len(osnr_values)
                    
                    # If OSNR below threshold for 3 consecutive samples
                    if avg_osnr < 18.0:  # Threshold from paper
                        self.logger.warning(f"QoT degradation detected for {connection_id}: OSNR={avg_osnr:.1f}dB")
                        
                        # Find session for this connection
                        session = self._get_session_by_connection(connection_id)
                        if session:
                            # Adjust TX power (simple +0.5 dB increase)
                            current_power = recent_samples[-1]["fields"].get("tx_power")
                            if current_power:
                                new_power = min(current_power + 0.5, -8.0)  # Max -8 dBm
                                self.logger.info(f"Adjusting TX power for {connection_id}: {current_power} -> {new_power}dBm")
                                
                                # This would call cmis_driver.adjust_tx_power()
                                # For now, just log
                                
                                # Send QoT event
                                qot_event = {
                                    "type": "qotEvent",
                                    "connection_id": connection_id,
                                    "agent_id": "pop1-router1",
                                    "event": "tx_power_adjusted",
                                    "details": {
                                        "old_power": current_power,
                                        "new_power": new_power,
                                        "reason": f"OSNR degraded to {avg_osnr:.1f} dB",
                                        "samples": 3
                                    },
                                    "timestamp": time.time()
                                }
                                self.kafka_manager.send_monitoring_message(qot_event)
    
    def _get_session_by_connection(self, connection_id: str) -> Optional[TelemetrySession]:
        """Get session by connection ID."""
        with self.session_lock:
            for session in self.sessions.values():
                if session.connection_id == connection_id:
                    return session
        return None
    
    def start_session(self, connection_id: str, interface: str) -> str:
        """Start a new telemetry session."""
        session_id = f"session-{connection_id}-{interface}-{int(time.time())}"
        
        with self.session_lock:
            session = TelemetrySession(
                session_id=session_id,
                connection_id=connection_id,
                interface=interface
            )
            self.sessions[session_id] = session
        
        self.logger.info(f"Started telemetry session {session_id} for {connection_id} on {interface}")
        return session_id
    
    def stop_session(self, session_id: str) -> bool:
        """Stop a telemetry session."""
        with self.session_lock:
            if session_id not in self.sessions:
                return False
            
            session = self.sessions[session_id]
            session.state = TelemetryState.STOPPED
            
            # Clean up QoT samples
            with self.qot_lock:
                if session.connection_id in self.qot_samples:
                    del self.qot_samples[session.connection_id]
        
        self.logger.info(f"Stopped telemetry session {session_id}")
        return True
    
    def pause_session(self, session_id: str) -> bool:
        """Pause a telemetry session."""
        with self.session_lock:
            if session_id not in self.sessions:
                return False
            
            session = self.sessions[session_id]
            if session.state != TelemetryState.ACTIVE:
                return False
            
            session.state = TelemetryState.PAUSED
        
        self.logger.info(f"Paused telemetry session {session_id}")
        return True
    
    def resume_session(self, session_id: str) -> bool:
        """Resume a paused telemetry session."""
        with self.session_lock:
            if session_id not in self.sessions:
                return False
            
            session = self.sessions[session_id]
            if session.state != TelemetryState.PAUSED:
                return False
            
            session.state = TelemetryState.ACTIVE
        
        self.logger.info(f"Resumed telemetry session {session_id}")
        return True
    
    def get_active_sessions(self) -> List[TelemetrySession]:
        """Get all active sessions."""
        with self.session_lock:
            return [s for s in self.sessions.values() if s.state == TelemetryState.ACTIVE]
    
    def is_healthy(self) -> bool:
        """Check if telemetry manager is healthy."""
        if not self.running:
            return False
        
        if self.collection_thread and not self.collection_thread.is_alive():
            return False
        
        # Check error rate
        if self.total_collections > 0:
            error_rate = self.failed_collections / self.total_collections
            if error_rate > 0.5:  # More than 50% errors
                return False
        
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """Get telemetry manager statistics."""
        with self.session_lock:
            session_stats = {
                "total_sessions": len(self.sessions),
                "active_sessions": len(self.get_active_sessions()),
            }
        
        return {
            "running": self.running,
            "interval_sec": self.interval_sec,
            "total_collections": self.total_collections,
            "failed_collections": self.failed_collections,
            "sessions": session_stats,
            "healthy": self.is_healthy(),
        }
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        self.stop()
