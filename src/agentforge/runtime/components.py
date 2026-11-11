"""Component ownership catalog and lifecycle adapter interfaces (BE-03)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Ownership = Literal["external", "managed"]
ComponentState = Literal[
    "not_implemented",
    "stopped",
    "starting",
    "running",
    "draining",
    "unhealthy",
    "external",
]


@dataclass(frozen=True)
class ComponentSpec:
    id: str
    ownership: Ownership
    required_in_modes: tuple[str, ...]
    description: str


class LifecycleAdapter(Protocol):
    spec: ComponentSpec

    def status(self) -> ComponentState: ...

    def start(self) -> None: ...

    def drain(self) -> None: ...

    def stop(self, *, force: bool = False) -> None: ...


COMPONENT_CATALOG: tuple[ComponentSpec, ...] = (
    ComponentSpec(
        id="api",
        ownership="managed",
        required_in_modes=("dev", "local", "hosted"),
        description="FastAPI control plane",
    ),
    ComponentSpec(
        id="postgres",
        ownership="external",
        required_in_modes=("dev", "local", "hosted"),
        description="Existing PostgreSQL (never stopped by AgentForge)",
    ),
    ComponentSpec(
        id="graph-driver",
        ownership="managed",
        required_in_modes=("local", "hosted"),
        description="LangGraph driver — BE-14",
    ),
    ComponentSpec(
        id="queue-worker",
        ownership="managed",
        required_in_modes=("local", "hosted"),
        description="Procrastinate workers — BE-20",
    ),
    ComponentSpec(
        id="runner",
        ownership="managed",
        required_in_modes=("local", "hosted"),
        description="Execution runner — BE-21",
    ),
    ComponentSpec(
        id="adapter-host",
        ownership="managed",
        required_in_modes=("local", "hosted"),
        description="Trusted Adapter Host for external Agent sessions — BE-25",
    ),
)


class NotImplementedAdapter:
    """Placeholder until BE-14/20/21 provide real adapters."""

    def __init__(self, spec: ComponentSpec) -> None:
        self.spec = spec

    def status(self) -> ComponentState:
        return "external" if self.spec.ownership == "external" else "not_implemented"

    def start(self) -> None:
        raise RuntimeError(f"Component {self.spec.id} is not implemented yet")

    def drain(self) -> None:
        return None

    def stop(self, *, force: bool = False) -> None:
        return None


def component_status_snapshot(mode: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for spec in COMPONENT_CATALOG:
        if mode == "dev" and spec.id in {
            "graph-driver",
            "queue-worker",
            "runner",
            "adapter-host",
        }:
            state = "not_implemented"
        elif spec.ownership == "external":
            state = "external"
        elif spec.id == "api":
            state = "managed"
        else:
            state = "not_implemented"
        rows.append(
            {
                "id": spec.id,
                "ownership": spec.ownership,
                "state": state,
                "description": spec.description,
            }
        )
    return rows
