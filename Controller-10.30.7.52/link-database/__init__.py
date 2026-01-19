# link_database/__init__.py
"""
IPoWDM Link Database Module

This module provides the Link Database service for storing interconnection
details and spectrum occupation in IPoWDM networks.

Key components:
- Database models (POP, Router, Interface, etc.)
- First-fit frequency allocation algorithm
- REST API for topology management
- Kafka integration for real-time updates
"""

# Import key classes for easier access
from .schema import (
    POP,
    Router,
    Interface,
    Transceiver,
    OpticalLink,
    FrequencySlot,
    Connection,
    VirtualOperator,
    ConnectionStatus,
    FrequencySlotStatus
)

from .first_fit import FirstFitAllocator
from .main import app  # FastAPI application

# Version information
__version__ = "1.0.0"
__author__ = "IPoWDM Development Team"
__description__ = "Link Database for IPoWDM optical networks"

# Define what gets imported with "from link_database import *"
__all__ = [
    # Models
    "POP",
    "Router", 
    "Interface",
    "Transceiver",
    "OpticalLink",
    "FrequencySlot",
    "Connection",
    "VirtualOperator",
    "ConnectionStatus",
    "FrequencySlotStatus",
    
    # Core classes
    "FirstFitAllocator",
    "app",
    
    # Metadata
    "__version__",
    "__author__",
    "__description__"
]