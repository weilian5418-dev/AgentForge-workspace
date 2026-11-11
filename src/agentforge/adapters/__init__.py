"""External Agent product adapters."""

from agentforge.adapters.base import AdapterCapabilityMatrix, AgentAdapter
from agentforge.adapters.registry import get_adapter, list_adapter_ids

__all__ = [
    "AdapterCapabilityMatrix",
    "AgentAdapter",
    "get_adapter",
    "list_adapter_ids",
]
