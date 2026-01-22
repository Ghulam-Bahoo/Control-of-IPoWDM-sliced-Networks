#!/usr/bin/env python3
"""
Hardware discovery script for SONiC Agent (AS9726-friendly)

What it outputs (env format):
  ASSIGNED_TRANSCEIVERS=[...]
  IFNAME_TO_PORTNUM_JSON={...}

IMPORTANT:
  IFNAME_TO_PORTNUM_JSON values must be SONiC platform port indices (Port#),
  e.g., Ethernet192 -> 25 (because alias shows Eth25(Port25)).
  Do NOT use Ethernet suffix (192) for sonic_platform.get_sfp().
"""

import sys
import json
import subprocess
from typing import Dict, List, Optional
import re


# Option B: map all 9 interfaces (including mgmt SFP ports)
CANDIDATE_PORTS = [
    "Ethernet0",
    "Ethernet64",
    "Ethernet96",
    "Ethernet120",
    "Ethernet128",
    "Ethernet160",
    "Ethernet192",
    "Ethernet256",
    "Ethernet257",
]


def _run(cmd: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def parse_port_index_from_status() -> Dict[str, int]:
    """
    Parse `show interfaces status` and extract Port# from Alias column.

    Example alias: "Eth25(Port25)" -> 25
    """
    mapping: Dict[str, int] = {}

    try:
        r = _run(["show", "interfaces", "status"], timeout=10)
        if r.returncode != 0:
            return mapping

        for line in r.stdout.splitlines():
            line = line.strip()
            if not line.startswith("Ethernet"):
                continue

            # We only need interface name + alias
            # Format is columnar, but stable enough to regex "Ethernet###" and "Port(\d+)"
            m_if = re.match(r"^(Ethernet\d+)\s+", line)
            if not m_if:
                continue
            ifname = m_if.group(1)

            m_port = re.search(r"\(Port(\d+)\)", line)
            if m_port:
                mapping[ifname] = int(m_port.group(1))

        return mapping
    except Exception:
        return mapping


def discover_sfp_presence() -> Dict[str, bool]:
    """Discover which interfaces have SFPs / QSFPs present."""
    presence: Dict[str, bool] = {}
    try:
        r = _run(["show", "interfaces", "transceiver", "presence"], timeout=10)
        if r.returncode != 0:
            return presence

        for line in r.stdout.splitlines():
            line = line.strip()
            if not line.startswith("Ethernet"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                iface = parts[0]
                present = parts[1].lower() == "present"
                presence[iface] = present
    except Exception:
        pass
    return presence


def eeprom_readable(interface: str) -> bool:
    """
    Treat EEPROM readability as "usable transceiver is present".
    """
    try:
        r = _run(["show", "interfaces", "transceiver", "eeprom", interface], timeout=10)
        return r.returncode == 0 and "EEPROM" in r.stdout
    except Exception:
        return False


def main() -> int:
    # 1) Build correct platform mapping from show interfaces status (Port#)
    status_map = parse_port_index_from_status()

    # 2) Presence detection
    presence = discover_sfp_presence()

    # 3) Candidate filtering
    candidates = [p for p in CANDIDATE_PORTS if p in status_map]
    present = [p for p in candidates if presence.get(p, False)]

    # 4) EEPROM readability filtering
    available: List[str] = [p for p in present if eeprom_readable(p)]

    # 5) Final list (Option B expects all 9 if they are present/readable)
    final = available

    # 6) Port mappings (only for final)
    mappings = {p: status_map[p] for p in final if p in status_map}

    # Output env-friendly JSON
    print("ASSIGNED_TRANSCEIVERS=" + json.dumps(final, separators=(",", ":")))
    print("IFNAME_TO_PORTNUM_JSON=" + json.dumps(mappings, separators=(",", ":")))

    # stderr debug
    print(f"# candidates={candidates}", file=sys.stderr)
    print(f"# present={present}", file=sys.stderr)
    print(f"# final={final}", file=sys.stderr)
    print(f"# mappings={mappings}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

