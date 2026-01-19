"""
Telemetry Manager for SONiC Agent
Handles periodic telemetry collection and session management.
"""

import time
import threading
import logging
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict


class TelemetryState(str, Enum):
    """Telemetry session state."""
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class TelemetrySession:
    """Telemetry session data."""
    session_id: str
    connection_id: str
    interfaces: List[str]
    state: TelemetryState = TelemetryState.ACTIVE
    created_at: float = field(default_factory=time.time)
    last_collection: float = 0.0
    collection_count: int = 0
    error_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def age(self) -> float:
        """Get session age in seconds."""
        return time.time() - self.created_at
    
    @property
    def since_last_collection(self) -> float:
        """Get time since last collection."""
        if self.last_collection == 0:
            return float('inf')
        return time.time() - self.last_collection
    
    def update_collection(self):
        """Update collection timestamp and count."""
        self.last_collection = time.time()
        self.collection_count += 1


class TelemetryManager:
    """Manages telemetry collection for multiple sessions."""
    
    def __init__(
        self,
        cmis_driver,
        kafka_manager,
        interval_sec: float = 3.0,
        max_sessions: int = 50,
        batch_size: int = 100
    ):
        """Initialize telemetry manager."""
        self.cmis_driver = cmis_driver
        self.kafka_manager = kafka_manager
        self.interval_sec = interval_sec
        self.max_sessions = max_sessions
        self.batch_size = batch_size
        
        self.logger = logging.getLogger("telemetry-manager")
        
        # Session management
        self.sessions: Dict[str, TelemetrySession] = {}
        self.session_lock = threading.RLock()
        
        # Thread control
        self.collection_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.running = False
        
        # Statistics
        self.total_collections = 0
        self.failed_collections = 0
        self.messages_sent = 0
        self.start_time = time.time()
        
        # Buffer for batch sending
        self.telemetry_buffer: List[Dict[str, Any]] = []
        self.buffer_lock = threading.RLock()
        
        self.logger.info(
            f"Telemetry manager initialized: "
            f"interval={interval_sec}s, max_sessions={max_sessions}, "
            f"batch_size={batch_size}"
        )
    
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
        
        # Flush buffer
        self._flush_buffer()
        
        # Wait for thread
        if self.collection_thread and self.collection_thread.is_alive():
            self.collection_thread.join(timeout=5)
        
        # Clear all sessions
        with self.session_lock:
            self.sessions.clear()
        
        self.logger.info("Telemetry manager stopped")
    
    def _collection_loop(self):
        """Main telemetry collection loop."""
        self.logger.info("Telemetry collection loop started")
        
        last_buffer_flush = time.time()
        buffer_flush_interval = 1.0  # Flush buffer every second
        
        while not self.stop_event.is_set():
            try:
                loop_start = time.time()
                
                # Collect telemetry for all active sessions
                self._collect_all_sessions()
                
                # Flush buffer periodically
                current_time = time.time()
                if current_time - last_buffer_flush >= buffer_flush_interval:
                    self._flush_buffer()
                    last_buffer_flush = current_time
                
                # Calculate sleep time
                loop_duration = time.time() - loop_start
                sleep_time = max(0.1, self.interval_sec - loop_duration)
                
                # Sleep, but check stop event frequently
                for _ in range(int(sleep_time * 10)):
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)
                
            except Exception as e:
                self.logger.error(f"Error in telemetry collection loop: {e}")
                time.sleep(1)
        
        # Final buffer flush
        self._flush_buffer()
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
                session.error_count += 1
                
                # Disable session after too many errors
                if session.error_count > 10:
                    session.state = TelemetryState.ERROR
                    self.logger.warning(
                        f"Session {session.session_id} disabled due to errors"
                    )
    
    def _collect_session(self, session: TelemetrySession):
        """Collect telemetry for a single session."""
        telemetry_batch = []
        
        for interface in session.interfaces:
            try:
                # Read telemetry from hardware
                readings = self.cmis_driver.read_telemetry(interface)
                
                # Create telemetry message
                telemetry_msg = {
                    "timestamp": time.time(),
                    "session_id": session.session_id,
                    "connection_id": session.connection_id,
                    "interface": interface,
                    "readings": {
                        "tx_power_dbm": readings.tx_power_dbm,
                        "rx_power_dbm": readings.rx_power_dbm,
                        "osnr_db": readings.osnr_db,
                        "pre_fec_ber": readings.pre_fec_ber,
                        "temperature_c": readings.temperature_c,
                        "module_state": readings.module_state,
                        "is_healthy": readings.is_healthy,
                    },
                    "metadata": session.metadata
                }
                
                telemetry_batch.append(telemetry_msg)
                
                # Update session
                session.update_collection()
                self.total_collections += 1
                
            except Exception as e:
                self.failed_collections += 1
                self.logger.error(
                    f"Failed to collect telemetry for {interface} in "
                    f"session {session.session_id}: {e}"
                )
        
        # Add to buffer
        if telemetry_batch:
            with self.buffer_lock:
                self.telemetry_buffer.extend(telemetry_batch)
                
                # Flush if buffer is full
                if len(self.telemetry_buffer) >= self.batch_size:
                    self._flush_buffer()
    
    def _flush_buffer(self):
        """Flush telemetry buffer to Kafka."""
        with self.buffer_lock:
            if not self.telemetry_buffer:
                return
            
            # Prepare batch messages
            messages = []
            for telemetry in self.telemetry_buffer:
                message = {
                    "topic": "monitoring_vOp2",  # Should be configurable
                    "key": telemetry["session_id"],
                    "value": {
                        "type": "telemetry",
                        "timestamp": telemetry["timestamp"],
                        "agent_id": "sonic-agent",  # Should be configurable
                        "data": telemetry
                    }
                }
                messages.append(message)
            
            # Send batch
            try:
                results = self.kafka_manager.send_batch(messages)
                successful = sum(1 for r in results if r)
                
                self.messages_sent += successful
                self.logger.debug(
                    f"Flushed {len(messages)} telemetry messages "
                    f"({successful} successful)"
                )
                
                # Clear buffer
                self.telemetry_buffer.clear()
                
            except Exception as e:
                self.logger.error(f"Failed to flush telemetry buffer: {e}")
                
                # Keep messages in buffer for retry
                # Limit buffer size to prevent memory issues
                if len(self.telemetry_buffer) > self.batch_size * 10:
                    self.logger.warning(
                        f"Telemetry buffer overflow, discarding old messages"
                    )
                    self.telemetry_buffer = self.telemetry_buffer[-self.batch_size:]
    
    def start_session(
        self,
        connection_id: str,
        interfaces: List[str],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Start a new telemetry session."""
        # Generate session ID
        session_id = f"session-{connection_id}-{int(time.time())}"
        
        # Check session limit
        with self.session_lock:
            if len(self.sessions) >= self.max_sessions:
                self.logger.error(f"Cannot create session: maximum sessions ({self.max_sessions}) reached")
                return None
            
            # Create session
            session = TelemetrySession(
                session_id=session_id,
                connection_id=connection_id,
                interfaces=interfaces,
                metadata=metadata or {},
                state=TelemetryState.ACTIVE
            )
            
            self.sessions[session_id] = session
        
        self.logger.info(
            f"Started telemetry session {session_id} for "
            f"connection {connection_id} with {len(interfaces)} interfaces"
        )
        
        return session_id
    
    def stop_session(self, session_id: str) -> bool:
        """Stop a telemetry session."""
        with self.session_lock:
            if session_id not in self.sessions:
                self.logger.warning(f"Session {session_id} not found")
                return False
            
            session = self.sessions[session_id]
            session.state = TelemetryState.STOPPED
            
            # Don't delete immediately, keep for statistics
        
        self.logger.info(f"Stopped telemetry session {session_id}")
        return True
    
    def pause_session(self, session_id: str) -> bool:
        """Pause a telemetry session."""
        with self.session_lock:
            if session_id not in self.sessions:
                self.logger.warning(f"Session {session_id} not found")
                return False
            
            session = self.sessions[session_id]
            if session.state != TelemetryState.ACTIVE:
                self.logger.warning(f"Session {session_id} is not active")
                return False
            
            session.state = TelemetryState.PAUSED
        
        self.logger.info(f"Paused telemetry session {session_id}")
        return True
    
    def resume_session(self, session_id: str) -> bool:
        """Resume a paused telemetry session."""
        with self.session_lock:
            if session_id not in self.sessions:
                self.logger.warning(f"Session {session_id} not found")
                return False
            
            session = self.sessions[session_id]
            if session.state != TelemetryState.PAUSED:
                self.logger.warning(f"Session {session_id} is not paused")
                return False
            
            session.state = TelemetryState.ACTIVE
        
        self.logger.info(f"Resumed telemetry session {session_id}")
        return True
    
    def update_session_interfaces(
        self,
        session_id: str,
        interfaces: List[str]
    ) -> bool:
        """Update interfaces for a session."""
        with self.session_lock:
            if session_id not in self.sessions:
                self.logger.warning(f"Session {session_id} not found")
                return False
            
            session = self.sessions[session_id]
            session.interfaces = interfaces
        
        self.logger.info(
            f"Updated session {session_id} with {len(interfaces)} interfaces"
        )
        return True
    
    def get_session(self, session_id: str) -> Optional[TelemetrySession]:
        """Get a session by ID."""
        with self.session_lock:
            return self.sessions.get(session_id)
    
    def get_active_sessions(self) -> List[TelemetrySession]:
        """Get all active sessions."""
        with self.session_lock:
            return [
                session for session in self.sessions.values()
                if session.state == TelemetryState.ACTIVE
            ]
    
    def cleanup_stale_sessions(self, max_age_sec: float = 3600):
        """Clean up stale sessions."""
        with self.session_lock:
            current_time = time.time()
            stale_sessions = []
            
            for session_id, session in self.sessions.items():
                # Remove stopped or error sessions older than max_age_sec
                if session.state in [TelemetryState.STOPPED, TelemetryState.ERROR]:
                    if session.age > max_age_sec:
                        stale_sessions.append(session_id)
                
                # Remove active sessions with no collection for too long
                elif session.state == TelemetryState.ACTIVE:
                    if session.since_last_collection > max_age_sec * 2:
                        session.state = TelemetryState.ERROR
                        self.logger.warning(
                            f"Session {session_id} marked as error due to inactivity"
                        )
            
            # Remove stale sessions
            for session_id in stale_sessions:
                del self.sessions[session_id]
            
            if stale_sessions:
                self.logger.info(
                    f"Cleaned up {len(stale_sessions)} stale sessions"
                )
    
    def is_healthy(self) -> bool:
        """Check if telemetry manager is healthy."""
        try:
            # Check if we're running
            if not self.running:
                return False
            
            # Check if thread is alive
            if self.collection_thread and not self.collection_thread.is_alive():
                return False
            
            # Check error rate
            if self.total_collections > 0:
                error_rate = self.failed_collections / self.total_collections
                if error_rate > 0.5:  # More than 50% errors
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get telemetry manager statistics."""
        with self.session_lock:
            session_stats = {
                "total_sessions": len(self.sessions),
                "active_sessions": len(self.get_active_sessions()),
                "paused_sessions": len([
                    s for s in self.sessions.values()
                    if s.state == TelemetryState.PAUSED
                ]),
                "stopped_sessions": len([
                    s for s in self.sessions.values()
                    if s.state == TelemetryState.STOPPED
                ]),
                "error_sessions": len([
                    s for s in self.sessions.values()
                    if s.state == TelemetryState.ERROR
                ]),
            }
        
        with self.buffer_lock:
            buffer_stats = {
                "buffer_size": len(self.telemetry_buffer),
                "batch_size": self.batch_size,
            }
        
        return {
            "running": self.running,
            "uptime": time.time() - self.start_time,
            "total_collections": self.total_collections,
            "failed_collections": self.failed_collections,
            "messages_sent": self.messages_sent,
            "collection_interval": self.interval_sec,
            "sessions": session_stats,
            "buffer": buffer_stats,
            "healthy": self.is_healthy(),
        }
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        self.stop()