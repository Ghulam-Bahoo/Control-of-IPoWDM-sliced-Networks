#!/usr/bin/env python3
"""
Health check script for SONiC Agent
"""

import os
import sys
import time
import json
import socket
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings


def check_kafka() -> bool:
    """Check Kafka connection."""
    try:
        from kafka import KafkaProducer
        producer = KafkaProducer(
            bootstrap_servers=settings.KAFKA_BROKER,
            request_timeout_ms=10000
        )
        producer.flush(timeout=5)
        producer.close()
        return True
    except Exception as e:
        print(f"Kafka check failed: {e}")
        return False


def check_hardware() -> bool:
    """Check hardware access."""
    try:
        from core.cmis_driver import CMISDriver
        driver = CMISDriver(settings.interface_mappings, mock_mode=settings.MOCK_HARDWARE)
        return driver.check_health()
    except Exception as e:
        print(f"Hardware check failed: {e}")
        return False


def check_network() -> bool:
    """Check network connectivity."""
    try:
        # Parse Kafka broker
        broker = settings.KAFKA_BROKER
        if ':' in broker:
            host, port = broker.split(':', 1)
            port = int(port)
        else:
            host = broker
            port = 9092
        
        # Test TCP connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception as e:
        print(f"Network check failed: {e}")
        return False


def main():
    """Main health check."""
    checks = [
        ("Kafka Connection", check_kafka),
        ("Hardware Access", check_hardware),
        ("Network Connectivity", check_network),
    ]
    
    results = []
    all_healthy = True
    
    for name, check_func in checks:
        try:
            healthy = check_func()
            results.append({"check": name, "healthy": healthy})
            
            if healthy:
                print(f"✓ {name}: OK")
            else:
                print(f"✗ {name}: FAILED")
                all_healthy = False
                
        except Exception as e:
            results.append({"check": name, "healthy": False, "error": str(e)})
            print(f"✗ {name}: ERROR - {e}")
            all_healthy = False
    
    # Output JSON for Docker health check
    health_status = {
        "timestamp": time.time(),
        "agent_id": settings.AGENT_ID,
        "healthy": all_healthy,
        "checks": results
    }
    
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps(health_status, indent=2))
    
    sys.exit(0 if all_healthy else 1)


if __name__ == "__main__":
    main()
