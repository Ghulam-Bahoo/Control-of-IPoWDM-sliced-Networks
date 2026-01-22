#!/usr/bin/env python3
"""
Seed slot data for links in Redis
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.linkdb_client import LinkDBClient

def seed_slots():
    """Seed slot data for all links."""
    linkdb = LinkDBClient()
    
    # Get all links
    link_keys = linkdb._client.smembers("links") or []
    
    for link_id in link_keys:
        slot_key = f"slots:{link_id}"
        
        # Check if slots already exist
        if not linkdb._client.exists(slot_key):
            # Create slots 0-319 (320 slots total)
            for slot in range(320):
                linkdb._client.sadd(slot_key, slot)
            print(f"Created slots for {link_id}")
        else:
            print(f"Slots already exist for {link_id}")
    
    print(f"Seeded slots for {len(link_keys)} links")

if __name__ == "__main__":
    seed_slots()
