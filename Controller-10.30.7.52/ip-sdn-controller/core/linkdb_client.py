"""
Link Database Client for IP SDN Controller - Updated for your Redis schema
"""

import json
import logging
from typing import Dict, List, Optional, Any, Set, Tuple
import redis
from datetime import datetime

from config.settings import settings
from models.schemas import PopNode, NetworkLink


logger = logging.getLogger(__name__)


class LinkDBClient:
    """Professional client for Link Database operations."""
    
    def __init__(self):
        """Initialize Link Database connection."""
        self._client = None
        self._initialize_client()
    
    def _initialize_client(self) -> None:
        """Initialize Redis client connection."""
        try:
            self._client = redis.Redis(
                host=settings.LINKDB_HOST,
                port=settings.LINKDB_PORT,
                password=settings.LINKDB_PASSWORD,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # Test connection
            self._client.ping()
            logger.info(f"Link Database connected to {settings.LINKDB_HOST}:{settings.LINKDB_PORT}")
            
        except Exception as e:
            logger.error(f"Failed to connect to Link Database: {e}")
            raise ConnectionError(f"Link DB connection failed: {e}")
    
    def get_topology(self) -> Tuple[Dict[str, PopNode], Dict[str, NetworkLink]]:
        """
        Get complete network topology from Link Database.
        Updated to match your Redis schema with pop_a/pop_b and distance_km.
        """
        try:
            pops = {}
            links = {}
            
            # Get all POPs
            pop_keys = self._client.smembers("pops") or []
            for pop_id in pop_keys:
                pop_data = self._client.hgetall(f"pop:{pop_id}")
                if pop_data:
                    try:
                        # Your Redis has: pop_id, name, location, operator, routers[]
                        pops[pop_id] = PopNode(
                            pop_id=pop_id,
                            name=pop_data.get('name', pop_id),
                            location=pop_data.get('location'),
                            router_ids=json.loads(pop_data.get('routers', '[]')),
                            interfaces=[]
                        )
                    except json.JSONDecodeError:
                        pops[pop_id] = PopNode(
                            pop_id=pop_id,
                            name=pop_data.get('name', pop_id),
                            location=pop_data.get('location'),
                            router_ids=[],
                            interfaces=[]
                        )
            
            # Get all links - your Redis has: link_id, pop_a, pop_b, distance_km, total_channels
            link_keys = self._client.smembers("links") or []
            for link_id in link_keys:
                link_data = self._client.hgetall(f"link:{link_id}")
                if link_data:
                    try:
                        # Parse frequency slots (occupied slots)
                        frequency_json = link_data.get('frequency_slots', '{}')
                        occupied_slots = json.loads(frequency_json)
                    except:
                        occupied_slots = {}
                    
                    # Convert from channels to slots
                    # Your Redis has total_channels (e.g., 96), we need slots (320)
                    total_channels = int(link_data.get('total_channels', 96))
                    channel_spacing = float(link_data.get('channel_spacing', 50.0))
                    
                    # Convert: assuming 12.5 GHz slots, 50 GHz channels = 4 slots per channel
                    total_slots = total_channels * 4
                    
                    # Calculate available slots
                    slot_key = f"slots:{link_id}"
                    available_slots = []
                    if self._client.exists(slot_key):
                        available_slots = [int(s) for s in self._client.smembers(slot_key)]
                    else:
                        # If no slot data, create slots 0-319
                        available_slots = list(range(320))
                    
                    # Get source and destination - your Redis uses pop_a and pop_b
                    source_pop = link_data.get('pop_a', '')
                    destination_pop = link_data.get('pop_b', '')
                    
                    if source_pop and destination_pop:
                        links[link_id] = NetworkLink(
                            link_id=link_id,
                            source_pop=source_pop,
                            destination_pop=destination_pop,
                            length_km=float(link_data.get('distance_km', 0)),
                            available_slots=available_slots,
                            total_slots=total_slots,
                            occupied_slots=occupied_slots
                        )
            
            logger.info(f"Retrieved topology: {len(pops)} POPs, {len(links)} links")
            return pops, links
            
        except Exception as e:
            logger.error(f"Failed to get topology: {e}")
            return {}, {}
    
    def get_available_interfaces(self, pop_id: str, router_id: str) -> List[str]:
        """
        Get available interfaces for a specific POP/router.
        """
        try:
            available_interfaces = []
            
            # Get all interfaces for this POP/router
            interface_pattern = f"interface:{pop_id}:{router_id}:*"
            interface_keys = self._client.keys(interface_pattern)
            
            for key in interface_keys:
                interface_data = self._client.hgetall(key)
                status = interface_data.get('status', 'UNKNOWN')
                current_conn = interface_data.get('current_connection')
                
                if status == 'AVAILABLE' and (not current_conn or current_conn == 'null'):
                    interface_name = key.split(':')[-1]
                    available_interfaces.append(interface_name)
            
            logger.debug(f"Found {len(available_interfaces)} available interfaces for {pop_id}/{router_id}")
            return available_interfaces
            
        except Exception as e:
            logger.error(f"Failed to get available interfaces: {e}")
            return []
    
    def allocate_interface(self, pop_id: str, router_id: str, interface: str, 
                          connection_id: str) -> bool:
        """Allocate an interface to a connection."""
        try:
            interface_key = f"interface:{pop_id}:{router_id}:{interface}"
            
            if not self._client.exists(interface_key):
                logger.warning(f"Interface {interface_key} does not exist")
                return False
            
            current_status = self._client.hget(interface_key, 'status')
            current_conn = self._client.hget(interface_key, 'current_connection')
            
            if current_status != 'AVAILABLE' or (current_conn and current_conn != 'null'):
                logger.warning(f"Interface {interface_key} is not available")
                return False
            
            update_data = {
                'status': 'OCCUPIED',
                'current_connection': connection_id,
                'allocated_at': datetime.utcnow().isoformat()
            }
            self._client.hset(interface_key, mapping=update_data)
            
            logger.info(f"Allocated interface {interface_key} to connection {connection_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to allocate interface: {e}")
            return False
    
    def release_interface(self, pop_id: str, router_id: str, interface: str) -> bool:
        """Release an interface from a connection."""
        try:
            interface_key = f"interface:{pop_id}:{router_id}:{interface}"
            
            if not self._client.exists(interface_key):
                logger.warning(f"Interface {interface_key} does not exist")
                return False
            
            update_data = {
                'status': 'AVAILABLE',
                'current_connection': 'null',
                'released_at': datetime.utcnow().isoformat()
            }
            self._client.hset(interface_key, mapping=update_data)
            
            logger.info(f"Released interface {interface_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to release interface: {e}")
            return False
    
    def allocate_spectrum_slots(self, link_id: str, connection_id: str, 
                               slots: List[int]) -> bool:
        """Allocate spectrum slots on a link for a connection."""
        try:
            link_key = f"link:{link_id}"
            
            if not self._client.exists(link_key):
                logger.warning(f"Link {link_id} does not exist")
                return False
            
            occupied_json = self._client.hget(link_key, 'occupied_slots') or '{}'
            occupied_slots = json.loads(occupied_json)
            
            slot_key = f"slots:{link_id}"
            available_slots_set = set(self._client.smembers(slot_key) or [])
            
            for slot in slots:
                slot_str = str(slot)
                if slot_str not in available_slots_set:
                    logger.warning(f"Slot {slot} on link {link_id} is not available")
                    return False
            
            occupied_slots[connection_id] = slots
            self._client.hset(link_key, 'occupied_slots', json.dumps(occupied_slots))
            
            for slot in slots:
                self._client.srem(slot_key, slot)
            
            logger.info(f"Allocated slots {slots} on link {link_id} to connection {connection_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to allocate spectrum slots: {e}")
            return False
    
    def release_spectrum_slots(self, link_id: str, connection_id: str) -> bool:
        """Release spectrum slots on a link for a connection."""
        try:
            link_key = f"link:{link_id}"
            
            if not self._client.exists(link_key):
                logger.warning(f"Link {link_id} does not exist")
                return False
            
            occupied_json = self._client.hget(link_key, 'occupied_slots') or '{}'
            occupied_slots = json.loads(occupied_json)
            
            if connection_id not in occupied_slots:
                logger.warning(f"Connection {connection_id} has no slots on link {link_id}")
                return True
            
            slots_to_release = occupied_slots.pop(connection_id)
            self._client.hset(link_key, 'occupied_slots', json.dumps(occupied_slots))
            
            slot_key = f"slots:{link_id}"
            for slot in slots_to_release:
                self._client.sadd(slot_key, slot)
            
            logger.info(f"Released slots {slots_to_release} on link {link_id} from connection {connection_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to release spectrum slots: {e}")
            return False
    
    def get_available_slots(self, link_id: str) -> List[int]:
        """Get available spectrum slots on a link."""
        try:
            slot_key = f"slots:{link_id}"
            
            if self._client.exists(slot_key):
                available_slots = [int(s) for s in self._client.smembers(slot_key)]
            else:
                available_slots = list(range(320))
            
            return sorted(available_slots)
            
        except Exception as e:
            logger.error(f"Failed to get available slots for link {link_id}: {e}")
            return []
    
    def create_connection_record(self, connection_id: str, 
                                connection_data: Dict[str, Any]) -> bool:
        """Create a connection record in Link Database."""
        try:
            conn_key = f"connection:{connection_id}"
            
            connection_data['created_at'] = datetime.utcnow().isoformat()
            connection_data['updated_at'] = connection_data['created_at']
            
            self._client.hset(conn_key, mapping=connection_data)
            self._client.sadd('connections', connection_id)
            
            logger.info(f"Created connection record: {connection_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create connection record: {e}")
            return False
    
    def update_connection_status(self, connection_id: str, 
                                status: str, details: Optional[Dict] = None) -> bool:
        """Update connection status in Link Database."""
        try:
            conn_key = f"connection:{connection_id}"
            
            if not self._client.exists(conn_key):
                logger.warning(f"Connection {connection_id} does not exist")
                return False
            
            update_data = {
                'status': status,
                'updated_at': datetime.utcnow().isoformat()
            }
            
            if details:
                current_details = self._client.hget(conn_key, 'details') or '{}'
                try:
                    current_details_dict = json.loads(current_details)
                except:
                    current_details_dict = {}
                
                current_details_dict.update(details)
                update_data['details'] = json.dumps(current_details_dict)
            
            self._client.hset(conn_key, mapping=update_data)
            
            logger.debug(f"Updated connection {connection_id} status to {status}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update connection status: {e}")
            return False
    
    def delete_connection_record(self, connection_id: str) -> bool:
        """Delete connection record from Link Database."""
        try:
            conn_key = f"connection:{connection_id}"
            
            if not self._client.exists(conn_key):
                logger.warning(f"Connection {connection_id} does not exist")
                return True
            
            self._client.delete(conn_key)
            self._client.srem('connections', connection_id)
            
            logger.info(f"Deleted connection record: {connection_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete connection record: {e}")
            return False
    
    def health_check(self) -> bool:
        """Check Link Database health."""
        try:
            return self._client.ping()
        except Exception:
            return False
    
    def get_connection_count(self) -> int:
        """Get total number of connections."""
        try:
            return self._client.scard('connections') or 0
        except Exception:
            return 0
    
    def close(self) -> None:
        """Close the database connection."""
        if self._client:
            self._client.close()
            logger.info("Link Database connection closed")
    
    def __del__(self):
        """Destructor to ensure proper cleanup."""
        self.close()
