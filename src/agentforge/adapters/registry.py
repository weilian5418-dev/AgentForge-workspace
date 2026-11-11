"""Known agent product adapters (D03)."""

from __future__ import annotations

from typing import Any

from agentforge.adapters.cli_agent import CliCodingAgentAdapter
from agentforge.adapters.cursor_agent import CursorAgentAdapter

_ADAPTERS: dict[str, Any] = {
    CursorAgentAdapter.adapter_id: CursorAgentAdapter,
    CliCodingAgentAdapter.adapter_id: CliCodingAgentAdapter,
}


def list_adapter_ids() -> list[str]:
    return sorted(_ADAPTERS)


def get_adapter(adapter_id: str) -> Any:
    cls = _ADAPTERS.get(adapter_id)
    if cls is None:
        raise KeyError(f"Unknown adapterId: {adapter_id}")
    return cls()
