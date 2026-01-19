"""
CMIS Driver for NeoPhotonics 400G ZR Modules
"""

import struct
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import IntEnum, Enum
import threading

# SONiC Platform API
try:
    from sonic_platform.platform import Platform
    from sonic_platform.sfp import Sfp
except ImportError:
    # For testing without SONiC
    Platform = type('MockPlatform', (), {})
    Sfp = type('MockSfp', (), {})


class CMISPage(IntEnum):
    """CMIS Memory Page Addresses."""
    MODULE_ID = 0x00
    MODULE_STATE = 0x10
    LANE_STATE = 0x11
    LASER_CONFIG = 0x20
    TX_POWER = 0x26
    RX_POWER = 0x27
    APPLICATIONS = 0x04
    VENDOR_SPECIFIC = 0x7F
    DIAGNOSTICS = 0x35


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
    ZR_400G_DWDM = 0x01      # 400GAUI-8 C2M, 400ZR DWDM amplified
    ZR_400G_OFEC_16QAM = 0x02 # 400GAUI-8 C2M, ZR400-OFEC-16QAM
    ZR_100G_OFEC_16QAM = 0x03 # 100GAUI-2 C2M, ZR400-OFEC-16QAM
    ZR_100G_OFEC_8QAM = 0x04  # 100GAUI-2 C2M, ZR300-OFEC-8QAM
    ZR_100G_OFEC_QPSK = 0x05  # 100GAUI-2 C2M, ZR200-OFEC-QPSK


@dataclass
class CMISConfiguration:
    """CMIS module configuration."""
    frequency_mhz: int  # Center frequency in MHz
    app_code: ApplicationCode
    tx_power_dbm: float
    modulation: str
    baud_rate_gbd: float
    
    @classmethod
    def default(cls):
        """Get default configuration."""
        return cls(
            frequency_mhz=193100,
            app_code=ApplicationCode.ZR_400G_DWDM,
            tx_power_dbm=-10.0,
            modulation="16QAM",
            baud_rate_gbd=64.0
        )


@dataclass
class TelemetryReadings:
    """Telemetry readings from CMIS module."""
    timestamp: float
    interface: str
    
    # Optical power
    tx_power_dbm: Optional[float]
    rx_power_dbm: Optional[float]
    rx_total_power_dbm: Optional[float]
    rx_signal_power_dbm: Optional[float]
    
    # Performance metrics
    osnr_db: Optional[float]
    esnr_db: Optional[float]
    pre_fec_ber: Optional[float]
    post_fec_ber: Optional[float]
    
    # Module status
    temperature_c: Optional[float]
    supply_voltage_v: Optional[float]
    bias_current_ma: Optional[float]
    
    # Configuration
    frequency_mhz: Optional[int]
    app_code: Optional[int]
    tx_power_setting: Optional[float]
    
    # Status flags
    module_state: Optional[str]
    fault_flags: Optional[List[str]]
    
    @property
    def is_healthy(self) -> bool:
        """Check if readings indicate healthy module."""
        if not all([
            self.tx_power_dbm is not None,
            self.rx_power_dbm is not None,
            self.osnr_db is not None
        ]):
            return False
        
        # Check thresholds
        if self.tx_power_dbm < -20 or self.tx_power_dbm > 5:
            return False
        
        if self.osnr_db < 15:  # Minimum OSNR for 400G
            return False
        
        if self.pre_fec_ber and self.pre_fec_ber > 1e-3:
            return False
        
        return True


