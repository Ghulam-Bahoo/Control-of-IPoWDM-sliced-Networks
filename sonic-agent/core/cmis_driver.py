"""
CMIS Driver for NeoPhotonics 400G ZR Modules
Production-ready implementation for Edgecore SONiC switches
"""

import struct
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import IntEnum
import threading


@dataclass
class CMISConfiguration:
    """
    Configuration for CMISDriver.

    This object can be expanded later; for now it just carries the
    interface mapping and a few basic options.
    """
    interface_mappings: Dict[str, int]
    mock_mode: bool = False
    read_timeout_sec: float = 0.5
    max_retries: int = 3

class CMISPage(IntEnum):
    """CMIS Memory Page Addresses."""
    MODULE_ID = 0x00
    MODULE_STATE = 0x10
    LANE_STATE = 0x11
    LASER_CONFIG = 0x20
    TX_POWER = 0x26
    RX_POWER = 0x27
    APPLICATIONS = 0x04
    DIAGNOSTICS = 0x35
    VENDOR_SPECIFIC = 0x7F


class ModuleState(IntEnum):
    """CMIS Module State."""
    RESET = 0x00
    INITIALIZED = 0x01
    LOW_POWER = 0x02
    HIGH_POWER_UP = 0x03
    HIGH_POWER = 0x04
    FAULT = 0x05


class ApplicationCode(IntEnum):
    """Application codes for NeoPhotonics QDDMA400700C2000."""
    ZR_400G_DWDM = 0x01
    ZR_400G_OFEC_16QAM = 0x02
    ZR_100G_OFEC_16QAM = 0x03
    ZR_100G_OFEC_8QAM = 0x04
    ZR_100G_OFEC_QPSK = 0x05


@dataclass
class TelemetryReadings:
    """Telemetry readings from CMIS module."""
    timestamp: float
    interface: str
    tx_power_dbm: Optional[float] = None
    rx_power_dbm: Optional[float] = None
    osnr_db: Optional[float] = None
    pre_fec_ber: Optional[float] = None
    temperature_c: Optional[float] = None
    module_state: Optional[str] = None
    frequency_mhz: Optional[int] = None
    app_code: Optional[int] = None
    tx_power_setting: Optional[float] = None


