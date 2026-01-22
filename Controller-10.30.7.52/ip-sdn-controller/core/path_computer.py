"""
Path Computation Engine for IP SDN Controller
Implements Dijkstra algorithm with spectrum slot allocation
"""

import logging
import heapq
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict

from config.settings import settings
from models.schemas import NetworkLink, PathSegment
from core.linkdb_client import LinkDBClient


logger = logging.getLogger(__name__)


class PathComputer:
    """
    Path computation engine with spectrum allocation.
    Implements the routing and spectrum assignment (RSA) algorithm.
    """
    
    def __init__(self, linkdb_client: LinkDBClient):
        self.linkdb = linkdb_client
        self.topology = None
        self.links = None
        self._build_graph()
    
    def _build_graph(self) -> None:
        """Build graph representation from topology."""
        try:
            self.topology, self.links = self.linkdb.get_topology()
            logger.info(f"Built graph with {len(self.topology)} nodes and {len(self.links)} edges")
        except Exception as e:
            logger.error(f"Failed to build graph: {e}")
            self.topology = {}
            self.links = {}
    
    def compute_shortest_path(self, source_pop: str, destination_pop: str) -> Optional[List[str]]:
        """
        Compute shortest path using Dijkstra's algorithm.
        
        Args:
            source_pop: Source POP ID
            destination_pop: Destination POP ID
            
        Returns:
            List of link IDs representing the path, or None if no path found
        """
        if not self.topology or not self.links:
            logger.error("Topology not loaded")
            return None
        
        if source_pop not in self.topology:
            logger.error(f"Source POP {source_pop} not found in topology")
            return None
        
        if destination_pop not in self.topology:
            logger.error(f"Destination POP {destination_pop} not found in topology")
            return None
        
        # Build adjacency list: pop -> list of (neighbor_pop, link_id, weight)
        adjacency = defaultdict(list)
        for link_id, link in self.links.items():
            adjacency[link.source_pop].append((link.destination_pop, link_id, link.length_km))
            adjacency[link.destination_pop].append((link.source_pop, link_id, link.length_km))
        
        # Dijkstra's algorithm
        distances = {pop: float('inf') for pop in self.topology}
        distances[source_pop] = 0
        previous = {pop: None for pop in self.topology}
        previous_link = {pop: None for pop in self.topology}
        
        # Priority queue: (distance, pop)
        pq = [(0, source_pop)]
        
        while pq:
            current_dist, current_pop = heapq.heappop(pq)
            
            # Early termination if we reached destination
            if current_pop == destination_pop:
                break
            
            # Skip if we found a better path already
            if current_dist > distances[current_pop]:
                continue
            
            # Explore neighbors
            for neighbor_pop, link_id, weight in adjacency[current_pop]:
                new_dist = current_dist + weight
                
                if new_dist < distances[neighbor_pop]:
                    distances[neighbor_pop] = new_dist
                    previous[neighbor_pop] = current_pop
                    previous_link[neighbor_pop] = link_id
                    heapq.heappush(pq, (new_dist, neighbor_pop))
        
        # Reconstruct path if destination is reachable
        if distances[destination_pop] == float('inf'):
            logger.warning(f"No path found from {source_pop} to {destination_pop}")
            return None
        
        # Reconstruct path in reverse
        path_links = []
        current = destination_pop
        
        while previous[current] is not None:
            link_id = previous_link[current]
            if link_id:
                path_links.append(link_id)
            current = previous[current]
        
        # Reverse to get source->destination order
        path_links.reverse()
        
        logger.info(f"Computed path from {source_pop} to {destination_pop}: {path_links}")
        return path_links
    
    def find_contiguous_slots(self, link_id: str, required_slots: int) -> Optional[List[int]]:
        """
        Find contiguous spectrum slots on a link.
        
        Args:
            link_id: Link identifier
            required_slots: Number of contiguous slots needed
            
        Returns:
            List of slot indices if found, None otherwise
        """
        try:
            available_slots = self.linkdb.get_available_slots(link_id)
            
            if not available_slots:
                logger.warning(f"No available slots on link {link_id}")
                return None
            
            # Find contiguous slots
            for i in range(len(available_slots) - required_slots + 1):
                current_slice = available_slots[i:i + required_slots]
                
                # Check if slots are contiguous
                is_contiguous = all(
                    current_slice[j] + 1 == current_slice[j + 1]
                    for j in range(required_slots - 1)
                )
                
                if is_contiguous:
                    logger.debug(f"Found contiguous slots on {link_id}: {current_slice}")
                    return current_slice
            
            logger.warning(f"No contiguous {required_slots} slots found on link {link_id}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to find contiguous slots on link {link_id}: {e}")
            return None
    
    def allocate_path_slots(self, path_links: List[str], required_slots: int) -> Dict[str, List[int]]:
        """
        Allocate spectrum slots along a path using First-Fit algorithm.
        
        Args:
            path_links: List of link IDs in the path
            required_slots: Number of slots needed
            
        Returns:
            Dictionary mapping link_id -> allocated slots
        """
        if not path_links:
            return {}
        
        # First-Fit: try same slots on all links
        link_allocations = {}
        
        # Get available slots for first link
        first_link = path_links[0]
        available_on_first = self.linkdb.get_available_slots(first_link)
        
        if not available_on_first:
            logger.error(f"No available slots on first link {first_link}")
            return {}
        
        # Try to find slots that are available on all links
        for i in range(len(available_on_first) - required_slots + 1):
            candidate_slots = available_on_first[i:i + required_slots]
            
            # Check contiguity
            is_contiguous = all(
                candidate_slots[j] + 1 == candidate_slots[j + 1]
                for j in range(required_slots - 1)
            )
            
            if not is_contiguous:
                continue
            
            # Check if these slots are available on all links
            available_on_all = True
            for link_id in path_links[1:]:
                available_on_link = set(self.linkdb.get_available_slots(link_id))
                if not all(slot in available_on_link for slot in candidate_slots):
                    available_on_all = False
                    break
            
            if available_on_all:
                # Allocate these slots on all links
                for link_id in path_links:
                    link_allocations[link_id] = candidate_slots
                break
        
        if link_allocations:
            logger.info(f"Allocated slots {list(link_allocations.values())[0]} on path")
            return link_allocations
        
        # If no common slots found, try different slots per link (more complex)
        logger.warning("No common slots found on all links, trying per-link allocation")
        return self._allocate_per_link_slots(path_links, required_slots)
    
    def _allocate_per_link_slots(self, path_links: List[str], required_slots: int) -> Dict[str, List[int]]:
        """
        Allocate possibly different slots on each link (spectrum conversion).
        This is a simplified implementation - real systems might use regenerators.
        
        Args:
            path_links: List of link IDs
            required_slots: Number of slots needed
            
        Returns:
            Dictionary mapping link_id -> allocated slots
        """
        link_allocations = {}
        
        for link_id in path_links:
            slots = self.find_contiguous_slots(link_id, required_slots)
            if not slots:
                logger.error(f"Failed to allocate slots on link {link_id}")
                # Rollback previous allocations
                for allocated_link, allocated_slots in link_allocations.items():
                    # In practice, we'd release these slots
                    pass
                return {}
            
            link_allocations[link_id] = slots
        
        logger.info(f"Allocated per-link slots on path")
        return link_allocations
    
    def calculate_required_slots(self, bandwidth_gbps: float, 
                                modulation: str = "DP-16QAM") -> int:
        """
        Calculate number of spectrum slots required based on bandwidth and modulation.
        
        Args:
            bandwidth_gbps: Required bandwidth in Gbps
            modulation: Modulation format
            
        Returns:
            Number of 12.5 GHz slots required
        """
        # Based on paper: 400G ZR uses 75 GHz = 6 slots of 12.5 GHz
        # Simplified calculation
        slot_width_ghz = settings.SLOT_WIDTH_GHZ
        
        # Spectral efficiency table (bits/s/Hz)
        spectral_efficiency = {
            "DP-QPSK": 2.0,    # 2 bits/s/Hz
            "DP-8QAM": 3.0,    # 3 bits/s/Hz
            "DP-16QAM": 4.0,   # 4 bits/s/Hz (400G ZR)
        }
        
        eff = spectral_efficiency.get(modulation, 4.0)
        
        # Required spectrum in GHz = bandwidth / spectral efficiency
        required_ghz = bandwidth_gbps / eff
        
        # Convert to slots (ceil division)
        required_slots = int(-(-required_ghz // slot_width_ghz))
        
        # Minimum 1 slot
        required_slots = max(1, required_slots)
        
        logger.debug(f"Bandwidth {bandwidth_gbps}Gbps with {modulation} requires {required_slots} slots")
        return required_slots
    
    def estimate_path_osnr(self, path_links: List[str]) -> Optional[float]:
        """
        Estimate OSNR for a path based on link lengths.
        Simplified model for demonstration.
        
        Args:
            path_links: List of link IDs in the path
            
        Returns:
            Estimated OSNR in dB
        """
        try:
            if not path_links:
                return None
            
            total_length = 0
            for link_id in path_links:
                if link_id in self.links:
                    total_length += self.links[link_id].length_km
            
            # Simplified OSNR model: OSNR ∝ 1/length
            # Assuming 25 dB for 100 km, scaling inversely
            if total_length > 0:
                estimated_osnr = 25 * (100 / total_length)
                return round(estimated_osnr, 2)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to estimate path OSNR: {e}")
            return None
    
    def validate_path(self, source_pop: str, destination_pop: str, 
                     source_interface: Optional[str] = None,
                     destination_interface: Optional[str] = None) -> Tuple[bool, str]:
        """
        Validate if a path can be established.
        
        Args:
            source_pop: Source POP ID
            destination_pop: Destination POP ID
            source_interface: Optional source interface
            destination_interface: Optional destination interface
            
        Returns:
            Tuple of (is_valid, message)
        """
        # Check if POPs exist
        if source_pop not in self.topology:
            return False, f"Source POP {source_pop} not found"
        
        if destination_pop not in self.topology:
            return False, f"Destination POP {destination_pop} not found"
        
        # Check source interface availability
        if source_interface:
            # Get routers for source POP
            source_routers = self.topology[source_pop].router_ids
            interface_found = False
            for router_id in source_routers:
                available = self.linkdb.get_available_interfaces(source_pop, router_id)
                if source_interface in available:
                    interface_found = True
                    break
            
            if not interface_found:
                return False, f"Source interface {source_interface} not available"
        
        # Check destination interface availability
        if destination_interface:
            # Get routers for destination POP
            dest_routers = self.topology[destination_pop].router_ids
            interface_found = False
            for router_id in dest_routers:
                available = self.linkdb.get_available_interfaces(destination_pop, router_id)
                if destination_interface in available:
                    interface_found = True
                    break
            
            if not interface_found:
                return False, f"Destination interface {destination_interface} not available"
        
        # Compute path
        path = self.compute_shortest_path(source_pop, destination_pop)
        if not path:
            return False, f"No path found from {source_pop} to {destination_pop}"
        
        return True, f"Valid path found with {len(path)} hops"
    
    def compute_complete_path(self, source_pop: str, destination_pop: str,
                            bandwidth_gbps: float, modulation: str) -> Tuple[Optional[List[PathSegment]], Optional[str]]:
        """
        Complete path computation with spectrum allocation.
        
        Args:
            source_pop: Source POP ID
            destination_pop: Destination POP ID
            bandwidth_gbps: Required bandwidth
            modulation: Modulation format
            
        Returns:
            Tuple of (path_segments, error_message)
        """
        try:
            # 1. Compute shortest path
            path_links = self.compute_shortest_path(source_pop, destination_pop)
            if not path_links:
                return None, "No path found"
            
            # 2. Calculate required slots
            required_slots = self.calculate_required_slots(bandwidth_gbps, modulation)
            
            # 3. Allocate spectrum slots
            slot_allocations = self.allocate_path_slots(path_links, required_slots)
            if not slot_allocations:
                return None, "Failed to allocate spectrum slots"
            
            # 4. Create path segments
            path_segments = []
            for link_id in path_links:
                if link_id in self.links:
                    link = self.links[link_id]
                    path_segments.append(PathSegment(
                        link_id=link_id,
                        source_pop=link.source_pop,
                        destination_pop=link.destination_pop,
                        allocated_slots=slot_allocations.get(link_id, []),
                        slot_width_ghz=settings.SLOT_WIDTH_GHZ
                    ))
            
            # 5. Estimate OSNR
            estimated_osnr = self.estimate_path_osnr(path_links)
            
            logger.info(f"Computed complete path: {len(path_segments)} segments, "
                       f"{required_slots} slots each, estimated OSNR: {estimated_osnr} dB")
            
            return path_segments, None
            
        except Exception as e:
            logger.error(f"Failed to compute complete path: {e}")
            return None, str(e)
