"""D03 discovery + registry persistence (in-memory session)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agentforge.discovery.registry import (
    DiscoveryRegistryError,
    create_profile,
    register_installation,
)
from agentforge.discovery.service import AgentDiscoveryService, DiscoveredInstallation
from agentforge.storage.models import (
    AgentProductRow,
    AgentProfileRow,
    CapabilityCheckRow,
)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, str], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        ident = getattr(row, "id", None)
        if ident is not None:
            self.store[(type(row), str(ident))] = row

    def flush(self) -> None:
        now = datetime.now(UTC)
        for row in self.added:
            if hasattr(row, "created_at") and getattr(row, "created_at", None) is None:
                row.created_at = now

    def get(self, model: type, key: str) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        _ = stmt
        # Mem harness does not interpret SQLAlchemy WHERE; tests register once.
        return None


def test_discover_passive_with_mocked_adapter() -> None:
    fake_item = {
        "productId": "cursor",
        "productVersion": "unknown",
        "environmentId": "windows_native",
        "locationRef": "C:/fake/cursor.exe",
        "authStatus": "unknown",
        "notes": ["which:cursor", "no_session_started"],
    }

    class _Fake:
        adapter_id = "cursor_agent"

        def discover(self, environment: str) -> list[dict[str, Any]]:
            assert environment == "windows_native"
            return [fake_item]

    with patch("agentforge.discovery.service.list_adapter_ids", return_value=["cursor_agent"]):
        with patch("agentforge.discovery.service.get_adapter", return_value=_Fake()):
            found = AgentDiscoveryService().discover()
    assert len(found) == 1
    assert found[0].product_id == "cursor"
    assert found[0].adapter_id == "cursor_agent"
    assert "no_session_started" in found[0].notes
    payload = found[0].to_dict()
    assert payload["productId"] == "cursor"
    assert "product_id" not in payload


def test_register_and_profile_manual_assisted(tmp_path: Path) -> None:
    exe = tmp_path / "cursor.exe"
    exe.write_text("x", encoding="utf-8")
    session = _MemSession()
    item = DiscoveredInstallation(
        product_id="cursor",
        product_version="unknown",
        environment_id="windows_native",
        location_ref=str(exe),
        auth_status="unknown",
        adapter_id="cursor_agent",
        notes=["manual_registration"],
    )
    inst = register_installation(session, item)  # type: ignore[arg-type]
    assert inst.installation_id.startswith("inst_")
    products = [r for r in session.added if isinstance(r, AgentProductRow)]
    assert len(products) == 1

    profile, check = create_profile(
        session,  # type: ignore[arg-type]
        installation_id=inst.installation_id,
        adapter_id="cursor_agent",
    )
    assert profile.access_level == "manual_assisted"
    assert profile.auth_status == "unknown"
    assert check.profile_id == profile.profile_id
    supports = {r.capability: r.support for r in check.results}
    assert supports["approvalRoundtrip"] == "unsupported"
    assert supports["createSession"] == "unknown"
    assert any(isinstance(r, AgentProfileRow) for r in session.added)
    assert any(isinstance(r, CapabilityCheckRow) for r in session.added)


def test_profile_missing_installation() -> None:
    session = _MemSession()
    with pytest.raises(DiscoveryRegistryError) as exc:
        create_profile(session, installation_id="inst_missing", adapter_id="cursor_agent")  # type: ignore[arg-type]
    assert exc.value.code == "installation_not_found"


def test_profile_unknown_adapter(tmp_path: Path) -> None:
    exe = tmp_path / "bin.exe"
    exe.write_text("x", encoding="utf-8")
    session = _MemSession()
    inst = register_installation(
        session,  # type: ignore[arg-type]
        DiscoveredInstallation(
            product_id="cursor",
            product_version="unknown",
            environment_id="windows_native",
            location_ref=str(exe),
        ),
    )
    with pytest.raises(DiscoveryRegistryError) as exc:
        create_profile(
            session,  # type: ignore[arg-type]
            installation_id=inst.installation_id,
            adapter_id="not_a_real_adapter",
        )
    assert exc.value.code == "adapter_not_found"
