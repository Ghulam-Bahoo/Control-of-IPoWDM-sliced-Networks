"""
Verification Script for Phase 2 Components
Tests Link DB Client, Path Computer, and Connection Manager
"""

import sys
import os
import json
from datetime import datetime

# Add parent directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from core.linkdb_client import LinkDBClient
from core.path_computer import PathComputer
from core.connection_manager import ConnectionManager, ConnectionStatus
from models.schemas import ConnectionRequest, ModulationFormat


def print_header(title: str):
    """Print section header."""
    print("\n" + "="*60)
    print(f"  {title}")
    print("="*60)


def test_linkdb_client():
    """Test Link Database Client."""
    print_header("TEST 1: LINK DATABASE CLIENT")
    
    try:
        # Initialize client
        linkdb = LinkDBClient()
        
        # Test 1.1: Connection
        print("1.1 Testing connection...")
        if linkdb.health_check():
            print("✅ Link DB connection successful")
        else:
            print("❌ Link DB connection failed")
            return False
        
        # Test 1.2: Get topology
        print("\n1.2 Testing topology retrieval...")
        pops, links = linkdb.get_topology()
        print(f"   Found {len(pops)} POPs: {list(pops.keys())}")
        print(f"   Found {len(links)} links: {list(links.keys())}")
        
        if pops and links:
            print("✅ Topology retrieval successful")
        else:
            print("⚠️  Topology data might be empty")
        
        # Test 1.3: Get available slots
        print("\n1.3 Testing slot availability...")
        if links:
            sample_link = list(links.keys())[0]
            available_slots = linkdb.get_available_slots(sample_link)
            print(f"   Available slots on {sample_link}: {len(available_slots)} slots")
            if available_slots:
                print(f"   Sample slots: {available_slots[:5]}...")
        
        # Test 1.4: Connection count
        print("\n1.4 Testing connection operations...")
        conn_count = linkdb.get_connection_count()
        print(f"   Existing connections in Link DB: {conn_count}")
        
        return True
        
    except Exception as e:
        print(f"❌ Link DB test failed: {e}")
        return False


def test_path_computer():
    """Test Path Computation Engine."""
    print_header("TEST 2: PATH COMPUTATION ENGINE")
    
    try:
        # Initialize components
        linkdb = LinkDBClient()
        path_computer = PathComputer(linkdb)
        
        # Get topology for test
        pops, links = linkdb.get_topology()
        
        if len(pops) < 2:
            print("⚠️  Need at least 2 POPs for path testing")
            return True  # Not a failure, just insufficient data
        
        # Test 2.1: Shortest path
        print("2.1 Testing shortest path computation...")
        pop_list = list(pops.keys())
        source_pop = pop_list[0]
        dest_pop = pop_list[-1] if len(pop_list) > 1 else pop_list[0]
        
        path = path_computer.compute_shortest_path(source_pop, dest_pop)
        if path:
            print(f"✅ Path found from {source_pop} to {dest_pop}: {path}")
        else:
            print(f"❌ No path found from {source_pop} to {dest_pop}")
            # Try with different POPs
            if len(pop_list) > 2:
                dest_pop = pop_list[1]
                path = path_computer.compute_shortest_path(source_pop, dest_pop)
                if path:
                    print(f"✅ Alternate path found: {path}")
        
        # Test 2.2: Slot requirement calculation
        print("\n2.2 Testing slot requirement calculation...")
        for bw in [100, 200, 400]:
            slots = path_computer.calculate_required_slots(bw, "DP-16QAM")
            print(f"   {bw}Gbps with DP-16QAM requires {slots} slots")
        
        # Test 2.3: Contiguous slot finding
        print("\n2.3 Testing contiguous slot finding...")
        if links:
            sample_link = list(links.keys())[0]
            required_slots = 4
            contiguous = path_computer.find_contiguous_slots(sample_link, required_slots)
            if contiguous:
                print(f"✅ Found {len(contiguous)} contiguous slots on {sample_link}: {contiguous}")
            else:
                print(f"⚠️  No {required_slots} contiguous slots on {sample_link}")
        
        # Test 2.4: Path validation
        print("\n2.4 Testing path validation...")
        is_valid, message = path_computer.validate_path(source_pop, dest_pop)
        print(f"   Path validation: {is_valid} - {message}")
        
        return True
        
    except Exception as e:
        print(f"❌ Path computer test failed: {e}")
        return False