class CMISDriver:
    """Driver for CMIS-compliant 400G ZR modules."""
    
    # NeoPhotonics QDDMA400700C2000 specific constants
    FREQUENCY_MIN = 191300  # MHz (191.3 THz)
    FREQUENCY_MAX = 196100  # MHz (196.1 THz)
    FREQUENCY_STEP = 100    # MHz per register step
    
    TX_POWER_MIN = -15.0    # dBm
    TX_POWER_MAX = -8.0     # dBm
    TX_POWER_STEP = 0.1     # dB per register step
    
    APP_CONFIGS = {
        ApplicationCode.ZR_400G_DWDM: {
            "name": "400ZR_DWDM",
            "rate_gbps": 400,
            "modulation": "16QAM",
            "host_assignment": 0x01,
            "media_assignment": 0x01,
            "baud_rate_gbd": 64.0
        },
        ApplicationCode.ZR_400G_OFEC_16QAM: {
            "name": "400ZR_OFEC_16QAM",
            "rate_gbps": 400,
            "modulation": "16QAM",
            "host_assignment": 0x01,
            "media_assignment": 0x01,
            "baud_rate_gbd": 64.0
        },
        ApplicationCode.ZR_100G_OFEC_16QAM: {
            "name": "100G_ZR_OFEC_16QAM",
            "rate_gbps": 100,
            "modulation": "16QAM",
            "host_assignment": 0x55,
            "media_assignment": 0x01,
            "baud_rate_gbd": 32.0
        },
        ApplicationCode.ZR_100G_OFEC_8QAM: {
            "name": "100G_ZR_OFEC_8QAM",
            "rate_gbps": 100,
            "modulation": "8QAM",
            "host_assignment": 0x55,
            "media_assignment": 0x01,
            "baud_rate_gbd": 32.0
        },
        ApplicationCode.ZR_100G_OFEC_QPSK: {
            "name": "100G_ZR_OFEC_QPSK",
            "rate_gbps": 100,
            "modulation": "QPSK",
            "host_assignment": 0x55,
            "media_assignment": 0x01,
            "baud_rate_gbd": 32.0
        }
    }
    
    def __init__(self, hardware_config):
        """Initialize CMIS driver."""
        self.config = hardware_config
        self.logger = logging.getLogger(f"cmis-driver.{id(self)}")
        self.lock = threading.RLock()
        
        # Initialize platform chassis
        self._init_platform()
        
        # Cache for SFP objects
        self.sfp_cache = {}
        
        # Module capabilities cache
        self.capabilities_cache = {}
        
        self.logger.info(f"CMIS driver initialized for {len(self.config.interface_mappings)} interfaces")
    
    def _init_platform(self):
        """Initialize SONiC platform."""
        try:
            self.platform_chassis = Platform().get_chassis()
            if not self.platform_chassis:
                raise RuntimeError("Failed to get platform chassis")
            
            self.logger.info(f"Platform initialized: {self.platform_chassis.get_name()}")
            
        except Exception as e:
            self.logger.error(f"Platform initialization failed: {e}")
            raise
    
    def _get_sfp(self, interface: str) -> Optional[Sfp]:
        """Get SFP object for interface with caching."""
        with self.lock:
            if interface in self.sfp_cache:
                return self.sfp_cache[interface]
            
            if interface not in self.config.interface_mappings:
                self.logger.error(f"Interface {interface} not in mappings")
                return None
            
            try:
                port_num = self.config.interface_mappings[interface]
                sfp = self.platform_chassis.get_sfp(port_num)
                
                if not sfp:
                    self.logger.error(f"No SFP at port {port_num}")
                    return None
                
                self.sfp_cache[interface] = sfp
                return sfp
                
            except Exception as e:
                self.logger.error(f"Failed to get SFP for {interface}: {e}")
                return None
    
    def _select_page(self, sfp: Sfp, page: int) -> bool:
        """Select CMIS memory page."""
        try:
            sfp.write_eeprom(CMISPage.VENDOR_SPECIFIC, 1, bytes([page]))
            time.sleep(0.001)  # 1ms delay
            
            # Verify selection
            current_page = sfp.read_eeprom(CMISPage.VENDOR_SPECIFIC, 1)
            if current_page and current_page[0] == page:
                return True
            
            self.logger.warning(f"Page selection verification failed for page {page:#04x}")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to select page {page:#04x}: {e}")
            return False
    
    def _read_module_state(self, sfp: Sfp) -> Optional[ModuleState]:
        """Read module state."""
        try:
            if not self._select_page(sfp, CMISPage.MODULE_STATE):
                return None
            
            state_data = sfp.read_eeprom(0x02, 1)
            if state_data:
                return ModuleState(state_data[0] & 0x0F)
            
        except Exception as e:
            self.logger.error(f"Failed to read module state: {e}")
        
        return None
    
    def _wait_for_ready(self, sfp: Sfp, timeout: float = 5.0) -> bool:
        """Wait for module to be ready."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            state = self._read_module_state(sfp)
            
            if state in [ModuleState.HIGH_POWER, ModuleState.HIGH_POWER_UP]:
                return True
            elif state == ModuleState.FAULT:
                self.logger.error("Module in fault state")
                return False
            
            time.sleep(0.1)
        
        self.logger.warning(f"Module not ready after {timeout}s")
        return False
    
    def check_health(self) -> bool:
        """Check health of all interfaces."""
        healthy_count = 0
        
        for interface in self.config.assigned_transceivers:
            try:
                sfp = self._get_sfp(interface)
                if not sfp:
                    continue
                
                if sfp.get_presence():
                    state = self._read_module_state(sfp)
                    if state in [ModuleState.HIGH_POWER, ModuleState.HIGH_POWER_UP]:
                        healthy_count += 1
                    else:
                        self.logger.warning(f"Interface {interface} not in high power state: {state}")
                else:
                    self.logger.warning(f"Interface {interface} not present")
                    
            except Exception as e:
                self.logger.error(f"Health check failed for {interface}: {e}")
        
        return healthy_count > 0
    
    def get_interface_status(self, interface: str) -> Dict[str, Any]:
        """Get interface status."""
        result = {
            "interface": interface,
            "present": False,
            "operational": False,
            "admin_state": "unknown",
            "speed": "unknown",
            "errors": []
        }
        
        try:
            sfp = self._get_sfp(interface)
            if not sfp:
                result["errors"].append("SFP not found")
                return result
            
            # Check presence
            result["present"] = sfp.get_presence()
            if not result["present"]:
                result["errors"].append("Transceiver not present")
                return result
            
            # Get module info
            result["vendor"] = sfp.get_vendor() or "unknown"
            result["part_number"] = sfp.get_part_number() or "unknown"
            result["serial"] = sfp.get_serial() or "unknown"
            
            # Get module state
            state = self._read_module_state(sfp)
            if state:
                result["module_state"] = state.name
                result["operational"] = state in [ModuleState.HIGH_POWER, ModuleState.HIGH_POWER_UP]
            
            # Read current configuration
            config = self._read_current_config(sfp)
            if config:
                result.update(config)
            
            # Read basic telemetry
            telemetry = self._read_basic_telemetry(sfp)
            if telemetry:
                result["telemetry"] = telemetry
            
        except Exception as e:
            result["errors"].append(f"Interface status error: {str(e)}")
            self.logger.error(f"Failed to get status for {interface}: {e}")
        
        return result
    
    def _read_current_config(self, sfp: Sfp) -> Optional[Dict[str, Any]]:
        """Read current module configuration."""
        try:
            config = {}
            
            # Read frequency
            if self._select_page(sfp, CMISPage.LASER_CONFIG):
                freq_data = sfp.read_eeprom(0x14, 2)
                if freq_data and len(freq_data) == 2:
                    reg_value = struct.unpack('>H', freq_data)[0]
                    config["frequency_mhz"] = self.FREQUENCY_MIN + (reg_value * self.FREQUENCY_STEP)
            
            # Read application
            if self._select_page(sfp, CMISPage.APPLICATIONS):
                app_data = sfp.read_eeprom(0x02, 1)
                if app_data:
                    config["app_code"] = app_data[0]
                    config["app_name"] = self.APP_CONFIGS.get(
                        ApplicationCode(app_data[0]), {}
                    ).get("name", "unknown")
            
            # Read TX power
            if self._select_page(sfp, CMISPage.TX_POWER):
                tx_data = sfp.read_eeprom(0x10, 1)
                if tx_data:
                    reg_value = tx_data[0]
                    config["tx_power_dbm"] = self.TX_POWER_MIN + (reg_value * self.TX_POWER_STEP)
            
            return config
            
        except Exception as e:
            self.logger.error(f"Failed to read current config: {e}")
            return None
    
    def _read_basic_telemetry(self, sfp: Sfp) -> Optional[Dict[str, Any]]:
        """Read basic telemetry data."""
        try:
            telemetry = {}
            
            # Select diagnostics page
            if not self._select_page(sfp, CMISPage.DIAGNOSTICS):
                return None
            
            # Read TX power (address 182-183, scale /100)
            tx_data = sfp.read_eeprom(182, 2)
            if tx_data and len(tx_data) == 2:
                telemetry["tx_power_dbm"] = struct.unpack('>h', tx_data)[0] / 100.0
            
            # Read RX power (address 188-189)
            rx_data = sfp.read_eeprom(188, 2)
            if rx_data and len(rx_data) == 2:
                telemetry["rx_power_dbm"] = struct.unpack('>h', rx_data)[0] / 100.0
            
            # Read OSNR (address 158-159, scale /10)
            osnr_data = sfp.read_eeprom(158, 2)
            if osnr_data and len(osnr_data) == 2:
                telemetry["osnr_db"] = struct.unpack('>H', osnr_data)[0] / 10.0
            
            # Read temperature (address 14-15, scale /256)
            temp_data = sfp.read_eeprom(14, 2)
            if temp_data and len(temp_data) == 2:
                telemetry["temperature_c"] = struct.unpack('>h', temp_data)[0] / 256.0
            
            return telemetry
            
        except Exception as e:
            self.logger.error(f"Failed to read basic telemetry: {e}")
            return None
    
    def get_pluggable_info(self) -> Dict[str, Any]:
        """Get information about all pluggable transceivers."""
        info = {}
        
        for interface in self.config.assigned_transceivers:
            try:
                sfp = self._get_sfp(interface)
                if not sfp:
                    info[interface] = {"error": "SFP not found"}
                    continue
                
                ifname_info = {
                    "present": sfp.get_presence(),
                    "vendor": sfp.get_vendor() or "unknown",
                    "part_number": sfp.get_part_number() or "unknown",
                    "serial": sfp.get_serial() or "unknown",
                    "revision": sfp.get_revision() or "unknown",
                    "type": "QSFP-DD 400G ZR",
                }
                
                if ifname_info["present"]:
                    # Get capabilities from module
                    caps = self._get_module_capabilities(sfp)
                    ifname_info.update(caps)
                    
                    # Get current state
                    state = self._read_module_state(sfp)
                    if state:
                        ifname_info["module_state"] = state.name
                
                info[interface] = ifname_info
                
            except Exception as e:
                info[interface] = {
                    "error": str(e),
                    "present": False
                }
                self.logger.error(f"Failed to get info for {interface}: {e}")
        
        return info
    
    def _get_module_capabilities(self, sfp: Sfp) -> Dict[str, Any]:
        """Get module capabilities."""
        try:
            # Check cache
            cache_key = f"{sfp.get_vendor()}-{sfp.get_part_number()}"
            if cache_key in self.capabilities_cache:
                return self.capabilities_cache[cache_key]
            
            capabilities = {
                "frequency_range": {
                    "min_mhz": self.FREQUENCY_MIN,
                    "max_mhz": self.FREQUENCY_MAX,
                    "step_mhz": self.FREQUENCY_STEP
                },
                "tx_power_range": {
                    "min_dbm": self.TX_POWER_MIN,
                    "max_dbm": self.TX_POWER_MAX,
                    "step_db": self.TX_POWER_STEP
                },
                "supported_applications": [],
                "supported_modulations": ["16QAM", "8QAM", "QPSK"],
                "max_baud_rate_gbd": 64.0,
                "power_class": 8,  # 19.0W max
            }
            
            # Read advertised applications from module
            if self._select_page(sfp, CMISPage.APPLICATIONS):
                # Read number of supported applications (address 0x00)
                num_apps_data = sfp.read_eeprom(0x00, 1)
                if num_apps_data:
                    num_apps = num_apps_data[0]
                    
                    for i in range(min(num_apps, 8)):  # Max 8 apps
                        app_data = sfp.read_eeprom(0x10 + (i * 16), 16)
                        if app_data and len(app_data) == 16:
                            # Parse application advertisement
                            app_code = app_data[0]
                            if app_code in [ac.value for ac in ApplicationCode]:
                                app_name = self.APP_CONFIGS.get(
                                    ApplicationCode(app_code), {}
                                ).get("name", f"Unknown_{app_code:#04x}")
                                capabilities["supported_applications"].append({
                                    "code": app_code,
                                    "name": app_name
                                })
            
            # Cache the capabilities
            self.capabilities_cache[cache_key] = capabilities
            return capabilities
            
        except Exception as e:
            self.logger.error(f"Failed to get module capabilities: {e}")
            return {
                "frequency_range": {
                    "min_mhz": self.FREQUENCY_MIN,
                    "max_mhz": self.FREQUENCY_MAX,
                    "step_mhz": self.FREQUENCY_STEP
                },
                "error": str(e)[:100]
            }
    
    def setup_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Setup a new connection with given configuration."""
        connection_id = config.get("connection_id", "unknown")
        frequency_mhz = config.get("frequency")
        endpoints = config.get("endpoint_config", [])
        
        result = {
            "success": False,
            "connection_id": connection_id,
            "frequency_mhz": frequency_mhz,
            "endpoints": [],
            "errors": []
        }
        
        if frequency_mhz is None:
            result["errors"].append("Frequency required")
            return result
        
        if not endpoints:
            result["errors"].append("Endpoints required")
            return result
        
        # Apply configuration to each endpoint
        for endpoint in endpoints:
            if endpoint.get("pop_id") != self.config.pop_id:
                continue
            
            interface = endpoint.get("port_id")
            app_code = endpoint.get("app")
            tx_power = endpoint.get("tx_power_level")
            
            endpoint_result = self._configure_interface(
                interface, frequency_mhz, app_code, tx_power
            )
            
            result["endpoints"].append({
                "interface": interface,
                **endpoint_result
            })
            
            if not endpoint_result.get("success"):
                result["errors"].append(
                    f"Endpoint {interface} failed: {endpoint_result.get('error')}"
                )
        
        result["success"] = len(result["errors"]) == 0
        return result
    
    def _configure_interface(
        self,
        interface: str,
        frequency_mhz: int,
        app_code: Optional[int],
        tx_power_dbm: Optional[float]
    ) -> Dict[str, Any]:
        """Configure a single interface."""
        result = {
            "success": False,
            "interface": interface,
            "frequency_mhz": frequency_mhz,
            "app_code": app_code,
            "tx_power_dbm": tx_power_dbm,
            "error": None,
            "warnings": []
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
                result["error"] = "Module not ready for configuration"
                return result
            
            # Apply frequency
            if frequency_mhz is not None:
                freq_result = self._apply_frequency(sfp, frequency_mhz)
                if not freq_result["success"]:
                    result["error"] = f"Frequency config failed: {freq_result['error']}"
                    return result
            
            # Apply application
            if app_code is not None:
                app_result = self._apply_application(sfp, app_code)
                if not app_result["success"]:
                    result["error"] = f"Application config failed: {app_result['error']}"
                    return result
                result["app_name"] = app_result.get("app_name")
            
            # Apply TX power
            if tx_power_dbm is not None:
                tx_result = self._apply_tx_power(sfp, tx_power_dbm)
                if not tx_result["success"]:
                    result["error"] = f"TX power config failed: {tx_result['error']}"
                    return result
            
            # Trigger module re-initialization if application changed
            if app_code is not None:
                self._trigger_module_init(sfp)
                time.sleep(0.5)  # Allow time for reinitialization
            
            # Verify configuration
            verify_result = self._verify_configuration(
                sfp, frequency_mhz, app_code, tx_power_dbm
            )
            
            if not verify_result["matched"]:
                result["warnings"].append("Configuration verification failed")
                result["verification"] = verify_result
            
            result["success"] = True
            self.logger.info(
                f"Configured {interface}: "
                f"freq={frequency_mhz}MHz, "
                f"app={app_code}, "
                f"tx={tx_power_dbm}dBm"
            )
            
        except Exception as e:
            result["error"] = f"Configuration error: {str(e)}"
            self.logger.error(f"Failed to configure {interface}: {e}", exc_info=True)
        
        return result
    
    def _apply_frequency(self, sfp: Sfp, frequency_mhz: int) -> Dict[str, Any]:
        """Apply frequency configuration."""
        result = {"success": False, "error": None}
        
        try:
            # Validate frequency
            if not (self.FREQUENCY_MIN <= frequency_mhz <= self.FREQUENCY_MAX):
                result["error"] = f"Frequency {frequency_mhz}MHz out of range"
                return result
            
            # Calculate register value
            reg_value = (frequency_mhz - self.FREQUENCY_MIN) // self.FREQUENCY_STEP
            
            if reg_value < 0 or reg_value > 0xFFFF:
                result["error"] = f"Invalid register value: {reg_value}"
                return result
            
            # Select laser config page
            if not self._select_page(sfp, CMISPage.LASER_CONFIG):
                result["error"] = "Failed to select laser config page"
                return result
            
            # Write frequency register (16-bit big endian)
            freq_bytes = struct.pack('>H', reg_value)
            sfp.write_eeprom(0x14, 2, freq_bytes)
            
            # Small delay for laser tuning
            time.sleep(0.1)
            
            result["success"] = True
            result["register_value"] = reg_value
            
        except Exception as e:
            result["error"] = f"Frequency configuration error: {str(e)}"
        
        return result
    
    def _apply_application(self, sfp: Sfp, app_code: int) -> Dict[str, Any]:
        """Apply application configuration."""
        result = {"success": False, "error": None, "app_name": None}
        
        try:
            # Validate application code
            if app_code not in [ac.value for ac in ApplicationCode]:
                result["error"] = f"Invalid app code: {app_code:#04x}"
                return result
            
            app_config = self.APP_CONFIGS.get(ApplicationCode(app_code))
            if not app_config:
                result["error"] = f"No config for app code: {app_code:#04x}"
                return result
            
            # Select applications page
            if not self._select_page(sfp, CMISPage.APPLICATIONS):
                result["error"] = "Failed to select applications page"
                return result
            
            # Write application select
            sfp.write_eeprom(0x02, 1, bytes([app_code]))
            
            # Write host and media assignments
            sfp.write_eeprom(0x03, 1, bytes([app_config["host_assignment"]]))
            sfp.write_eeprom(0x04, 1, bytes([app_config["media_assignment"]]))
            
            result["success"] = True
            result["app_name"] = app_config["name"]
            
        except Exception as e:
            result["error"] = f"Application configuration error: {str(e)}"
        
        return result
    
    def _apply_tx_power(self, sfp: Sfp, tx_power_dbm: float) -> Dict[str, Any]:
        """Apply TX power configuration."""
        result = {"success": False, "error": None}
        
        try:
            # Validate power
            if not (self.TX_POWER_MIN <= tx_power_dbm <= self.TX_POWER_MAX):
                result["error"] = f"TX power {tx_power_dbm}dBm out of range"
                return result
            
            # Calculate register value
            reg_value = int((tx_power_dbm - self.TX_POWER_MIN) / self.TX_POWER_STEP)
            
            if reg_value < 0 or reg_value > 0xFF:
                result["error"] = f"Invalid register value: {reg_value}"
                return result
            
            # Select TX power page
            if not self._select_page(sfp, CMISPage.TX_POWER):
                result["error"] = "Failed to select TX power page"
                return result
            
            # Write TX power register
            sfp.write_eeprom(0x10, 1, bytes([reg_value]))
            
            result["success"] = True
            result["register_value"] = reg_value
            
        except Exception as e:
            result["error"] = f"TX power configuration error: {str(e)}"
        
        return result
    
    def _trigger_module_init(self, sfp: Sfp):
        """Trigger module re-initialization."""
        try:
            if not self._select_page(sfp, CMISPage.MODULE_STATE):
                return
            
            # Write to INIT register
            sfp.write_eeprom(0x04, 1, bytes([0x01]))
            
        except Exception as e:
            self.logger.warning(f"Failed to trigger module init: {e}")
    
    def _verify_configuration(
        self,
        sfp: Sfp,
        target_freq: Optional[int],
        target_app: Optional[int],
        target_tx: Optional[float]
    ) -> Dict[str, Any]:
        """Verify current configuration matches target."""
        verification = {
            "matched": True,
            "details": {}
        }
        
        try:
            # Verify frequency
            if target_freq is not None:
                if not self._select_page(sfp, CMISPage.LASER_CONFIG):
                    verification["details"]["frequency"] = {"error": "Page select failed"}
                    verification["matched"] = False
                else:
                    freq_data = sfp.read_eeprom(0x14, 2)
                    if freq_data and len(freq_data) == 2:
                        reg_value = struct.unpack('>H', freq_data)[0]
                        actual_freq = self.FREQUENCY_MIN + (reg_value * self.FREQUENCY_STEP)
                        freq_match = abs(actual_freq - target_freq) <= self.FREQUENCY_STEP
                        
                        verification["details"]["frequency"] = {
                            "target": target_freq,
                            "actual": actual_freq,
                            "match": freq_match,
                            "difference": abs(actual_freq - target_freq)
                        }
                        verification["matched"] &= freq_match
                    else:
                        verification["details"]["frequency"] = {"error": "Read failed"}
                        verification["matched"] = False
            
            # Verify application
            if target_app is not None:
                if not self._select_page(sfp, CMISPage.APPLICATIONS):
                    verification["details"]["application"] = {"error": "Page select failed"}
                    verification["matched"] = False
                else:
                    app_data = sfp.read_eeprom(0x02, 1)
                    if app_data:
                        actual_app = app_data[0]
                        app_match = actual_app == target_app
                        
                        verification["details"]["application"] = {
                            "target": target_app,
                            "actual": actual_app,
                            "match": app_match
                        }
                        verification["matched"] &= app_match
                    else:
                        verification["details"]["application"] = {"error": "Read failed"}
                        verification["matched"] = False
            
            # Verify TX power
            if target_tx is not None:
                if not self._select_page(sfp, CMISPage.TX_POWER):
                    verification["details"]["tx_power"] = {"error": "Page select failed"}
                    verification["matched"] = False
                else:
                    tx_data = sfp.read_eeprom(0x10, 1)
                    if tx_data:
                        reg_value = tx_data[0]
                        actual_tx = self.TX_POWER_MIN + (reg_value * self.TX_POWER_STEP)
                        tx_match = abs(actual_tx - target_tx) <= (self.TX_POWER_STEP * 2)
                        
                        verification["details"]["tx_power"] = {
                            "target": target_tx,
                            "actual": actual_tx,
                            "match": tx_match,
                            "difference": abs(actual_tx - target_tx)
                        }
                        verification["matched"] &= tx_match
                    else:
                        verification["details"]["tx_power"] = {"error": "Read failed"}
                        verification["matched"] = False
            
        except Exception as e:
            verification["matched"] = False
            verification["error"] = str(e)
        
        return verification
    
    def reconfig_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Reconfigure an existing connection."""
        connection_id = config.get("connection_id", "unknown")
        endpoints = config.get("endpoint_config", [])
        
        result = {
            "success": False,
            "connection_id": connection_id,
            "reconfigured": [],
            "errors": []
        }
        
        if not endpoints:
            result["errors"].append("Endpoints required")
            return result
        
        # Reconfigure each endpoint
        for endpoint in endpoints:
            if endpoint.get("pop_id") != self.config.pop_id:
                continue
            
            interface = endpoint.get("port_id")
            tx_power = endpoint.get("tx_power_level")
            
            if tx_power is None:
                result["errors"].append(f"TX power required for {interface}")
                continue
            
            reconf_result = self._reconfigure_interface(interface, tx_power)
            
            result["reconfigured"].append({
                "interface": interface,
                **reconf_result
            })
            
            if not reconf_result.get("success"):
                result["errors"].append(
                    f"Endpoint {interface} failed: {reconf_result.get('error')}"
                )
        
        result["success"] = len(result["errors"]) == 0
        return result
    
    def _reconfigure_interface(self, interface: str, tx_power_dbm: float) -> Dict[str, Any]:
        """Reconfigure TX power for an interface."""
        result = {
            "success": False,
            "interface": interface,
            "tx_power_dbm": tx_power_dbm,
            "error": None
        }
        
        try:
            sfp = self._get_sfp(interface)
            if not sfp:
                result["error"] = "SFP not found"
                return result
            
            if not sfp.get_presence():
                result["error"] = "Transceiver not present"
                return result
            
            # Apply TX power
            tx_result = self._apply_tx_power(sfp, tx_power_dbm)
            if not tx_result["success"]:
                result["error"] = tx_result["error"]
                return result
            
            result["success"] = True
            self.logger.info(f"Reconfigured {interface} TX power to {tx_power_dbm}dBm")
            
        except Exception as e:
            result["error"] = f"Reconfiguration error: {str(e)}"
            self.logger.error(f"Failed to reconfigure {interface}: {e}")
        
        return result
    
    def control_interface(self, interface: str, admin_state: str) -> Dict[str, Any]:
        """Control interface administrative state."""
        result = {
            "success": False,
            "interface": interface,
            "admin_state": admin_state,
            "error": None
        }
        
        try:
            # This would use SONiC CLI commands
            # For now, return success for simulation
            result["success"] = True
            self.logger.info(f"Interface {interface} set to {admin_state}")
            
        except Exception as e:
            result["error"] = f"Interface control error: {str(e)}"
        
        return result
    
    def read_telemetry(self, interface: str) -> TelemetryReadings:
        """Read comprehensive telemetry from interface."""
        timestamp = time.time()
        
        try:
            sfp = self._get_sfp(interface)
            if not sfp or not sfp.get_presence():
                return TelemetryReadings(
                    timestamp=timestamp,
                    interface=interface,
                    tx_power_dbm=None,
                    rx_power_dbm=None,
                    rx_total_power_dbm=None,
                    rx_signal_power_dbm=None,
                    osnr_db=None,
                    esnr_db=None,
                    pre_fec_ber=None,
                    post_fec_ber=None,
                    temperature_c=None,
                    supply_voltage_v=None,
                    bias_current_ma=None,
                    frequency_mhz=None,
                    app_code=None,
                    tx_power_setting=None,
                    module_state=None,
                    fault_flags=None
                )
            
            # Select diagnostics page
            if not self._select_page(sfp, CMISPage.DIAGNOSTICS):
                raise RuntimeError("Failed to select diagnostics page")
            
            # Read all telemetry registers
            readings = self._read_all_telemetry_registers(sfp)
            
            # Get current configuration
            config = self._read_current_config(sfp)
            
            # Get module state
            state = self._read_module_state(sfp)
            
            return TelemetryReadings(
                timestamp=timestamp,
                interface=interface,
                tx_power_dbm=readings.get("tx_power_dbm"),
                rx_power_dbm=readings.get("rx_power_dbm"),
                rx_total_power_dbm=readings.get("rx_total_power_dbm"),
                rx_signal_power_dbm=readings.get("rx_signal_power_dbm"),
                osnr_db=readings.get("osnr_db"),
                esnr_db=readings.get("esnr_db"),
                pre_fec_ber=readings.get("pre_fec_ber"),
                post_fec_ber=readings.get("post_fec_ber"),
                temperature_c=readings.get("temperature_c"),
                supply_voltage_v=readings.get("supply_voltage_v"),
                bias_current_ma=readings.get("bias_current_ma"),
                frequency_mhz=config.get("frequency_mhz") if config else None,
                app_code=config.get("app_code") if config else None,
                tx_power_setting=config.get("tx_power_dbm") if config else None,
                module_state=state.name if state else None,
                fault_flags=readings.get("fault_flags", [])
            )
            
        except Exception as e:
            self.logger.error(f"Failed to read telemetry for {interface}: {e}")
            
            # Return empty readings on error
            return TelemetryReadings(
                timestamp=timestamp,
                interface=interface,
                tx_power_dbm=None,
                rx_power_dbm=None,
                rx_total_power_dbm=None,
                rx_signal_power_dbm=None,
                osnr_db=None,
                esnr_db=None,
                pre_fec_ber=None,
                post_fec_ber=None,
                temperature_c=None,
                supply_voltage_v=None,
                bias_current_ma=None,
                frequency_mhz=None,
                app_code=None,
                tx_power_setting=None,
                module_state=None,
                fault_flags=[str(e)]
            )
    
    def _read_all_telemetry_registers(self, sfp: Sfp) -> Dict[str, Any]:
        """Read all telemetry registers from diagnostics page."""
        readings = {}
        
        try:
            # TX Power (address 182-183, scale /100)
            tx_data = sfp.read_eeprom(182, 2)
            if tx_data and len(tx_data) == 2:
                readings["tx_power_dbm"] = struct.unpack('>h', tx_data)[0] / 100.0
            
            # RX Total Power (address 188-189)
            rx_tot_data = sfp.read_eeprom(188, 2)
            if rx_tot_data and len(rx_tot_data) == 2:
                readings["rx_total_power_dbm"] = struct.unpack('>h', rx_tot_data)[0] / 100.0
            
            # RX Signal Power (address 194-195)
            rx_sig_data = sfp.read_eeprom(194, 2)
            if rx_sig_data and len(rx_sig_data) == 2:
                readings["rx_signal_power_dbm"] = struct.unpack('>h', rx_sig_data)[0] / 100.0
            
            # OSNR (address 158-159, scale /10)
            osnr_data = sfp.read_eeprom(158, 2)
            if osnr_data and len(osnr_data) == 2:
                readings["osnr_db"] = struct.unpack('>H', osnr_data)[0] / 10.0
            
            # eSNR (address 164-165, scale /10)
            esnr_data = sfp.read_eeprom(164, 2)
            if esnr_data and len(esnr_data) == 2:
                readings["esnr_db"] = struct.unpack('>H', esnr_data)[0] / 10.0
            
            # Pre-FEC BER (address 176-179)
            ber_data = sfp.read_eeprom(176, 4)
            if ber_data and len(ber_data) == 4:
                log_ber = struct.unpack('>i', ber_data)[0]
                readings["pre_fec_ber"] = 10 ** (log_ber / 65536.0) if log_ber != 0 else 0.0
            
            # Temperature (address 14-15, scale /256)
            temp_data = sfp.read_eeprom(14, 2)
            if temp_data and len(temp_data) == 2:
                readings["temperature_c"] = struct.unpack('>h', temp_data)[0] / 256.0
            
            # Supply Voltage (address 16-17, scale /10000)
            volt_data = sfp.read_eeprom(16, 2)
            if volt_data and len(volt_data) == 2:
                readings["supply_voltage_v"] = struct.unpack('>H', volt_data)[0] / 10000.0
            
            # Bias Current (address 26-27, scale /500)
            bias_data = sfp.read_eeprom(26, 2)
            if bias_data and len(bias_data) == 2:
                readings["bias_current_ma"] = struct.unpack('>H', bias_data)[0] / 500.0
            
            # Read fault flags (address 0x02 on module state page)
            if self._select_page(sfp, CMISPage.MODULE_STATE):
                fault_data = sfp.read_eeprom(0x03, 1)
                if fault_data:
                    fault_byte = fault_data[0]
                    fault_flags = []
                    
                    if fault_byte & 0x01:
                        fault_flags.append("temp_high")
                    if fault_byte & 0x02:
                        fault_flags.append("temp_low")
                    if fault_byte & 0x04:
                        fault_flags.append("vcc_high")
                    if fault_byte & 0x08:
                        fault_flags.append("vcc_low")
                    if fault_byte & 0x10:
                        fault_flags.append("tx_power_high")
                    if fault_byte & 0x20:
                        fault_flags.append("tx_power_low")
                    if fault_byte & 0x40:
                        fault_flags.append("rx_power_high")
                    if fault_byte & 0x80:
                        fault_flags.append("rx_power_low")
                    
                    readings["fault_flags"] = fault_flags
            
        except Exception as e:
            self.logger.error(f"Failed to read telemetry registers: {e}")
            readings["error"] = str(e)
        
        return readings