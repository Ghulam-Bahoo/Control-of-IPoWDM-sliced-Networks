# link_database/first_fit.py
# First-Fit Frequency Allocation Algorithm

import logging
from typing import List, Dict, Optional, Any
from datetime import datetime

logger = logging.getLogger("first-fit-allocator")

class FirstFitAllocator:
    """First-fit frequency allocation algorithm for optical networks"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.channel_spacing = 50  # MHz
        self.start_frequency = 191300  # MHz
        self.end_frequency = 196100    # MHz
        
    async def allocate_frequency(self, link_id: str, connection_id: str, 
                               virtual_operator: str, bandwidth: int) -> Optional[Dict]:
        """
        Allocate frequency using first-fit algorithm.
        
        Args:
            link_id: Optical link ID
            connection_id: Connection identifier
            virtual_operator: Virtual operator (vOp1, vOp2)
            bandwidth: Required bandwidth in Gbps
            
        Returns:
            Dictionary with allocated frequency and available slots, or None
        """
        logger.info(f"Allocating frequency for {connection_id} on link {link_id}")
        
        # Get all frequency slots for this link
        slot_freqs = await self.redis.smembers(f"slots:{link_id}")
        if not slot_freqs:
            logger.error(f"No frequency slots found for link {link_id}")
            return None
        
        # Sort frequencies
        sorted_freqs = sorted([int(freq) for freq in slot_freqs])
        
        # Calculate required slots based on bandwidth
        required_slots = self._calculate_required_slots(bandwidth)
        logger.debug(f"Bandwidth {bandwidth}G requires {required_slots} slot(s)")
        
        # Find first available contiguous block
        allocated_frequency = await self._find_first_fit(
            link_id, sorted_freqs, required_slots, connection_id, virtual_operator
        )
        
        if not allocated_frequency:
            logger.warning(f"No available frequencies for {connection_id}")
            return None
        
        # Get list of all available slots (for reporting)
        available_slots = await self._get_available_slots(link_id, sorted_freqs)
        
        logger.info(f"Allocated frequency {allocated_frequency}MHz for {connection_id}")
        
        return {
            "frequency": allocated_frequency,
            "required_slots": required_slots,
            "available_slots": available_slots,
            "link_id": link_id,
            "virtual_operator": virtual_operator
        }
    
    async def _find_first_fit(self, link_id: str, sorted_freqs: List[int], 
                            required_slots: int, connection_id: str, 
                            virtual_operator: str) -> Optional[int]:
        """
        Find first available contiguous frequency block.
        """
        for i in range(len(sorted_freqs) - required_slots + 1):
            # Check if this block is available
            block_available = True
            block_frequencies = []
            
            for j in range(required_slots):
                freq = sorted_freqs[i + j]
                slot_key = f"slot:{link_id}:{freq}"
                slot_data = await self.redis.hgetall(slot_key)
                
                if not slot_data or slot_data.get("status") != "available":
                    block_available = False
                    break
                
                block_frequencies.append(freq)
            
            if block_available:
                # Allocate the first frequency in the block
                allocated_freq = block_frequencies[0]
                
                # Mark all slots in block as occupied
                for freq in block_frequencies:
                    slot_key = f"slot:{link_id}:{freq}"
                    slot_data = await self.redis.hgetall(slot_key)
                    if slot_data:
                        slot_data["status"] = "occupied"
                        slot_data["occupied_by"] = connection_id
                        slot_data["virtual_operator"] = virtual_operator
                        slot_data["occupied_since"] = datetime.utcnow().isoformat()
                        slot_data["updated_at"] = datetime.utcnow().isoformat()
                        await self.redis.hset(slot_key, mapping=slot_data)
                
                return allocated_freq
        
        return None
    
    def _calculate_required_slots(self, bandwidth: int) -> int:
        """
        Calculate required frequency slots based on bandwidth.
        
        Simplified model:
        - 400G: 4 slots (or 1 slot for higher order modulation)
        - 100G: 1 slot
        """
        if bandwidth >= 400:
            return 1  # Using 16QAM/64QAM
        elif bandwidth >= 100:
            return 1
        else:
            return 1
    
    async def _get_available_slots(self, link_id: str, sorted_freqs: List[int]) -> List[int]:
        """Get all available frequency slots for a link"""
        available = []
        
        for freq in sorted_freqs:
            slot_key = f"slot:{link_id}:{freq}"
            slot_data = await self.redis.hgetall(slot_key)
            
            if slot_data and slot_data.get("status") == "available":
                available.append(freq)
        
        return available
    
    async def release_frequency(self, link_id: str, frequency: int):
        """Release allocated frequency"""
        slot_key = f"slot:{link_id}:{frequency}"
        slot_data = await self.redis.hgetall(slot_key)
        
        if slot_data:
            slot_data["status"] = "available"
            slot_data["occupied_by"] = None
            slot_data["virtual_operator"] = None
            slot_data["occupied_since"] = None
            slot_data["updated_at"] = datetime.utcnow().isoformat()
            
            await self.redis.hset(slot_key, mapping=slot_data)
            logger.info(f"Released frequency {frequency}MHz on link {link_id}")
            
            return True
        
        return False
    
    async def get_link_utilization(self, link_id: str) -> Dict:
        """Get utilization statistics for a link"""
        slot_freqs = await self.redis.smembers(f"slots:{link_id}")
        if not slot_freqs:
            return {"error": "Link not found"}
        
        total = len(slot_freqs)
        occupied = 0
        
        for freq in slot_freqs:
            slot_key = f"slot:{link_id}:{freq}"
            slot_data = await self.redis.hgetall(slot_key)
            
            if slot_data and slot_data.get("status") == "occupied":
                occupied += 1
        
        utilization = (occupied / total) * 100 if total > 0 else 0
        
        return {
            "link_id": link_id,
            "total_slots": total,
            "occupied_slots": occupied,
            "available_slots": total - occupied,
            "utilization_percentage": round(utilization, 2),
            "status": "high" if utilization > 80 else "medium" if utilization > 50 else "low"
        }
