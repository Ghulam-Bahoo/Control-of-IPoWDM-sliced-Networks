#!/usr/bin/env python3
import sys
import os
import time
sys.path.insert(0, '.')

print("Testing Kafka Manager...")

try:
    from core.kafka_manager import KafkaManager
    
    # Initialize
    print("Initializing Kafka Manager...")
    km = KafkaManager()
    
    # Check health
    health = km.health_check()
    print(f"Kafka Health: {health}")
    
    # Start consumer
    print("Starting consumer...")
    km.start_consuming()
    
    # Send test heartbeat (if there's a listener)
    print("Sending test broadcast...")
    from core.agent_dispatcher import AgentDispatcher
    ad = AgentDispatcher(km)
    ad.broadcast_discovery()
    
    # Wait a bit
    time.sleep(3)
    
    # Stop
    print("Stopping...")
    km.stop_consuming()
    km.close()
    
    print("✅ Kafka test completed successfully")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
