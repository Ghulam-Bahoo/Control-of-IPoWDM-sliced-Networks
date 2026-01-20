#!/usr/bin/env python3
"""
Hardware discovery script for SONiC Agent
Discovers available interfaces and their port mappings dynamically.
"""

import os
import sys
import json
import subprocess
from typing import Dict, List, Tuple
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def discover_sonic_interfaces() -> Dict[str, List[str]]:
    """Discover interfaces on SONiC switch using sonic-cfggen."""
    try:
        # Use sonic-cfggen to get all interfaces
        result = subprocess.run(
            ['sonic-cfggen', '-d', '--print-data'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            print(f"Error running sonic-cfggen: {result.stderr}")
            return {}
        
        # Parse JSON output
        data = json.loads(result.stdout)
        
        # Extract interfaces from PORT table
        ports = data.get('PORT', {})
        interfaces = list(ports.keys())
        
        # Filter for Ethernet interfaces (could also include PortChannel, etc.)
        ethernet_interfaces = [iface for iface in interfaces if iface.startswith('Ethernet')]
        
        return {
            'all_interfaces': interfaces,
            'ethernet_interfaces': ethernet_interfaces,
            'port_details': ports
        }
        
    except Exception as e:
        print(f"Failed to discover interfaces: {e}")
        return {}


def discover_sfp_presence() -> Dict[str, bool]:
    """Discover which interfaces have SFPs present."""
    presence = {}
    
    try:
        # Run show interfaces transceiver presence
        result = subprocess.run(
            ['show', 'interfaces', 'transceiver', 'presence'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if 'Ethernet' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        iface = parts[0]
                        present = parts[1].lower() == 'present'
                        presence[iface] = present
        
    except Exception as e:
        print(f"Failed to check SFP presence: {e}")
    
    return presence


def discover_interface_capabilities(interface: str) -> Dict:
    """Discover capabilities of a specific interface."""
    try:
        # Run show interfaces transceiver eeprom
        result = subprocess.run(
            ['show', 'interfaces', 'transceiver', 'eeprom', interface],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            # Parse vendor, part number, etc.
            output = result.stdout
            capabilities = {
                'interface': interface,
                'has_eeprom': True
            }
            
            # Extract basic info
            for line in output.split('\n'):
                if 'Vendor Name' in line:
                    capabilities['vendor'] = line.split(':')[-1].strip()
                elif 'Vendor PN' in line:
                    capabilities['part_number'] = line.split(':')[-1].strip()
                elif 'Vendor SN' in line:
                    capabilities['serial'] = line.split(':')[-1].strip()
            
            return capabilities
        
    except Exception:
        pass
    
    return {'interface': interface, 'has_eeprom': False}


def get_assigned_interfaces_from_linkdb(linkdb_url: str, vop_id: str, pop_id: str, router_id: str) -> List[str]:
    """Get assigned interfaces from Link Database API."""
    import requests
    
    try:
        # Query LinkDB for interfaces assigned to this vOp
        response = requests.get(
            f"{linkdb_url}/api/vops/{vop_id}/interfaces",
            params={'pop_id': pop_id, 'router_id': router_id},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get('interfaces', [])
            
    except Exception as e:
        print(f"Failed to query LinkDB: {e}")
    
    return []


def create_port_mappings(interfaces: List[str]) -> Dict[str, int]:
    """Create interface name to port number mappings."""
    mappings = {}
    
    for interface in interfaces:
        # Extract port number from interface name
        # Ethernet192 -> 192
        if interface.startswith('Ethernet'):
            try:
                port_num = int(interface[8:])  # Remove "Ethernet" prefix
                mappings[interface] = port_num
            except ValueError:
                # If not a simple number, try to find mapping
                pass
    
    return mappings


def main():
    """Main discovery routine."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Discover SONiC hardware')
    parser.add_argument('--linkdb-url', default='http://localhost:8000', help='Link Database URL')
    parser.add_argument('--vop-id', help='Virtual operator ID')
    parser.add_argument('--pop-id', help='POP ID')
    parser.add_argument('--router-id', help='Router ID')
    parser.add_argument('--output', choices=['json', 'env'], default='env', help='Output format')
    
    args = parser.parse_args()
    
    print("Discovering SONiC hardware...")
    
    # 1. Discover all interfaces on the switch
    discovery = discover_sonic_interfaces()
    ethernet_ifaces = discovery.get('ethernet_interfaces', [])
    
    print(f"Found {len(ethernet_ifaces)} Ethernet interfaces: {ethernet_ifaces}")
    
    # 2. Check which have SFPs present
    sfp_presence = discover_sfp_presence()
    present_interfaces = [iface for iface in ethernet_ifaces if sfp_presence.get(iface, False)]
    
    print(f"Found {len(present_interfaces)} interfaces with SFPs: {present_interfaces}")
    
    # 3. Try to get assigned interfaces from LinkDB
    assigned_interfaces = []
    if args.vop_id and args.pop_id and args.router_id:
        assigned_interfaces = get_assigned_interfaces_from_linkdb(
            args.linkdb_url, args.vop_id, args.pop_id, args.router_id
        )
        print(f"LinkDB assigned {len(assigned_interfaces)} interfaces: {assigned_interfaces}")
    
    # 4. Determine final interface list
    if assigned_interfaces:
        # Use interfaces assigned by LinkDB
        final_interfaces = assigned_interfaces
    elif present_interfaces:
        # Use all interfaces with SFPs present
        final_interfaces = present_interfaces
    else:
        # Fallback to all Ethernet interfaces
        final_interfaces = ethernet_ifaces[:2]  # Use first 2 as default
    
    # 5. Create port mappings
    port_mappings = create_port_mappings(final_interfaces)
    
    # 6. Output results
    if args.output == 'json':
        result = {
            'assigned_transceivers': final_interfaces,
            'interface_mappings': port_mappings,
            'discovery_details': {
                'all_interfaces': discovery.get('all_interfaces', []),
                'sfp_present': sfp_presence,
                'ethernet_interfaces': ethernet_ifaces
            }
        }
        print(json.dumps(result, indent=2))
    
    else:  # env format, emit compact JSON (no spaces) so bash 'source' is safe
        print(f"ASSIGNED_TRANSCEIVERS={json.dumps(final_interfaces, separators=(',', ':'))}")
        print(f"IFNAME_TO_PORTNUM_JSON={json.dumps(port_mappings, separators=(',', ':'))}")
        
        for interface in final_interfaces:
            caps = discover_interface_capabilities(interface)
            print(
                f"# {interface}: vendor={caps.get('vendor', 'unknown')}, "
                f"part={caps.get('part_number', 'unknown')}"
            )


if __name__ == "__main__":
    sys.exit(main())