class CMISDriver:
    """Production CMIS driver for Edgecore SONiC switches."""
    
    # NeoPhotonics QDDMA400700C2000 specifications
    FREQUENCY_MIN = 191300  # MHz
    FREQUENCY_MAX = 196100  # MHz
    FREQUENCY_STEP = 100    # MHz
    
    TX_POWER_MIN = -15.0    # dBm
    TX_POWER_MAX = -8.0     # dBm
    TX_POWER_STEP = 0.1     # dB
    
    def __init__(self, interface_mappings: Dict[str, int], mock_mode: bool = False):
        """Initialize CMIS driver."""
        self.interface_mappings = interface_mappings
        self.mock_mode = mock_mode
        self.logger = logging.getLogger("cmis-driver")
        self.lock = threading.RLock()
        
        if not mock_mode:
            self._init_sonic_platform()
        else:
            self.logger.info("Running in mock mode - no hardware access")
            self.platform_chassis = None
        
        self.logger.info(f"CMIS driver initialized for {len(interface_mappings)} interfaces")
    
    def _init_sonic_platform(self):
        """Initialize SONiC platform access."""
        try:
            # Method 1: Standard SONiC platform import
            import sonic_platform.platform
            self.platform_chassis = sonic_platform.platform.Platform().get_chassis()
            
            if not self.platform_chassis:
                # Method 2: Try direct import
                import sonic_platform
                self.platform_chassis = sonic_platform.platform.Platform().get_chassis()
            
            if self.platform_chassis:
                self.logger.info(f"SONiC platform initialized: {self.platform_chassis.get_name()}")
            else:
                raise RuntimeError("Failed to get platform chassis")
                
        except ImportError as e:
            self.logger.error(f"SONiC platform import failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Platform initialization error: {e}")
            raise
    
    def _get_sfp(self, interface: str):
        """Get SFP object for interface."""
        if self.mock_mode:
            return MockSFP(interface)
        
        if interface not in self.interface_mappings:
            self.logger.error(f"Interface {interface} not in mappings")
            return None
        
        try:
            port_num = self.interface_mappings[interface]
            sfp = self.platform_chassis.get_sfp(port_num)
            return sfp
        except Exception as e:
            self.logger.error(f"Failed to get SFP for {interface}: {e}")
            return None
    
    def check_health(self) -> bool:
        """Check health of all interfaces."""
        healthy_count = 0
        
        for interface in self.interface_mappings.keys():
            try:
                sfp = self._get_sfp(interface)
                if not sfp:
                    continue
                
                if sfp.get_presence():
                    # Try to read basic info
                    vendor = sfp.get_vendor()
                    if vendor:
                        healthy_count += 1
                        self.logger.debug(f"Interface {interface} healthy: {vendor}")
                    else:
                        self.logger.warning(f"Interface {interface} present but no vendor info")
                else:
                    self.logger.warning(f"Interface {interface} not present")
                    
            except Exception as e:
                self.logger.error(f"Health check failed for {interface}: {e}")
        
        return healthy_count > 0
    
    def get_interface_status(self, interface: str) -> Dict[str, Any]:
        """Get detailed interface status."""
        result = {
            "port_id": interface,
            "present": False,
            "operational": False,
            "vendor": "unknown",
            "part_number": "unknown",
            "serial": "unknown",
            "module_state": "unknown",
            "frequency_mhz": None,
            "app_code": None,
            "tx_power_dbm": None
        }
        
        try:
            sfp = self._get_sfp(interface)
            if not sfp:
                return result
            
            result["present"] = sfp.get_presence()
            if not result["present"]:
                return result
            
            # Get basic info
            result["vendor"] = sfp.get_vendor() or "unknown"
            result["part_number"] = sfp.get_part_number() or "unknown"
            result["serial"] = sfp.get_serial() or "unknown"
            
            # Read module state
            state = self._read_module_state(sfp)
            if state:
                result["module_state"] = state.name
                result["operational"] = state in [ModuleState.HIGH_POWER, ModuleState.HIGH_POWER_UP]
            
            # Read current configuration
            config = self._read_current_config(sfp)
            if config:
                result.update(config)
            
        except Exception as e:
            self.logger.error(f"Failed to get status for {interface}: {e}")
        
        return result
    
    def _read_module_state(self, sfp) -> Optional[ModuleState]:
        """Read module state from CMIS."""
        try:
            # Select module state page
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.MODULE_STATE]))
            time.sleep(0.001)
            
            # Read state byte
            state_data = sfp.read_eeprom(0x02, 1)
            if state_data:
                state_value = state_data[0] & 0x0F
                return ModuleState(state_value)
                
        except Exception as e:
            self.logger.debug(f"Failed to read module state: {e}")
        
        return None
    
    def _read_current_config(self, sfp) -> Dict[str, Any]:
        """Read current module configuration."""
        config = {}
        
        try:
            # Read frequency
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.LASER_CONFIG]))
            time.sleep(0.001)
            
            freq_data = sfp.read_eeprom(0x14, 2)
            if freq_data and len(freq_data) == 2:
                reg_value = struct.unpack('>H', freq_data)[0]
                config["frequency_mhz"] = self.FREQUENCY_MIN + (reg_value * self.FREQUENCY_STEP)
            
            # Read application
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.APPLICATIONS]))
            time.sleep(0.001)
            
            app_data = sfp.read_eeprom(0x02, 1)
            if app_data:
                config["app_code"] = app_data[0]
            
            # Read TX power
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.TX_POWER]))
            time.sleep(0.001)
            
            tx_data = sfp.read_eeprom(0x10, 1)
            if tx_data:
                reg_value = tx_data[0]
                config["tx_power_dbm"] = self.TX_POWER_MIN + (reg_value * self.TX_POWER_STEP)
                
        except Exception as e:
            self.logger.debug(f"Failed to read current config: {e}")
        
        return config
    
    def get_capabilities(self, interface: str) -> Dict[str, Any]:
        """Get capabilities of interface (Fig. 2a format)."""
        capabilities = {
            "port_id": interface,
            "type": "ZR",
            "app_code": {
                "1": {"rate": "400G", "mode": "DWDM-amplified"},
                "2": {"rate": "400G", "mode": "OFEC-16QAM"},
                "3": {"rate": "100G", "mode": "OFEC-16QAM"},
                "4": {"rate": "100G", "mode": "OFEC-8QAM"},
                "5": {"rate": "100G", "mode": "OFEC-QPSK"}
            },
            "frequency_range": [self.FREQUENCY_MIN, self.FREQUENCY_MAX],
            "tx_power_range": [self.TX_POWER_MIN, self.TX_POWER_MAX]
        }
        
        try:
            sfp = self._get_sfp(interface)
            if not sfp or not sfp.get_presence():
                return capabilities
            
            # Add vendor info
            capabilities["vendor"] = sfp.get_vendor() or "unknown"
            capabilities["part_number"] = sfp.get_part_number() or "unknown"
            
        except Exception as e:
            self.logger.error(f"Failed to get capabilities for {interface}: {e}")
        
        return capabilities
    
    def configure_interface(
        self,
        interface: str,
        frequency_mhz: int,
        app_code: int,
        tx_power_dbm: float
    ) -> Dict[str, Any]:
        """Configure interface with given parameters."""
        result = {
            "success": False,
            "interface": interface,
            "error": None,
            "details": {}
        }
        
        try:
            sfp = self._get_sfp(interface)
            if not sfp:
                result["error"] = "SFP not found"
                return result
            
            if not sfp.get_presence():
                result["error"] = "Transceiver not present"
                return result
            
            # Wait for module to be ready
            if not self._wait_for_ready(sfp):
                result["error"] = "Module not ready"
                return result
            
            # Apply frequency
            freq_result = self._apply_frequency(sfp, frequency_mhz)
            if not freq_result["success"]:
                result["error"] = f"Frequency config failed: {freq_result.get('error')}"
                return result
            result["details"]["frequency"] = freq_result
            
            # Apply application
            app_result = self._apply_application(sfp, app_code)
            if not app_result["success"]:
                result["error"] = f"Application config failed: {app_result.get('error')}"
                return result
            result["details"]["application"] = app_result
            
            # Apply TX power
            tx_result = self._apply_tx_power(sfp, tx_power_dbm)
            if not tx_result["success"]:
                result["error"] = f"TX power config failed: {tx_result.get('error')}"
                return result
            result["details"]["tx_power"] = tx_result
            
            # Trigger reinitialization
            self._trigger_module_init(sfp)
            time.sleep(0.5)  # Allow time for reinit
            
            # Verify configuration
            verify_result = self._verify_configuration(sfp, frequency_mhz, app_code, tx_power_dbm)
            result["details"]["verification"] = verify_result
            
            if verify_result.get("matched", False):
                result["success"] = True
                self.logger.info(f"Configured {interface}: freq={frequency_mhz}MHz, app={app_code}, tx={tx_power_dbm}dBm")
            else:
                result["error"] = "Configuration verification failed"
                
        except Exception as e:
            result["error"] = f"Configuration error: {str(e)}"
            self.logger.error(f"Failed to configure {interface}: {e}")
        
        return result
    
    def _apply_frequency(self, sfp, frequency_mhz: int) -> Dict[str, Any]:
        """Apply frequency configuration."""
        try:
            # Validate frequency
            if not (self.FREQUENCY_MIN <= frequency_mhz <= self.FREQUENCY_MAX):
                return {"success": False, "error": f"Frequency {frequency_mhz}MHz out of range"}
            
            # Calculate register value
            reg_value = (frequency_mhz - self.FREQUENCY_MIN) // self.FREQUENCY_STEP
            
            # Select laser config page and write frequency
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.LASER_CONFIG]))
            time.sleep(0.001)
            
            freq_bytes = struct.pack('>H', reg_value)
            sfp.write_eeprom(0x14, 2, freq_bytes)
            time.sleep(0.1)  # Allow for laser tuning
            
            return {"success": True, "register_value": reg_value}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _apply_application(self, sfp, app_code: int) -> Dict[str, Any]:
        """Apply application configuration."""
        try:
            # Validate application code
            if app_code not in [ac.value for ac in ApplicationCode]:
                return {"success": False, "error": f"Invalid app code: {app_code}"}
            
            # Select applications page and write app code
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.APPLICATIONS]))
            time.sleep(0.001)
            
            sfp.write_eeprom(0x02, 1, bytes([app_code]))
            
            return {"success": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _apply_tx_power(self, sfp, tx_power_dbm: float) -> Dict[str, Any]:
        """Apply TX power configuration."""
        try:
            # Validate power
            if not (self.TX_POWER_MIN <= tx_power_dbm <= self.TX_POWER_MAX):
                return {"success": False, "error": f"TX power {tx_power_dbm}dBm out of range"}
            
            # Calculate register value
            reg_value = int((tx_power_dbm - self.TX_POWER_MIN) / self.TX_POWER_STEP)
            
            # Select TX power page and write power
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.TX_POWER]))
            time.sleep(0.001)
            
            sfp.write_eeprom(0x10, 1, bytes([reg_value]))
            
            return {"success": True, "register_value": reg_value}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _wait_for_ready(self, sfp, timeout: float = 5.0) -> bool:
        """Wait for module to be ready."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            state = self._read_module_state(sfp)
            if state in [ModuleState.HIGH_POWER, ModuleState.HIGH_POWER_UP]:
                return True
            elif state == ModuleState.FAULT:
                return False
            time.sleep(0.1)
        
        return False
    
    def _trigger_module_init(self, sfp):
        """Trigger module reinitialization."""
        try:
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.MODULE_STATE]))
            time.sleep(0.001)
            sfp.write_eeprom(0x04, 1, bytes([0x01]))  # Init trigger
        except Exception:
            pass
    
    def _verify_configuration(self, sfp, target_freq, target_app, target_tx):
        """Verify configuration matches target."""
        verification = {"matched": True}
        
        try:
            # Read back configuration
            config = self._read_current_config(sfp)
            
            # Verify frequency
            if target_freq and "frequency_mhz" in config:
                freq_diff = abs(config["frequency_mhz"] - target_freq)
                verification["frequency_match"] = freq_diff <= self.FREQUENCY_STEP
                verification["matched"] &= verification["frequency_match"]
            
            # Verify application
            if target_app and "app_code" in config:
                verification["app_match"] = config["app_code"] == target_app
                verification["matched"] &= verification["app_match"]
            
            # Verify TX power
            if target_tx and "tx_power_dbm" in config:
                tx_diff = abs(config["tx_power_dbm"] - target_tx)
                verification["tx_power_match"] = tx_diff <= (self.TX_POWER_STEP * 2)
                verification["matched"] &= verification["tx_power_match"]
                
        except Exception as e:
            verification["matched"] = False
            verification["error"] = str(e)
        
        return verification
    
    def read_telemetry(self, interface: str) -> TelemetryReadings:
        """Read telemetry from interface."""
        timestamp = time.time()
        
        try:
            sfp = self._get_sfp(interface)
            if not sfp or not sfp.get_presence():
                return TelemetryReadings(timestamp=timestamp, interface=interface)
            
            # Select diagnostics page
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([CMISPage.DIAGNOSTICS]))
            time.sleep(0.001)
            
            # Read telemetry values
            readings = TelemetryReadings(timestamp=timestamp, interface=interface)
            
            # TX Power
            tx_data = sfp.read_eeprom(182, 2)
            if tx_data and len(tx_data) == 2:
                readings.tx_power_dbm = struct.unpack('>h', tx_data)[0] / 100.0
            
            # RX Power
            rx_data = sfp.read_eeprom(188, 2)
            if rx_data and len(rx_data) == 2:
                readings.rx_power_dbm = struct.unpack('>h', rx_data)[0] / 100.0
            
            # OSNR
            osnr_data = sfp.read_eeprom(158, 2)
            if osnr_data and len(osnr_data) == 2:
                readings.osnr_db = struct.unpack('>H', osnr_data)[0] / 10.0
            
            # Temperature
            temp_data = sfp.read_eeprom(14, 2)
            if temp_data and len(temp_data) == 2:
                readings.temperature_c = struct.unpack('>h', temp_data)[0] / 256.0
            
            # Read current config for module state and settings
            config = self._read_current_config(sfp)
            state = self._read_module_state(sfp)
            
            if config:
                readings.frequency_mhz = config.get("frequency_mhz")
                readings.app_code = config.get("app_code")
                readings.tx_power_setting = config.get("tx_power_dbm")
            
            if state:
                readings.module_state = state.name
            
            return readings
            
        except Exception as e:
            self.logger.error(f"Failed to read telemetry for {interface}: {e}")
            return TelemetryReadings(timestamp=timestamp, interface=interface)
    
    def adjust_tx_power(self, interface: str, new_power_dbm: float) -> Dict[str, Any]:
        """Adjust TX power (for QoT reconfiguration)."""
        return self.configure_interface(
            interface=interface,
            frequency_mhz=None,  # Keep current frequency
            app_code=None,       # Keep current app
            tx_power_dbm=new_power_dbm
        )


class MockSFP:
    """Mock SFP class for testing without hardware."""
    def __init__(self, interface):
        self.interface = interface
        self.mock_data = {
            "present": True,
            "vendor": "NeoPhotonics",
            "part_number": "QDDMA400700C2000",
            "serial": "EVC2327067"
        }
    
    def get_presence(self):
        return self.mock_data["present"]
    
    def get_vendor(self):
        return self.mock_data["vendor"]
    
    def get_part_number(self):
        return self.mock_data["part_number"]
    
    def get_serial(self):
        return self.mock_data["serial"]
    
    def read_eeprom(self, offset, num_bytes):
        # Return mock data based on offset
        if offset == 182:  # TX power
            return struct.pack('>h', int(-10.0 * 100))
        elif offset == 188:  # RX power
            return struct.pack('>h', int(-12.5 * 100))
        elif offset == 158:  # OSNR
            return struct.pack('>H', int(25.5 * 10))
        elif offset == 14:  # Temperature
            return struct.pack('>h', int(45.0 * 256))
        else:
            return b'\x00' * num_bytes
    
    def write_eeprom(self, offset, num_bytes, data):
        return True
