# link_database/schema.py
# Link Database Schema for IPoWDM Networks

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
import json

class ConnectionStatus(str, Enum):
    ACTIVE = "active"
    PLANNED = "planned"
    DISABLED = "disabled"
    FAILED = "failed"

class FrequencySlotStatus(str, Enum):
    AVAILABLE = "available"
    OCCUPIED = "occupied"
    RESERVED = "reserved"
    BLOCKED = "blocked"

# Database Models
class POP:
    """Point of Presence"""
    def __init__(self, pop_id: str, name: str, location: str, operator: str = "telco"):
        self.pop_id = pop_id  # e.g., "pop1", "pop2"
        self.name = name      # e.g., "New York DC"
        self.location = location  # e.g., "40.7128,-74.0060"
        self.operator = operator  # Main telco operator
        self.routers: List[Router] = []
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

class Router:
    """IPoWDM Router at a POP"""
    def __init__(self, router_id: str, pop_id: str, model: str = "Edgecore"):
        self.router_id = router_id  # e.g., "router1", "router2"
        self.pop_id = pop_id
        self.model = model
        self.interfaces: List[Interface] = []
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

class Interface:
    """Router Interface with Pluggable Transceiver"""
    def __init__(self, interface_id: str, router_id: str, pop_id: str, 
                 port_num: int, if_type: str = "Ethernet"):
        self.interface_id = interface_id  # e.g., "Ethernet48"
        self.router_id = router_id
        self.pop_id = pop_id
        self.port_num = port_num
        self.if_type = if_type
        self.transceiver: Optional[Transceiver] = None
        self.assigned_to: Optional[str] = None  # Virtual operator (vOp1, vOp2)
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

class Transceiver:
    """Pluggable Transceiver (ZR/ZR+)"""
    def __init__(self, interface_id: str, vendor: str, part_number: str,
                 serial: str, type: str = "ZR", max_rate: int = 400):
        self.interface_id = interface_id
        self.vendor = vendor  # e.g., "Acacia", "Infinera"
        self.part_number = part_number
        self.serial = serial
        self.type = type  # ZR, ZR+
        self.max_rate = max_rate  # Gbps
        self.frequency_range: List[int] = [191300, 196100]  # MHz
        self.tx_power_range: List[float] = [-15.0, -8.0]  # dBm
        self.supported_app_codes: Dict[int, Dict] = {
            1: {"rate": "400G", "mode": "DWDM-amplified"},
            2: {"rate": "400G", "mode": "OFEC-16QAM"},
            3: {"rate": "100G", "mode": "OFEC-16QAM"},
        }
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

class OpticalLink:
    """Physical Optical Link between two POPs"""
    def __init__(self, link_id: str, pop_a: str, pop_b: str, 
                 distance_km: float, fiber_type: str = "SMF"):
        self.link_id = link_id  # e.g., "link-pop1-pop2"
        self.pop_a = pop_a
        self.pop_b = pop_b
        self.distance_km = distance_km
        self.fiber_type = fiber_type
        self.total_channels = 96  # C-band channels
        self.channel_spacing = 50  # GHz
        self.frequency_slots: Dict[int, FrequencySlot] = {}  # key: frequency
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

class FrequencySlot:
    """Frequency slot on an optical link"""
    def __init__(self, frequency: int, link_id: str):
        self.frequency = frequency  # MHz, e.g., 191300, 191350, ...
        self.link_id = link_id
        self.status = FrequencySlotStatus.AVAILABLE
        self.occupied_by: Optional[str] = None  # connection_id
        self.virtual_operator: Optional[str] = None  # vOp1, vOp2
        self.occupied_since: Optional[str] = None
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

class Connection:
    """End-to-end optical connection"""
    def __init__(self, connection_id: str, pop_a: str, pop_b: str):
        self.connection_id = connection_id
        self.pop_a = pop_a
        self.pop_b = pop_b
        self.status = ConnectionStatus.PLANNED
        self.frequency: Optional[int] = None  # MHz
        self.bandwidth: Optional[int] = None  # Gbps
        self.interfaces: List[Dict] = []  # [{"pop": "pop1", "interface": "Ethernet48"}, ...]
        self.virtual_operator: Optional[str] = None
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()
        self.activated_at: Optional[str] = None
        self.telemetry_enabled = False

class VirtualOperator:
    """Virtual Operator Resource Allocation"""
    def __init__(self, vop_id: str, name: str):
        self.vop_id = vop_id  # e.g., "vOp1", "vOp2"
        self.name = name  # e.g., "CloudProviderA"
        self.assigned_interfaces: Dict[str, List[str]] = {}  # pop_id -> [interface_ids]
        self.assigned_frequencies: Dict[str, List[int]] = {}  # link_id -> [frequencies]
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()
