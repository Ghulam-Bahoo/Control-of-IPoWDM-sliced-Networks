#!/usr/bin/env python3
"""
Hardware discovery script for SONiC Agent

- Discovers interfaces and SFP/QSFP presence dynamically.
- Builds a correct interface->port mapping based on SONiC PORT table:
    * Prefer PORT[index], if available
    * Otherwise, parse alias like "Eth16(Port16)" -> 16
    * As last resort, fall back to Ethernet suffix ("Ethernet192" -> 192)
- Emits environment variables for the agent:
    ASSIGNED_TRANSCEIVERS=[...]
    IFNAME_TO_PORTNUM_JSON={...}

CMIS focus:
- Designed for QSFP-DD / QSFP28 / EDFA line/client ports.
- SFP/SFP+ mgmt ports (Ethernet256, Ethernet257) are intentionally excluded.
"""

import sys
import json
import subprocess
from typing import Dict, List
from pathlib import Path
import re

# Add app directory to path if needed
sys.path.insert(0, str(Path(__file__).parent.parent))

# Candidate CMIS/optical ports we care about on this AS9726:
#   - QSFP-DD line ports: Ethernet0, Ethernet160, Ethernet192
#   - EDFA: Ethernet64
#   - QSFP28 client/agg: Ethernet96, Ethernet120, Ethernet128
CANDIDATE_PORTS = [
    "Ethernet0",
    "Ethernet64",
    "Ethernet96",
    "Ethernet120",
    "Ethernet128",
    "Ethernet160",
    "Ethernet192",
    # SFP mgmt ports explicitly NOT included:
    # "Ethernet256",
    # "Ethernet257",
]


