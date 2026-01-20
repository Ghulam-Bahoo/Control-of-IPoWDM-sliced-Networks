#!/usr/bin/env python3
"""
Test script for CMIS driver on SONiC switch
"""

import os
import sys
import time
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings
from core.cmis_driver import CMISDriver


def test_cmis_driver():
    """Test CMIS driver functionality."""
    print("Testing CMIS Driver on SONiC Switch")
    print("=" * 50)
    
    # Create driver
    driver = CMISDriver(
        interface_mappings=settings.interface_mappings,
        mock_mode=settings.MOCK_HARDWARE
    )
    
    print(f"Interface mappings: {settings.interface_mappings}")
    print(f"Mock mode: {settings.MOCK_HARDWARE}")
    print()
    
    # Test 1: Health check
    print("1. Health Check:")
    healthy = driver.check_health()
    print(f"   Overall health: {'✓ Healthy' if healthy else '✗ Unhealthy'}")
    print()
    
    # Test 2: Interface status
    print("2. Interface Status:")
    for interface in settings.ASSIGNED_TRANSCEIVERS:
        status = driver.get_interface_status(interface)
        print(f"   {interface}:")
        print(f"     Present: {status.get('present')}")
        print(f"     Operational: {status.get('operational')}")
        print(f"     Vendor: {status.get('vendor')}")
        print(f"     Module State: {status.get('module_state')}")
        if status.get('frequency_mhz'):
            print(f"     Frequency: {status.get('frequency_mhz')} MHz")
        if status.get('tx_power_dbm'):
            print(f"     TX Power: {status.get('tx_power_dbm')} dBm")
        print()
    
    # Test 3: Capabilities
    print("3. Interface Capabilities:")
    for interface in settings.ASSIGNED_TRANSCEIVERS:
        caps = driver.get_capabilities(interface)
        print(f"   {interface}:")
        print(f"     Type: {caps.get('type')}")
        print(f"     Frequency Range: {caps.get('frequency_range')} MHz")
        print(f"     TX Power Range: {caps.get('tx_power_range')} dBm")
        print(f"     Application Codes: {len(caps.get('app_code', {}))}")
        print()
    
    # Test 4: Telemetry reading
    print("4. Telemetry Reading:")
    for interface in settings.ASSIGNED_TRANSCEIVERS:
        readings = driver.read_telemetry(interface)
        print(f"   {interface}:")
        print(f"     TX Power: {readings.tx_power_dbm} dBm")
        print(f"     RX Power: {readings.rx_power_dbm} dBm")
        print(f"     OSNR: {readings.osnr_db} dB")
        print(f"     Temperature: {readings.temperature_c} °C")
        print(f"     Module State: {readings.module_state}")
        print()
    
    print("=" * 50)
    print("CMIS Driver Test Complete")
    
    return healthy


if __name__ == "__main__":
    try:
        success = test_cmis_driver()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