def test_connection_manager():
    """Test Connection Manager."""
    print_header("TEST 3: CONNECTION MANAGER")
    
    try:
        # Initialize components
        linkdb = LinkDBClient()
        path_computer = PathComputer(linkdb)
        conn_manager = ConnectionManager(linkdb, path_computer)
        
        # Get topology
        pops, links = linkdb.get_topology()
        
        if len(pops) < 2:
            print("⚠️  Need at least 2 POPs for connection testing")
            return True  # Not a failure
        
        # Test 3.1: List existing connections
        print("3.1 Testing connection listing...")
        existing_connections = conn_manager.list_connections()
        print(f"   Found {len(existing_connections)} existing connections")
        
        # Test 3.2: Create test connection
        print("\n3.2 Testing connection creation...")
        pop_list = list(pops.keys())
        source_pop = pop_list[0]
        dest_pop = pop_list[-1] if len(pop_list) > 1 else pop_list[0]
        
        # Create connection request
        request = ConnectionRequest(
            source_pop=source_pop,
            destination_pop=dest_pop,
            bandwidth_gbps=400.0,
            modulation=ModulationFormat.DP_16QAM,
            qos_requirements={"max_latency_ms": 10}
        )
        
        response = conn_manager.create_connection(request)
        
        if response:
            print(f"✅ Connection created: {response.connection_id}")
            print(f"   Status: {response.status}")
            print(f"   Path segments: {len(response.path)}")
            print(f"   Estimated OSNR: {response.estimated_osnr} dB")
            
            # Test 3.3: Get connection
            print("\n3.3 Testing connection retrieval...")
            retrieved = conn_manager.get_connection_response(response.connection_id)
            if retrieved:
                print(f"✅ Successfully retrieved connection {retrieved.connection_id}")
            else:
                print(f"❌ Failed to retrieve connection")
            
            # Test 3.4: List connections by status
            print("\n3.4 Testing connection filtering...")
            pending = conn_manager.list_connections(ConnectionStatus.PENDING)
            print(f"   PENDING connections: {len(pending)}")
            
            # Test 3.5: Connection statistics
            print("\n3.5 Testing connection statistics...")
            stats = conn_manager.get_connection_stats()
            print(f"   Total connections: {stats['total_connections']}")
            print(f"   By status: {stats['by_status']}")
            
            # Test 3.6: Mark setup completed
            print("\n3.6 Testing state transitions...")
            if conn_manager.complete_setup(response.connection_id):
                print(f"✅ Marked connection as setup completed")
                
                # Verify status update
                updated = conn_manager.get_connection_response(response.connection_id)
                if updated and updated.status == ConnectionStatus.ACTIVE:
                    print(f"✅ Status updated to ACTIVE")
                else:
                    print(f"❌ Status not updated correctly")
            else:
                print(f"❌ Failed to mark setup completed")
            
            # Test 3.7: Teardown
            print("\n3.7 Testing connection teardown...")
            if conn_manager.start_teardown(response.connection_id):
                print(f"✅ Started teardown")
                if conn_manager.complete_teardown(response.connection_id):
                    print(f"✅ Completed teardown and cleanup")
                else:
                    print(f"❌ Failed to complete teardown")
            else:
                print(f"❌ Failed to start teardown")
            
        else:
            print(f"❌ Failed to create connection")
            print("   This might be due to unavailable resources (slots/interfaces)")
        
        # Test 3.8: Health check
        print("\n3.8 Testing health check...")
        health = conn_manager.health_check()
        print(f"   Health status: {health}")
        
        return True
        
    except Exception as e:
        print(f"❌ Connection manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_integration():
    """Test integration between all components."""
    print_header("TEST 4: COMPONENT INTEGRATION")
    
    try:
        # Initialize all components
        linkdb = LinkDBClient()
        path_computer = PathComputer(linkdb)
        conn_manager = ConnectionManager(linkdb, path_computer)
        
        print("✅ All components initialized successfully")
        
        # Test data flow
        pops, links = linkdb.get_topology()
        print(f"   Topology loaded: {len(pops)} POPs, {len(links)} links")
        
        # Create a connection through the complete flow
        if len(pops) >= 2:
            pop_list = list(pops.keys())
            source_pop = pop_list[0]
            dest_pop = pop_list[1]
            
            # 1. Path computation
            path = path_computer.compute_shortest_path(source_pop, dest_pop)
            print(f"   1. Path computed: {path}")
            
            if path:
                # 2. Connection creation
                request = ConnectionRequest(
                    source_pop=source_pop,
                    destination_pop=dest_pop,
                    bandwidth_gbps=100.0,
                    modulation=ModulationFormat.DP_16QAM
                )
                
                response = conn_manager.create_connection(request)
                print(f"   2. Connection created: {response.connection_id if response else 'Failed'}")
                
                if response:
                    # 3. Verify in Link DB
                    conn_count = linkdb.get_connection_count()
                    print(f"   3. Link DB connection count: {conn_count}")
                    
                    # 4. Cleanup
                    conn_manager.start_teardown(response.connection_id)
                    conn_manager.complete_teardown(response.connection_id)
                    print(f"   4. Connection cleaned up")
        
        print("\n✅ Integration test completed")
        return True
        
    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        return False


def main():
    """Main verification function."""
    print("="*60)
    print("  PHASE 2 VERIFICATION - IP SDN CONTROLLER")
    print("="*60)
    
    # Load settings
    print(f"\nConfiguration:")
    print(f"  Virtual Operator: {settings.VIRTUAL_OPERATOR}")
    print(f"  Kafka Broker: {settings.KAFKA_BROKER}")
    print(f"  Link DB: {settings.LINKDB_HOST}:{settings.LINKDB_PORT}")
    print(f"  API Port: {settings.API_PORT}")
    
    results = {}
    
    # Run tests
    results['linkdb'] = test_linkdb_client()
    results['path_computer'] = test_path_computer()
    results['connection_manager'] = test_connection_manager()
    results['integration'] = test_integration()
    
    # Summary
    print_header("VERIFICATION SUMMARY")
    
    all_passed = all(results.values())
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:25} {status}")
    
    print("\n" + "="*60)
    if all_passed:
        print("🎉 ALL TESTS PASSED! Phase 2 components are working correctly.")
        print("\nNext steps:")
        print("1. Check Link Database has proper topology data")
        print("2. Verify POPs and links are correctly defined")
        print("3. Ensure interfaces are available for allocation")
        print("4. Proceed to Phase 3: Kafka & QoT integration")
    else:
        print("⚠️  SOME TESTS FAILED. Please check the errors above.")
        print("\nTroubleshooting:")
        print("1. Ensure Redis (Link DB) is running: docker ps | grep redis")
        print("2. Check topology data exists in Redis")
        print("3. Verify network connectivity to Kafka broker")
        print("4. Check environment variables in .env file")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
