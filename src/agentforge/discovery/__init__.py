"""Local Agent product discovery (BE-24). Discovery ≠ schedulable."""

from agentforge.discovery.registry import (
    DiscoveryRegistryError,
    create_profile,
    register_installation,
)
from agentforge.discovery.service import AgentDiscoveryService, DiscoveredInstallation

__all__ = [
    "AgentDiscoveryService",
    "DiscoveredInstallation",
    "DiscoveryRegistryError",
    "create_profile",
    "register_installation",
]
