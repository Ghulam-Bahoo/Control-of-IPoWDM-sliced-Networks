"""
Module: Link Database Client
Description: Interface to Link Database for resource tracking
Author: AI Developer
Date: 2024
"""

import logging
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
import redis  # Assuming Redis as Link DB per paper architecture

from config.settings import settings


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
    
    def initialize_vop(self, vop_id: str, tenant_name: str, 
                      interface_assignments: List[Dict]) -> bool:
        """
        Initialize virtual operator in Link Database.
        
        Args:
            vop_id: Virtual operator ID
            tenant_name: Tenant/operator name
            interface_assignments: List of interface assignments
            
        Returns:
            True if initialization successful
            
        Raises:
            ValueError: If vOp already exists
        """
        try:
            # Check if vOp already exists
            if self._client.exists(f"vop:{vop_id}"):
                raise ValueError(f"Virtual operator {vop_id} already exists in Link DB")
            
            # Create vOp entry
            vop_data = {
                'vop_id': vop_id,
                'tenant_name': tenant_name,
                'status': 'ACTIVE',
                'created_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat(),
                'interface_assignments': json.dumps(interface_assignments)
            }
            
            # Store vOp metadata
            self._client.hset(f"vop:{vop_id}", mapping=vop_data)
            
            # Initialize topology entries for each interface
            for assignment in interface_assignments:
                pop_id = assignment['pop_id']
                router_id = assignment['router_id']
                
                for interface in assignment['interfaces']:
                    # Create interface entry
                    interface_key = f"interface:{pop_id}:{router_id}:{interface}"
                    interface_data = {
                        'vop_id': vop_id,
                        'pop_id': pop_id,
                        'router_id': router_id,
                        'interface_name': interface,
                        'status': 'AVAILABLE',
                        'assigned_at': datetime.utcnow().isoformat(),
                        'current_connection': 'null'
                    }
                    self._client.hset(interface_key, mapping=interface_data)
                    
                    # Add to vOp's interface list
                    self._client.sadd(f"vop:{vop_id}:interfaces", interface_key)
            
            # Add to active vOps set
            self._client.sadd('active_vops', vop_id)
            
            logger.info(f"Initialized {vop_id} in Link Database with {len(interface_assignments)} assignments")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize {vop_id} in Link DB: {e}")
            raise
    
    def get_vop_info(self, vop_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a virtual operator."""
        try:
            data = self._client.hgetall(f"vop:{vop_id}")
            if not data:
                return None
            
            # Parse interface assignments
            if 'interface_assignments' in data:
                data['interface_assignments'] = json.loads(data['interface_assignments'])
            
            return data
        except Exception as e:
            logger.error(f"Failed to get info for {vop_id}: {e}")
            return None
    
    def check_interface_availability(self, pop_id: str, router_id: str, 
                                   interface_name: str) -> bool:
        """
        Check if an interface is available for assignment.
        
        Args:
            pop_id: POP identifier
            router_id: Router identifier
            interface_name: Interface name
            
        Returns:
            True if interface is available
        """
        try:
            interface_key = f"interface:{pop_id}:{router_id}:{interface_name}"
            
            # Check if interface exists
            if not self._client.exists(interface_key):
                return True  # Interface not in DB, considered available
            
            # Check current status
            status = self._client.hget(interface_key, 'status')
            return status == 'AVAILABLE'
            
        except Exception as e:
            logger.error(f"Failed to check interface availability: {e}")
            return False
    
    def get_all_vops(self) -> List[str]:
        """Get list of all active virtual operators."""
        try:
            return list(self._client.smembers('active_vops'))
        except Exception as e:
            logger.error(f"Failed to get active vOps: {e}")
            return []
    
    def deactivate_vop(self, vop_id: str) -> bool:
        """Deactivate a virtual operator and release resources."""
        try:
            # Get all interfaces assigned to this vOp
            interface_keys = self._client.smembers(f"vop:{vop_id}:interfaces")
            
            # Release all interfaces
            for interface_key in interface_keys:
                self._client.hset(interface_key, 'status', 'AVAILABLE')
                self._client.hset(interface_key, 'vop_id', 'null')
                self._client.hset(interface_key, 'current_connection', 'null')
            
            # Update vOp status
            self._client.hset(f"vop:{vop_id}", 'status', 'INACTIVE')
            self._client.hset(f"vop:{vop_id}", 'updated_at', datetime.utcnow().isoformat())
            
            # Remove from active vOps set
            self._client.srem('active_vops', vop_id)
            
            logger.info(f"Deactivated {vop_id} in Link Database")
            return True
            
        except Exception as e:
            logger.error(f"Failed to deactivate {vop_id}: {e}")
            return False
    
    def health_check(self) -> bool:
        """Check Link Database health."""
        try:
            return self._client.ping()
        except Exception:
            return False
    
    def close(self) -> None:
        """Close the database connection."""
        if self._client:
            self._client.close()
    
    def __del__(self):
        """Destructor to ensure proper cleanup."""
        self.close()


# Singleton instance
linkdb_client = LinkDBClient()
