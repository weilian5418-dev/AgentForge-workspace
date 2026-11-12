"""D03 discovery/profile contract shapes."""

from __future__ import annotations

from datetime import UTC, datetime

from agentforge.contracts.agents import (
    AgentProfile,
    CapabilityCheck,
    CapabilityResult,
    DiscoveredInstallationView,
    DiscoveryRunRequest,
    DiscoveryRunResponse,
    ManualInstallationRegisterRequest,
    ProfileCreateRequest,
    ProfileValidateResponse,
)


def test_discovery_request_defaults() -> None:
    body = DiscoveryRunRequest.model_validate({})
    assert body.environments == ["windows_native"]
    assert body.persist is True


def test_discovery_response_note() -> None:
    view = DiscoveredInstallationView(
        product_id="cursor",
        product_version="unknown",
        environment_id="windows_native",
        location_ref="C:/cursor",
    )
    resp = DiscoveryRunResponse(items=[view])
    assert "schedulability" in resp.note.lower() or "Discovery" in resp.note


def test_manual_register_and_profile_roundtrip() -> None:
    ManualInstallationRegisterRequest.model_validate(
        {
            "productId": "cursor",
            "locationRef": "C:/Programs/cursor",
            "adapterId": "cursor_agent",
        }
    )
    ProfileCreateRequest.model_validate({"installationId": "inst_abc", "adapterId": "cursor_agent"})
    now = datetime.now(UTC)
    profile = AgentProfile(
        profile_id="aprof_1",
        installation_id="inst_1",
        adapter_id="cursor_agent",
        adapter_version="0.1.0",
        config_version="1",
        config_hash="a" * 64,
        auth_status="unknown",
        access_level="manual_assisted",
    )
    check = CapabilityCheck(
        check_id="achk_1",
        profile_id="aprof_1",
        product_version="unknown",
        adapter_id="cursor_agent",
        adapter_version="0.1.0",
        environment_id="windows_native",
        checked_at=now,
        results=[
            CapabilityResult(capability="createSession", support="unknown"),
            CapabilityResult(capability="approvalRoundtrip", support="unsupported"),
        ],
    )
    out = ProfileValidateResponse(
        profile=profile, capability_check=check, access_level="manual_assisted"
    )
    dumped = out.model_dump(mode="json", by_alias=True)
    assert dumped["accessLevel"] == "manual_assisted"
    assert "approvalRoundtrip" in dumped["note"]
