"""Passive discovery of known install locations / CLI / WSL / manual registrations.

Does not steal credentials, attach private sessions, or mark installs as auto-ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agentforge.adapters.registry import get_adapter, list_adapter_ids


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class DiscoveredInstallation:
    product_id: str
    product_version: str
    environment_id: str
    location_ref: str
    auth_status: str = "unknown"
    adapter_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "productId": self.product_id,
            "productVersion": self.product_version,
            "environmentId": self.environment_id,
            "locationRef": self.location_ref,
            "authStatus": self.auth_status,
            "adapterId": self.adapter_id,
            "notes": list(self.notes),
        }


class AgentDiscoveryService:
    """Passive scanner — concrete control evidence stays unknown until validated."""

    def discover(
        self,
        *,
        environments: list[str] | None = None,
        manual_registrations: list[dict[str, Any]] | None = None,
        adapter_ids: list[str] | None = None,
    ) -> list[DiscoveredInstallation]:
        found: list[DiscoveredInstallation] = []
        envs = environments or ["windows_native"]
        adapters = adapter_ids or list_adapter_ids()

        for adapter_id in adapters:
            adapter = get_adapter(adapter_id)
            for env in envs:
                for item in adapter.discover(env):
                    found.append(
                        DiscoveredInstallation(
                            product_id=str(item["productId"]),
                            product_version=str(item.get("productVersion", "unknown")),
                            environment_id=str(item.get("environmentId", env)),
                            location_ref=str(item["locationRef"]),
                            auth_status=str(item.get("authStatus", "unknown")),
                            adapter_id=adapter_id,
                            notes=list(item.get("notes") or []) + [f"discoveredAt={_iso_now()}"],
                        )
                    )

        for item in manual_registrations or []:
            found.append(
                DiscoveredInstallation(
                    product_id=str(item["productId"]),
                    product_version=str(item.get("productVersion", "unknown")),
                    environment_id=str(item.get("environmentId", "windows_native")),
                    location_ref=str(item["locationRef"]),
                    auth_status=str(item.get("authStatus", "unknown")),
                    adapter_id=str(item["adapterId"]) if item.get("adapterId") else None,
                    notes=["manual_registration", f"discoveredAt={_iso_now()}"],
                )
            )

        # Deduplicate by (product, location, env)
        unique: dict[tuple[str, str, str], DiscoveredInstallation] = {}
        for row in found:
            key = (row.product_id, row.location_ref, row.environment_id)
            unique[key] = row
        return list(unique.values())