def discover_sonic_interfaces() -> Dict[str, Dict]:
    """
    Discover interfaces on SONiC switch using sonic-cfggen.

    Returns a dict:
    {
        "ports": { ifname -> PORT attributes },
        "ethernet_interfaces": [ ... ],
        "panel_index": { ifname -> int panel index }
    }
    """
    try:
        result = subprocess.run(
            ["sonic-cfggen", "-d", "--print-data"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            print(f"Error running sonic-cfggen: {result.stderr}", file=sys.stderr)
            return {"ports": {}, "ethernet_interfaces": [], "panel_index": {}}

        data = json.loads(result.stdout)
        ports = data.get("PORT", {}) or {}

        all_ifaces = list(ports.keys())
        ethernet_ifaces = [i for i in all_ifaces if i.startswith("Ethernet")]

        panel_index: Dict[str, int] = {}

        for ifname, attrs in ports.items():
            idx = None

            # 1) Prefer PORT['index'] if present
            raw_idx = attrs.get("index")
            if raw_idx is not None:
                try:
                    idx = int(raw_idx)
                except (TypeError, ValueError):
                    idx = None

            # 2) Otherwise, parse alias like "Eth16(Port16)"
            if idx is None:
                alias = attrs.get("alias") or ""
                m = re.search(r"Port(\d+)", alias)
                if m:
                    try:
                        idx = int(m.group(1))
                    except ValueError:
                        idx = None

            # 3) Last resort: Ethernet suffix "Ethernet192" -> 192
            if idx is None and ifname.startswith("Ethernet"):
                try:
                    idx = int(ifname[8:])
                except ValueError:
                    idx = None

            if idx is not None:
                panel_index[ifname] = idx

        return {
            "ports": ports,
            "ethernet_interfaces": ethernet_ifaces,
            "panel_index": panel_index,
        }

    except Exception as e:
        print(f"Failed to discover interfaces: {e}", file=sys.stderr)
        return {"ports": {}, "ethernet_interfaces": [], "panel_index": {}}


def discover_sfp_presence() -> Dict[str, bool]:
    """Discover which interfaces have SFPs / QSFPs present (via CLI)."""
    presence: Dict[str, bool] = {}

    try:
        result = subprocess.run(
            ["show", "interfaces", "transceiver", "presence"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if "Ethernet" in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        iface = parts[0]
                        present = parts[1].lower() == "present"
                        presence[iface] = present

    except Exception as e:
        print(f"Failed to check SFP presence: {e}", file=sys.stderr)

    return presence


def discover_interface_capabilities(interface: str) -> Dict:
    """
    Discover basic capabilities of a specific interface via CLI.

    We use this not only for logging, but as an "availability" check:
    - If we can successfully read EEPROM, we treat the transceiver as CMIS-available.
    """
    try:
        result = subprocess.run(
            ["show", "interfaces", "transceiver", "eeprom", interface],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            output = result.stdout
            capabilities = {
                "interface": interface,
                "has_eeprom": True,
            }

            for line in output.split("\n"):
                if "Vendor Name" in line:
                    capabilities["vendor"] = line.split(":")[-1].strip()
                elif "Vendor PN" in line:
                    capabilities["part_number"] = line.split(":")[-1].strip()
                elif "Vendor SN" in line:
                    capabilities["serial"] = line.split(":")[-1].strip()

            return capabilities

    except Exception:
        pass

    return {"interface": interface, "has_eeprom": False}


def get_assigned_interfaces_from_linkdb(
    linkdb_url: str, vop_id: str, pop_id: str, router_id: str
) -> List[str]:
    """Get interfaces assigned to this vOp from Link Database API."""
    import requests

    try:
        response = requests.get(
            f"{linkdb_url}/api/vops/{vop_id}/interfaces",
            params={"pop_id": pop_id, "router_id": router_id},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("interfaces", [])

    except Exception as e:
        print(f"Failed to query LinkDB: {e}", file=sys.stderr)

    return []


def create_port_mappings(interfaces: List[str], panel_index: Dict[str, int]) -> Dict[str, int]:
    """Create interface name to port number mappings using panel_index if possible."""
    mappings: Dict[str, int] = {}

    for interface in interfaces:
        idx = panel_index.get(interface)
        if idx is not None:
            mappings[interface] = idx
        else:
            # Fallback: numeric suffix from name
            if interface.startswith("Ethernet"):
                try:
                    mappings[interface] = int(interface[8:])
                except ValueError:
                    pass

    return mappings


def main():
    """Main discovery routine."""
    import argparse

    parser = argparse.ArgumentParser(description="Discover SONiC hardware")
    parser.add_argument("--linkdb-url", default="http://localhost:8000", help="Link Database URL")
    parser.add_argument("--vop-id", help="Virtual operator ID")
    parser.add_argument("--pop-id", help="POP ID")
    parser.add_argument("--router-id", help="Router ID")
    parser.add_argument("--output", choices=["json", "env"], default="env", help="Output format")

    args = parser.parse_args()

    print("Discovering SONiC hardware...", file=sys.stderr)

    # 1. Discover interfaces and panel indices
    discovery = discover_sonic_interfaces()
    all_eth_ifaces = discovery.get("ethernet_interfaces", [])
    panel_index = discovery.get("panel_index", {})

    # Restrict to candidate ports we care about (CMIS-capable line/client ports)
    ethernet_ifaces = [i for i in all_eth_ifaces if i in CANDIDATE_PORTS]

    print(f"Candidate Ethernet interfaces: {ethernet_ifaces}", file=sys.stderr)

    # 2. Check SFP/QSFP presence
    sfp_presence = discover_sfp_presence()
    present_interfaces = [iface for iface in ethernet_ifaces if sfp_presence.get(iface, False)]

    print(
        f"Found {len(present_interfaces)} candidate interfaces with SFPs present: {present_interfaces}",
        file=sys.stderr,
    )

    # 3. Filter by "availability": EEPROM readable (i.e., real transceiver there)
    available_interfaces: List[str] = []
    caps_cache: Dict[str, Dict] = {}

    for iface in present_interfaces:
        caps = discover_interface_capabilities(iface)
        caps_cache[iface] = caps
        if caps.get("has_eeprom"):
            available_interfaces.append(iface)

    print(
        f"Found {len(available_interfaces)} CMIS-available interfaces (EEPROM readable): {available_interfaces}",
        file=sys.stderr,
    )

    # 4. Interfaces from LinkDB (optional)
    assigned_interfaces: List[str] = []
    if args.vop_id and args.pop_id and args.router_id:
        assigned_interfaces = get_assigned_interfaces_from_linkdb(
            args.linkdb_url, args.vop_id, args.pop_id, args.router_id
        )
        print(
            f"LinkDB assigned {len(assigned_interfaces)} interfaces (before filtering): {assigned_interfaces}",
            file=sys.stderr,
        )

        # Restrict LinkDB interfaces to candidate + available set
        assigned_interfaces = [
            i for i in assigned_interfaces if i in available_interfaces
        ]
        print(
            f"LinkDB assigned {len(assigned_interfaces)} CMIS-available interfaces: {assigned_interfaces}",
            file=sys.stderr,
        )

    # 5. Decide final interface list for CMIS agent
    if assigned_interfaces:
        final_interfaces = assigned_interfaces
    else:
        final_interfaces = available_interfaces

    if not final_interfaces:
        print("WARNING: No CMIS-available interfaces detected.", file=sys.stderr)

    print(f"Final interfaces for CMIS agent: {final_interfaces}", file=sys.stderr)

    # 6. Build port mappings (for sonic_platform.get_sfp(port_num))
    port_mappings = create_port_mappings(final_interfaces, panel_index)

    # 7. Output
    if args.output == "json":
        result = {
            "assigned_transceivers": final_interfaces,
            "interface_mappings": port_mappings,
            "discovery_details": {
                "all_ethernet_interfaces": all_eth_ifaces,
                "candidate_ethernet_interfaces": ethernet_ifaces,
                "sfp_presence": sfp_presence,
                "available_interfaces": available_interfaces,
            },
        }
        print(json.dumps(result, indent=2))
    else:
        # env format: no extra braces
        print("ASSIGNED_TRANSCEIVERS=" + json.dumps(final_interfaces, separators=(",", ":")))
        print("IFNAME_TO_PORTNUM_JSON=" + json.dumps(port_mappings, separators=(",", ":")))

        # Helpful for human debugging (stderr)
        for interface in final_interfaces:
            caps = caps_cache.get(interface) or discover_interface_capabilities(interface)
            print(
                f"# {interface}: vendor={caps.get('vendor', 'unknown')}, "
                f"part={caps.get('part_number', 'unknown')}, "
                f"serial={caps.get('serial', 'unknown')}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    sys.exit(main())
