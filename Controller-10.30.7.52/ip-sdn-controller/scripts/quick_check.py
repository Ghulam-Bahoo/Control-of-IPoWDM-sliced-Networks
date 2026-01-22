import sys
import os

# Add parent directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.linkdb_client import LinkDBClient
from core.path_computer import PathComputer
from core.connection_manager import ConnectionManager

def quick_check():
    print("🔍 Quick Check - Phase 2 Components")
    print("-" * 40)
    
    try:
        # 1. Link DB
        print("1. Testing Link DB connection...")
        linkdb = LinkDBClient()
        if linkdb.health_check():
            pops, links = linkdb.get_topology()
            print(f"   ✅ Connected. Topology: {len(pops)} POPs, {len(links)} links")
            
            # Show sample data
            if pops:
                print(f"   Sample POPs: {list(pops.keys())[:3]}")
            if links:
                print(f"   Sample links: {list(links.keys())[:3]}")
        else:
            print("   ❌ Link DB connection failed")
            return False
        
        # 2. Path Computer
        print("\n2. Testing Path Computer...")
        pc = PathComputer(linkdb)
        if len(pops) >= 2:
            pop_list = list(pops.keys())
            path = pc.compute_shortest_path(pop_list[0], pop_list[-1])
            print(f"   ✅ Initialized. Sample path: {path if path else 'No path'}")
        else:
            print("   ✅ Initialized (insufficient POPs for path test)")
        
        # 3. Connection Manager
        print("\n3. Testing Connection Manager...")
        cm = ConnectionManager(linkdb, pc)
        connections = cm.list_connections()
        print(f"   ✅ Initialized. Found {len(connections)} existing connections")
        
        # 4. Health
        print("\n4. Overall Health...")
        cm_health = cm.health_check()
        print(f"   Connection Manager: {cm_health['status']}")
        print(f"   Active connections: {cm_health['connections_count']}")
        
        print("\n🎉 All components initialized successfully!")
        return True
        
    except Exception as e:
        print(f"\n❌ Error during quick check: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    if quick_check():
        print("\n✅ Phase 2 components are ready!")
        sys.exit(0)
    else:
        print("\n❌ Issues detected. Please check above errors.")
        sys.exit(1)
