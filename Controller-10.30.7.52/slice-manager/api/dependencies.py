"""
Module: API Dependencies
Description: FastAPI dependency injection for clean architecture
Author: AI Developer
Date: 2024
"""

from core.slice_orchestrator import slice_orchestrator


def get_slice_orchestrator():
    """Dependency to get the slice orchestrator instance."""
    return slice_orchestrator