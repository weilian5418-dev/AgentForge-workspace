"""Read-only query services for consistent snapshots and inventories (D12)."""

from agentforge.queries.agents import (
    build_agent_summary,
    explain_profile_schedulability,
    summarize_agent_counts,
    summarize_progress,
)
from agentforge.queries.authorization import AuthenticatedPrincipal, api_principal
from agentforge.queries.details import list_run_details
from agentforge.queries.snapshots import load_run_snapshot

__all__ = [
    "AuthenticatedPrincipal",
    "api_principal",
    "build_agent_summary",
    "explain_profile_schedulability",
    "list_run_details",
    "load_run_snapshot",
    "summarize_agent_counts",
    "summarize_progress",
]
